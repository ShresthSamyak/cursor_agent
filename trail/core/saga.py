"""Transactional tools: tags, idempotent call ids, rollback, compensation, commit barrier.

Every call the agent makes goes through the ledger. A state-modifying call with
the same normalised arguments is never issued twice while one is in flight or
committed, and never retried blindly after an ambiguous failure (PDF p. 8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tools import COMPENSABLE, IRREVERSIBLE, REVERSIBLE, Manifest, normalized_key

IN_FLIGHT, COMMITTED, FAILED, CANCELLED, COMPENSATED, UNKNOWN = (
    "in_flight", "committed", "failed", "cancelled", "compensated", "unknown")
# Errors returned before anything was committed: safe to fix and resend.
PRE_COMMIT_ERRORS = {"invalid_args", "unknown_tool", "not_found"}


@dataclass
class Call:
    call_id: str
    tool: str
    args: dict[str, Any]
    key: str
    tag: str
    goal_id: str
    step: str
    issued_version: int
    status: str = IN_FLIGHT
    result: dict[str, Any] | None = None
    error: str | None = None
    attempt: int = 0
    stale: bool = False
    compensates: str | None = None       # call_id this call undoes

    @property
    def state_modifying(self) -> bool:
        return self.tag != REVERSIBLE


@dataclass
class Saga:
    manifest: Manifest = field(default_factory=Manifest)
    calls: dict[str, Call] = field(default_factory=dict)
    by_key: dict[str, list[str]] = field(default_factory=dict)
    seq: int = 0
    duplicate_writes_prevented: int = 0
    duplicate_writes: int = 0

    def next_id(self, tool: str) -> str:
        self.seq += 1
        return f"t{self.seq}-{tool[:24]}"

    def tag(self, tool: str) -> str:
        return self.manifest.tag(tool)

    def blocked(self, tool: str, args: dict[str, Any]) -> Call | None:
        """An existing call that makes issuing this one unsafe or pointless."""
        key = normalized_key(tool, args)
        for cid in self.by_key.get(key, []):
            call = self.calls[cid]
            if call.tag == REVERSIBLE:
                if call.status == IN_FLIGHT and not call.stale:
                    return call
                continue
            if call.status in {IN_FLIGHT, COMMITTED, UNKNOWN}:
                return call
        return None

    def open(self, tool: str, args: dict[str, Any], *, goal_id: str, step: str, version: int,
             attempt: int = 0, compensates: str | None = None) -> Call:
        call = Call(call_id=self.next_id(tool), tool=tool, args=args, key=normalized_key(tool, args),
                    tag=self.tag(tool), goal_id=goal_id, step=step, issued_version=version,
                    attempt=attempt, compensates=compensates)
        self.calls[call.call_id] = call
        self.by_key.setdefault(call.key, []).append(call.call_id)
        return call

    def settle(self, call_id: str, status: str, result: dict[str, Any] | None) -> Call | None:
        call = self.calls.get(call_id)
        if call is None:
            return None
        call.result = result if isinstance(result, dict) else {}
        if status == "success":
            if call.state_modifying and call.status == COMMITTED:
                self.duplicate_writes += 1
            call.status = COMMITTED if call.state_modifying else "done"
            if call.compensates and call.compensates in self.calls:
                self.calls[call.compensates].status = COMPENSATED
        else:
            code = str(call.result.get("error") or "error")
            call.error = code
            if code == "duplicate_booking":
                # The world already holds this booking: treat as committed, never retry.
                call.status = COMMITTED
            elif call.state_modifying and code not in PRE_COMMIT_ERRORS:
                call.status = UNKNOWN
            else:
                call.status = FAILED
        return call

    def abort(self, call: Call) -> str:
        """Roll back one call. Returns 'cancel', 'compensate', 'hold' or 'none'."""
        if call.status == IN_FLIGHT:
            call.status = CANCELLED
            call.stale = True
            return "cancel"
        call.stale = True
        if call.status == COMMITTED and call.tag == COMPENSABLE:
            return "compensate"
        if call.status == COMMITTED and call.tag == IRREVERSIBLE:
            return "hold"
        return "none"

    def compensation_args(self, call: Call) -> tuple[str, dict[str, Any]] | None:
        tool = self.manifest.compensator(call.tool)
        spec = self.manifest.get(tool) if tool else None
        if spec is None:
            return None
        pool = {**call.args, **(call.result or {})}
        args = {a.name: pool[a.name] for a in spec.args if a.name in pool}
        if any(a.required and a.name not in args for a in spec.args):
            return None
        return tool, args

    def in_flight(self, goal_id: str | None = None) -> list[Call]:
        return [c for c in self.calls.values()
                if c.status == IN_FLIGHT and (goal_id is None or c.goal_id == goal_id)]

    def committed(self, tool: str | None = None) -> list[Call]:
        return [c for c in self.calls.values() if c.status == COMMITTED and (tool is None or c.tool == tool)]


__all__ = ["Saga", "Call", "REVERSIBLE", "COMPENSABLE", "IRREVERSIBLE", "IN_FLIGHT", "COMMITTED",
           "FAILED", "CANCELLED", "COMPENSATED", "UNKNOWN"]
