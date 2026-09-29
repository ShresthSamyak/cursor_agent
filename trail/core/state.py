"""Immutable session snapshots and interruption metrics (PDF p. 17)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionState:
    """What outside readers may see. Only Runtime's loop replaces it."""

    session_id: str
    version: int = 0                     # increments on every semantic change
    turn: int = 0
    phase: str = "listening"             # listening, planning, tool_calls, speaking, done, closed
    ducked: bool = False
    user_speaking: bool = False
    typing: bool = False
    focus: dict[str, Any] | None = None  # {"intent", "slots"}
    goals: tuple[dict[str, Any], ...] = ()
    in_flight: tuple[str, ...] = ()
    active_app: str = ""


@dataclass
class Metrics:
    turns: int = 0
    interrupts: dict[str, int] = field(default_factory=dict)
    time_to_yield_ms: list[float] = field(default_factory=list)
    first_response_ms: list[float] = field(default_factory=list)
    correction_latency_ms: list[float] = field(default_factory=list)
    corrections: int = 0
    fork_spawned: int = 0
    fork_hits: int = 0
    fork_killed: int = 0
    backchannel_false_stops: int = 0
    stale_output_leaks: int = 0
    stale_results_ignored: int = 0
    stale_proposals_dropped: int = 0
    wasted_agent_interrupts: int = 0
    duplicate_writes: int = 0
    duplicate_writes_prevented: int = 0
    calls_issued: int = 0
    calls_cancelled: int = 0
    compensations: int = 0
    retries: int = 0
    fillers: int = 0
    held_at_barrier: int = 0
    llm_calls: int = 0
    llm_failures: int = 0
    duplicate_events_dropped: int = 0
    errors: int = 0

    def interrupt(self, kind: str) -> None:
        self.interrupts[kind] = self.interrupts.get(kind, 0) + 1

    @property
    def fork_hit_rate(self) -> float | None:
        return self.fork_hits / self.corrections if self.corrections else None

    def as_dict(self) -> dict[str, Any]:
        def stats(xs: list[float]) -> dict[str, float] | None:
            if not xs:
                return None
            s = sorted(xs)
            return {"n": len(s), "max": round(s[-1], 1), "p50": round(s[len(s) // 2], 1),
                    "mean": round(sum(s) / len(s), 1)}

        out = {k: v for k, v in self.__dict__.items() if not isinstance(v, list)}
        out["time_to_yield_ms"] = stats(self.time_to_yield_ms)
        out["first_response_ms"] = stats(self.first_response_ms)
        out["correction_latency_ms"] = stats(self.correction_latency_ms)
        out["fork_hit_rate"] = self.fork_hit_rate
        return out
