"""Scripted replay of the demo acts (PDF p. 18): the live-demo fallback and an integration test.

    python -m trail demo act1            # booking hero: trail recall, self-interruption, fork hit, detour, barrier
    python -m trail demo act2            # cross-app: afford it?
    python -m trail demo act3            # code mentor: waits, drops fixed warnings, secret now, pre-diagnosis
    python -m trail demo heckler         # 5 interrupts: Trail vs the naive cancel-and-restart agent
    python -m trail demo all --via-bridge   # send the same events to a running bridge so the overlay shows them

Runs the real runtime in desktop mode with the Skylark Air tools; nothing is mocked except the user.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

from ..core.bus import Event, EventType, Output, Target
from ..core.config import RuntimeConfig
from ..core.runtime import Runtime
from .tools import MANIFEST, DesktopTools

ROUTE = "Chandigarh → Goa"


@dataclass
class Step:
    say: str | None = None          # narration printed before the step
    event: dict | None = None       # bus event fields
    wait: float = 0.0               # seconds to wait after sending (lets tools/streams progress)
    until: str | None = None        # wait for an output matching "type:kind" or "status:code"


def dwell(text: str, context: str = ROUTE, app: str = "chrome", dwell_ms: float = 600) -> dict:
    return {"type": "dwell", "app": app, "source": "extension",
            "target": {"role": "cell", "text": text, "context": context, "dwell_ms": dwell_ms}}


def ask(text: str) -> dict:
    return {"type": "speech_final", "text": text, "source": "speech"}


def doc(version: int, text: str, changed: list[tuple[int, str]]) -> dict:
    return {"type": "doc_change", "app": "vscode", "source": "vscode",
            "data": {"file": "auth.py", "version": version, "language": "python", "text": text,
                     "changed": [{"line": n, "text": t} for n, t in changed], "diagnostics": []}}


AUTH_V1 = '''import requests

def login(email, password):
    user = db.get_user(email)
    return user.email
'''
AUTH_V2 = AUTH_V1.replace("    return user.email\n", "    if password == user.password_hash:\n        return user.email\n")
AUTH_V3 = AUTH_V2.replace("if password == user.password_hash:", "if hmac.compare_digest(hash_pw(password), user.password_hash):")
AUTH_V4 = 'API_KEY = "sk-live-4f9a8b7c6d5e4f3a2b1c0d9e8f7a"\n' + AUTH_V3
TRACE = '''FAILED test_auth.py::test_login_unknown_user
Traceback (most recent call last):
  File "test_auth.py", line 9, in test_login_unknown_user
    assert login("nobody@example.com", "x") is None
  File "auth.py", line 6, in login
    if hmac.compare_digest(hash_pw(password), user.password_hash):
AttributeError: 'NoneType' object has no attribute 'password_hash'
'''

ACTS: dict[str, list[Step]] = {
    "act1": [
        Step("Agent mode on. The user hovers three dates on the Skylark Air results page.",
             {"type": "app_switch", "app": "chrome"}),
        Step(None, dwell("Fri · ₹6,400")), Step(None, dwell("Sat · ₹5,000")), Step(None, dwell("Sun · ₹11,000")),
        Step('User: "Which should I book?"', ask("Which should I book?"), wait=0.25),
        Step("While Trail is still talking, the user hovers Monday.", dwell("Mon · ₹4,500"), wait=0.6),
        Step('User: "Actually, two passengers."', ask("Actually, two passengers."), wait=0.6),
        Step('User: "What\'s the baggage allowance?"', ask("What's the baggage allowance?"), until="speak:final"),
        Step('User: "Back to the flight."', ask("Back to the flight."), wait=0.6),
        Step('User: "Book it."', ask("Book it."), until="speak:clarify"),
    ],
    "act2": [
        Step("The user switches to the budget sheet and looks at the remaining-budget cell.",
             {"type": "app_switch", "app": "excel"}),
        Step(None, dwell("Remaining for flights · ₹5,000", context="October trip budget", app="excel")),
        Step('User: "Can I afford the cheapest one?"', ask("Can I afford the cheapest one?"), wait=0.6),
    ],
    "act3": [
        Step("In VS Code.", {"type": "app_switch", "app": "vscode"}),
        Step('User: "I\'m building OAuth login."', ask("I'm building OAuth login."), wait=0.3),
        Step("The user types a bug (typing, so the warning waits).", {"type": "typing", "app": "vscode", "active": True}),
        Step(None, doc(1, AUTH_V1, [(5, "    return user.email")]), wait=0.2),
        Step("Typing pause: the queued warning is delivered now.", {"type": "typing", "app": "vscode", "active": False}, wait=0.2),
        Step("A second bug, fixed before the pause.", {"type": "typing", "app": "vscode", "active": True}),
        Step(None, doc(2, AUTH_V2, [(5, "    if password == user.password_hash:")]), wait=0.1),
        Step(None, doc(3, AUTH_V3, [(5, "    if hmac.compare_digest(hash_pw(password), user.password_hash):")]), wait=0.1),
        Step("Pause: the stale warning is dropped silently.", {"type": "typing", "app": "vscode", "active": False}, wait=0.2),
        Step("The user pastes an API key mid-typing: critical, so Trail interrupts at once.",
             {"type": "typing", "app": "vscode", "active": True}),
        Step(None, doc(4, AUTH_V4, [(1, AUTH_V4.splitlines()[0])]), wait=0.2),
        Step("Tests run and fail; a stack trace appears in the terminal.", {"type": "typing", "app": "vscode", "active": False}),
        Step(None, {"type": "test_run", "app": "vscode", "data": {"passed": False, "summary": "1 failed"}}),
        Step(None, {"type": "terminal", "app": "vscode", "text": TRACE}, wait=0.1),
        Step('User: "Why did that break?"', ask("Why did that break?"), wait=0.3),
    ],
}


class Printer:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.streaming = False
        self.buffer: list[str] = []

    def ts(self) -> str:
        return f"{(time.monotonic() - self.t0):6.2f}s"

    def show(self, o: Output) -> None:
        if o.type == "speak_start":
            self.streaming = True
            print(f"{self.ts()}  Trail ▸ ", end="", flush=True)
        elif o.type == "token":
            print(o.text, end="", flush=True)
        elif o.type == "speak_end":
            print(flush=True)
            self.streaming = False
        elif o.type == "speak":
            if self.streaming:
                print(" [cut off]", flush=True)
                self.streaming = False
            tag = {"ack": "…", "clarify": "?", "final": "▸", "notice": "!"}.get(o.kind or "", "▸")
            tier = f" ({o.meta.get('tier')})" if o.kind == "notice" else ""
            print(f"{self.ts()}  Trail {tag}{tier} {o.text}", flush=True)
        elif o.type in {"tool_call", "tool_cancel"}:
            tag = o.meta.get("tag", "") if o.meta else ""
            verb = "calls" if o.type == "tool_call" else "cancels"
            print(f"{self.ts()}    [{verb} {o.api_name} {json.dumps(o.args or {}, ensure_ascii=False)} {tag}]", flush=True)
        elif o.type == "status" and o.code in {"fork_hit", "notice_dropped", "prediagnosis_ready", "prediagnosis_hit",
                                                "notice_waiting", "context_rejected"}:
            print(f"{self.ts()}    [{o.code} {json.dumps(o.meta, ensure_ascii=False)}]", flush=True)


async def play(acts: list[str], *, speed: float = 1.0, chunk_delay: float = 0.045) -> Runtime:
    rt = Runtime(RuntimeConfig(mode="desktop", stream_chunk_delay_s=chunk_delay / max(speed, 0.1)),
                 tool_executor=DesktopTools(speed=speed))
    inq: asyncio.Queue = asyncio.Queue()
    outq: asyncio.Queue = asyncio.Queue()
    printer = Printer()
    runner = asyncio.create_task(rt.run(inq, outq))
    seen: list[Output] = []

    async def drain() -> None:
        while True:
            o = await outq.get()
            seen.append(o)
            printer.show(o)

    drainer = asyncio.create_task(drain())
    await inq.put(Event(type=EventType.MANIFEST, tools=MANIFEST))
    for act in acts:
        print(f"\n===== {act} =====", flush=True)
        for step in ACTS[act]:
            if step.say:
                print(f"{printer.ts()}  ── {step.say}", flush=True)
            if step.event is not None:
                ev = dict(step.event)
                if "target" in ev:
                    ev["target"] = Target(**ev["target"])
                mark = len(seen)
                await inq.put(Event(**ev))
                if step.until:
                    kind, _, sub = step.until.partition(":")
                    deadline = time.monotonic() + 8 / max(speed, 0.1)
                    while time.monotonic() < deadline and not any(
                            o.type == kind and (o.kind == sub or o.code == sub) for o in seen[mark:]):
                        await asyncio.sleep(0.02)
                await asyncio.sleep(step.wait / max(speed, 0.1))
        # let streams finish before the next act
        await asyncio.sleep(1.2 / max(speed, 0.1))
    await inq.put(Event(type=EventType.SESSION_END))
    await asyncio.wait_for(runner, 5)
    await asyncio.sleep(0.05)
    drainer.cancel()
    m = rt.metrics.as_dict()
    print(f"\n[metrics] forks spawned {m['fork_spawned']}, fork hits {m['fork_hits']}, calls {m['calls_issued']}, "
          f"cancelled {m['calls_cancelled']}, held at barrier {m['held_at_barrier']}, notices dropped as fixed "
          f"{rt.arbiter.dropped_stale}, pre-diagnosis hits {rt.prediagnosis_hits}, errors {m['errors']}", flush=True)
    return rt


def heckler() -> None:
    """Finale: the same five interrupts against Trail and the naive baseline, scored by the kit."""
    import os

    os.environ.setdefault("TRAIL_LLM", "none")
    os.environ.setdefault("TRAIL_STT", "none")
    from pathlib import Path

    from ..core.agent import NaiveAgent, ParticipantAgent
    from ..eval import _run_one, trace_metrics
    from harness.scorer import score_scenario

    sc = json.loads((Path(__file__).resolve().parents[2] / "scenarios_trail" / "trail_18_heckler.json").read_text(encoding="utf-8"))
    for name, cls in (("naive cancel-and-restart", NaiveAgent), ("Trail", ParticipantAgent)):
        trace, _ = asyncio.run(_run_one(sc, cls, 2.0))
        score = score_scenario(sc, trace)["total"]
        m = trace_metrics(sc, trace)
        ys = ", ".join(f"{y:.0f}" for y in m["yield_ms"])
        print(f"{name:>26}: score {score:5.1f}   response to each interrupt (ms): {ys}")


def main(act: str, *, speed: float = 1.0) -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if act == "heckler":
        heckler()
        return
    acts = ["act1", "act2", "act3"] if act == "all" else [act]
    asyncio.run(play(acts, speed=speed))
