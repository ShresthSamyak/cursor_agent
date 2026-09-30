"""Localhost WebSocket bridge: one pipe for the extensions, the overlay and speech (PDF p. 14).

    python -m trail bridge            # random token, printed and written to %LOCALAPPDATA%/Trail/bridge.token
    python -m trail bridge --dev      # fixed token "trail-dev" for development

The desktop runtime behind it is the same Runtime the kit scores (docs/bridge-protocol.md).
Also serves the demo pages, the corpus and the browser build of the overlay over HTTP.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from ..core.bus import Event, EventType, Output
from ..core.config import RuntimeConfig
from ..core.runtime import Runtime
from .tools import CORPUS_PATH, MANIFEST, DesktopTools

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
OVERLAY = ROOT / "overlay" / "renderer"
PERCEPTION = {"dwell", "hover", "select", "typing", "doc_change", "terminal", "app_switch", "save", "test_run"}
MAX_FRAME = 256 * 1024
RATE_PER_S = 200


def token_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "Trail" if base else Path.home() / ".trail") / "bridge.token"


class Bridge:
    def __init__(self, *, token: str, runtime: Runtime) -> None:
        self.token = token
        self.runtime = runtime
        self.clients: dict[Any, str] = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.agent_mode = True
        self.perception = True
        self.last_state = 0.0
        self.state_pending = False
        self.latency: dict[str, float] = {}
        self._turn_started: float | None = None

    # ---- outputs -> clients ---------------------------------------------------
    def sink(self) -> asyncio.Queue:
        bridge = self

        class Sink(asyncio.Queue):
            def put_nowait(self, item: Output) -> None:  # type: ignore[override]
                bridge.on_output(item)

        return Sink()

    def on_output(self, o: Output) -> None:
        if o.type in {"speak", "speak_start"} and self._turn_started is not None:
            self.latency["last_response_ms"] = round((time.monotonic() - self._turn_started) * 1000, 1)
            self._turn_started = None
        frame = {"type": "output", "output": o.model_dump(mode="json", exclude_none=True)}
        self.broadcast(frame)
        self.schedule_state()

    def broadcast(self, frame: dict, *, only: set[str] | None = None) -> None:
        data = json.dumps(frame, ensure_ascii=False)
        for ws, name in list(self.clients.items()):
            if only and name not in only:
                continue
            asyncio.ensure_future(self._send(ws, data))

    async def _send(self, ws, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:
            self.clients.pop(ws, None)

    def schedule_state(self) -> None:
        if self.state_pending:
            return
        self.state_pending = True
        delay = max(0.0, 0.05 - (time.monotonic() - self.last_state))     # at most 20 Hz

        async def later() -> None:
            await asyncio.sleep(delay)
            self.state_pending = False
            self.last_state = time.monotonic()
            self.broadcast({"type": "state", "state": self.state()})

        asyncio.ensure_future(later())

    def state(self) -> dict[str, Any]:
        rt = self.runtime
        s = rt.state
        ttys = rt.metrics.time_to_yield_ms
        return {
            "agent_mode": self.agent_mode, "perception": self.perception, "active_app": s.active_app,
            "specialist": rt.specialist_override, "phase": s.phase, "version": s.version, "ducked": s.ducked,
            "focus": s.focus, "goals": list(s.goals), "forks": rt.forks.tree()[-8:],
            "calls": [{"call_id": c.call_id, "tool": c.tool, "tag": c.tag, "status": c.status, "goal": c.goal_id}
                      for c in list(rt.saga.calls.values())[-12:]],
            "trail": rt.trail.summary()[-12:],
            "latency": {"time_to_yield_ms": round(ttys[-1], 2) if ttys else None, **self.latency},
            "pending_notices": [{"text": q.text, "tier": q.tier.name.lower()} for q in rt.arbiter.queue],
            "mentor": {"goal": rt.mentor.goal, "mode": rt.mentor.mode, "dropped_as_fixed": len(rt.mentor.dropped_as_fixed)},
        }

    # ---- clients -> runtime ---------------------------------------------------
    async def handle(self, ws) -> None:
        req = ws.request
        q = parse_qs(urlparse(req.path).query)
        client = (q.get("client") or ["cli"])[0][:20]
        host = (ws.remote_address or ("", 0))[0]
        if host not in {"127.0.0.1", "::1", "localhost"}:
            await ws.close(code=4403, reason="loopback only")
            return
        if not secrets.compare_digest((q.get("token") or [""])[0], self.token):
            await ws.send(json.dumps({"type": "error", "code": "unauthorized", "text": "bad token"}))
            await ws.close(code=4401, reason="unauthorized")
            return
        self.clients[ws] = client
        await ws.send(json.dumps({"type": "state", "state": self.state()}))
        window_start, count = time.monotonic(), 0
        try:
            async for raw in ws:
                now = time.monotonic()
                if now - window_start >= 1.0:
                    window_start, count = now, 0
                count += 1
                if count > RATE_PER_S:
                    await ws.send(json.dumps({"type": "error", "code": "rate_limited"}))
                    continue
                try:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    await ws.send(json.dumps({"type": "error", "code": "bad_frame"}))
                    continue
                await self.on_frame(ws, client, frame)
        except Exception:
            pass
        finally:
            self.clients.pop(ws, None)

    async def on_frame(self, ws, client: str, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "hello":
            return
        if kind == "control":
            await self.on_control(ws, frame)
            return
        if kind != "event" or not isinstance(frame.get("event"), dict):
            await ws.send(json.dumps({"type": "error", "code": "bad_frame"}))
            return
        ev = dict(frame["event"])
        etype = ev.get("type")
        if etype in PERCEPTION and not (self.agent_mode and self.perception):
            return      # perception paused: drop at the door, nothing enters the trail
        ev.setdefault("source", client)
        ev.pop("ts", None)
        try:
            event = Event.model_validate(ev)
        except (ValidationError, ValueError):
            await ws.send(json.dumps({"type": "error", "code": "bad_frame", "text": f"invalid {etype} event"}))
            return
        if event.type in {EventType.SPEECH_FINAL, EventType.VAD_START}:
            self._turn_started = time.monotonic()
        await self.events.put(event)

    async def on_control(self, ws, frame: dict) -> None:
        action = frame.get("action")
        rt = self.runtime
        if action == "agent_mode":
            self.agent_mode = bool(frame.get("on"))
        elif action == "perception":
            self.perception = bool(frame.get("on"))
        elif action == "specialist":
            rt.specialist_override = frame.get("name") or None
        elif action == "mode" and frame.get("name") in {"teach", "fix"}:
            rt.mentor.mode = frame["name"]
        elif action == "confirm":
            await self.events.put(Event(type=EventType.SPEECH_FINAL, text="Yes, go ahead." if frame.get("answer") else "No, don't."))
        elif action == "audit":
            read = [{"app": e["app"], "text": e["text"], "kind": e["kind"]} for e in rt.trail.summary()]
            sent = [{"model": getattr(rt.llm, "name", None), "calls": rt.metrics.llm_calls}] if rt.llm else []
            await ws.send(json.dumps({"type": "audit", "read": read, "sent_to_cloud": sent,
                                      "decisions": rt.log[-40:]}, ensure_ascii=False, default=str))
            return
        self.schedule_state()

    # ---- HTTP -------------------------------------------------------------------
    def http(self, connection, request):
        path = urlparse(request.path).path
        if path == "/ws":
            return None                                  # WebSocket upgrade
        routes = {"/demo/flights": WEB / "flights.html", "/demo/budget": WEB / "budget.html",
                  "/corpus/travel.json": CORPUS_PATH}
        target: Path | None = routes.get(path)
        if target is None and path.startswith("/demo/"):
            target = WEB / path[len("/demo/"):]
        if target is None and (path == "/overlay" or path.startswith("/overlay/")):
            rel = path[len("/overlay/"):] or "index.html"
            target = OVERLAY / rel
        if path == "/health":
            return connection.respond(HTTPStatus.OK, "ok\n")
        if target is None or not _inside(target, (WEB, OVERLAY, CORPUS_PATH.parent)) or not target.is_file():
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype += "; charset=utf-8"
        headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
        return Response(200, "OK", headers, body)


def _inside(path: Path, roots) -> bool:
    try:
        rp = path.resolve()
        return any(rp.is_relative_to(r.resolve()) for r in roots)
    except OSError:
        return False


async def serve(port: int = 8765, *, dev: bool = False, speed: float = 1.0) -> None:
    from websockets.asyncio.server import serve as ws_serve

    from ..core.agent import shared_setup

    token = "trail-dev" if dev else secrets.token_urlsafe(18)
    try:
        tp = token_path()
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(token, encoding="utf-8")
    except OSError:
        pass
    shared = await shared_setup()
    runtime = Runtime(RuntimeConfig(mode="desktop", stream_chunk_delay_s=0.06), llm=shared.get("llm"),
                      tool_executor=DesktopTools(speed=speed))
    bridge = Bridge(token=token, runtime=runtime)
    await bridge.events.put(Event(type=EventType.MANIFEST, tools=MANIFEST))
    runner = asyncio.create_task(runtime.run(bridge.events, bridge.sink()))
    async with ws_serve(bridge.handle, "127.0.0.1", port, process_request=bridge.http, max_size=MAX_FRAME):
        print(f"Trail bridge on ws://127.0.0.1:{port}/ws  token={token}")
        print(f"  demo pages: http://127.0.0.1:{port}/demo/flights  http://127.0.0.1:{port}/demo/budget")
        print(f"  overlay (browser): http://127.0.0.1:{port}/overlay/?token={token}")
        print(f"  models: llm={getattr(shared.get('llm'), 'name', None)} stt={shared.get('stt')}")
        await runner


def main(port: int = 8765, corpus: str | None = None, *, dev: bool = False) -> None:
    try:
        asyncio.run(serve(port, dev=dev))
    except KeyboardInterrupt:
        pass
