"""Kit adapter: ParticipantAgent(in_queue, out_queue) over the Trail runtime.

Every kit message becomes a bus event and every runtime output becomes a kit
action, so the scored path and the desktop path run the same runtime code
(PDF p. 5-7). The mapping follows docs/kit/PROTOCOL.md exactly.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from pydantic import ValidationError

from . import media
from .bus import Event, EventType, Output
from .config import RuntimeConfig
from .llm import select_llm
from .runtime import Runtime

# Heavy resources are shared across scenarios (one agent instance per scenario).
_SHARED: dict[str, Any] = {"ready": False, "llm": None, "stt": False}
_SETUP_LOCK: asyncio.Lock | None = None


def decode(message: Any) -> Event | None:
    """Kit event dict -> bus Event. Returns None for anything unusable."""
    if not isinstance(message, dict):
        return None
    kind = message.get("event_type")
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    ts = message.get("timestamp_ms")
    ts = float(ts) if isinstance(ts, (int, float)) and ts >= 0 else None
    text = payload.get("text") if isinstance(payload.get("text"), str) else ""
    text = text[:32_000]
    try:
        if kind == "tool_manifest":
            tools = payload.get("tools") if isinstance(payload.get("tools"), dict) else {}
            return Event(type=EventType.MANIFEST, tools=tools, ts=ts, source="kit")
        if kind == "user_speech_chunk":
            final = bool(payload.get("end_of_turn"))
            return Event(type=EventType.SPEECH_FINAL if final else EventType.SPEECH_PARTIAL, text=text, ts=ts,
                         source="kit", barge_in=False) if (text or final) else None
        if kind == "interruption":
            return Event(type=EventType.SPEECH_FINAL, text=text, ts=ts, source="kit", barge_in=True)
        if kind == "user_audio_chunk":
            ref = payload.get("audio_ref")
            if not isinstance(ref, str) or not ref:
                return None
            dur = payload.get("duration_ms")
            return Event(type=EventType.AUDIO, media_ref=ref, final=bool(payload.get("end_of_turn", True)),
                         duration_ms=float(dur) if isinstance(dur, (int, float)) else None, ts=ts, source="kit")
        if kind == "video_frame":
            ref = payload.get("image_ref")
            if not isinstance(ref, str) or not ref:
                return None
            hint = payload.get("device_hint")
            fid = payload.get("frame_id")
            return Event(type=EventType.FRAME, media_ref=ref, frame_id=str(fid) if fid is not None else None,
                         device_hint=hint if isinstance(hint, str) else None, ts=ts, source="kit")
        if kind == "tool_result":
            cid = payload.get("call_id")
            if not isinstance(cid, str) or not cid:
                return None
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            status = payload.get("status") if isinstance(payload.get("status"), str) else result.get("status", "success")
            return Event(type=EventType.TOOL_RESULT, call_id=cid, api_name=str(payload.get("api_name") or ""),
                         status=status, result=result, ts=ts, source="kit")
        if kind == "scenario_end":
            return Event(type=EventType.INPUT_END, ts=ts, source="kit")
    except (ValidationError, ValueError, TypeError):
        return None
    return None


def encode(output: Output) -> dict[str, Any] | None:
    """Runtime output -> kit action. Non-kit outputs (duck, status, ...) are dropped."""
    if output.type == "speak":
        action = {"ack": "filler_speech", "notice": "filler_speech", "clarify": "clarification_request",
                  "final": "final_response"}.get(output.kind or "")
        if action is None or not output.text.strip():
            return None
        msg: dict[str, Any] = {"action": action, "payload": {"text": output.text}}
        # The snapshot rides on every spoken action, so the recovery scorer always sees the latest state.
        msg["state_snapshot"] = output.snapshot or {"intent": "none", "slots": {}}
        return msg
    if output.type == "tool_call":
        return {"action": "tool_call", "payload": {"call_id": output.call_id, "api_name": output.api_name,
                                                   "args": dict(output.args or {})}}
    if output.type == "tool_cancel":
        return {"action": "cancel_tool", "payload": {"call_id": output.call_id}}
    return None


class _KitSink(asyncio.Queue):
    """Translate runtime outputs straight onto the kit queue: no extra hop, no added latency."""

    def __init__(self, out_queue: asyncio.Queue, trace: list | None = None) -> None:
        super().__init__()
        self._out = out_queue
        self._trace = trace

    def put_nowait(self, item: Output) -> None:  # type: ignore[override]
        if self._trace is not None:
            self._trace.append(item)
        action = encode(item)
        if action is not None:
            self._out.put_nowait(action)


async def shared_setup(*, llm_choice: str | None = None) -> dict[str, Any]:
    global _SETUP_LOCK
    if _SETUP_LOCK is None:
        _SETUP_LOCK = asyncio.Lock()
    async with _SETUP_LOCK:
        if not _SHARED["ready"]:
            try:
                _SHARED["llm"] = await asyncio.wait_for(select_llm(llm_choice), 150)
            except Exception:
                _SHARED["llm"] = None
            try:
                _SHARED["stt"] = await asyncio.wait_for(media.load_stt(), 140)
            except Exception:
                _SHARED["stt"] = False
            _SHARED["ready"] = True
    return _SHARED


class ParticipantAgent:
    """The kit entry point (submission.yaml: agent.agent:ParticipantAgent)."""

    config = RuntimeConfig(mode="harness")

    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue) -> None:
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.runtime: Runtime | None = None
        self.outputs: list[Output] = []

    async def setup(self) -> None:
        await shared_setup()

    async def run(self) -> None:
        shared = _SHARED if _SHARED["ready"] else {"llm": None}
        self.runtime = Runtime(self.config, llm=shared.get("llm"))
        events: asyncio.Queue = asyncio.Queue()
        sink = _KitSink(self.out_queue, self.outputs if os.environ.get("TRAIL_TRACE") else None)
        runner = asyncio.create_task(self.runtime.run(events, sink), name="trail-runtime")

        async def ingest() -> None:
            while True:
                message = await self.in_queue.get()
                event = decode(message)
                if event is not None:
                    events.put_nowait(event)

        feeder = asyncio.create_task(ingest(), name="trail-kit-input")
        try:
            done, _ = await asyncio.wait({runner, feeder}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    raise t.exception()
            await asyncio.Event().wait()   # the harness cancels run() at shutdown
        finally:
            for t in (feeder, runner):
                if not t.done():
                    t.cancel()
            await asyncio.gather(feeder, runner, return_exceptions=True)


class NaiveAgent(ParticipantAgent):
    """Ablation rung 1: cancel and restart on any user input."""

    from .config import Features as _F

    config = RuntimeConfig(mode="harness", features=_F.ladder()[0][1])
