"""Interrupt Arbiter: the single gate for everything the agent says.

Harness rules come from the kit's safety rubric (filler budget, no verbatim
repeats, no premature completion claims). Desktop rules come from the PDF
(p. 4-5): severity tiers, flow state, staleness check, unsolicited budget.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

# Kinds of speech.
ACK, CLARIFY, FINAL, NOTICE = "ack", "clarify", "final", "notice"


class Tier(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


# Past-tense completion claims. A future guard ("I'll", "now", "let me") makes it a promise.
_CLAIMS: dict[str, tuple[str, ...]] = {
    "book": (r"(?<![-\w])booked\b", r"(?<![-\w])reserved\b", r"booking (?:is )?confirmed", r"you'?re (?:all )?set\b", r"confirmed your"),
    "ticket": (r"ticket (?:id|created|opened|filed|number)", r"\b(?:opened|filed|created|raised|logged) (?:a |the |your )?(?:support )?ticket"),
    "cancel": (r"booking(?: [a-z]{2,4}-\w+)? (?:is |was |has been )?cancell?ed", r"cancell?ed (?:your|the) booking"),
    "generic": (r"\b(?:has|have) been (?:created|submitted|scheduled|sent|placed|ordered|charged|paid|processed|updated|deleted)\b",
                r"\b(?:i|we) (?:have |'ve )?(?:created|submitted|scheduled|sent|placed|ordered|charged|paid|processed|deleted)\b"),
}
FUTURE_GUARDS = ("will", "'ll", "going to", "let me", "one moment", "about to", "getting", "get that",
                 "get this", "now", "right away")


def claims(text: str) -> set[str]:
    low = text.lower()
    if any(g in low for g in FUTURE_GUARDS):
        return set()
    return {kind for kind, pats in _CLAIMS.items() if any(re.search(p, low) for p in pats)}


@dataclass
class Pending:
    kind: str
    text: str
    tier: Tier
    key: str
    still_valid: Callable[[], bool] | None = None
    created_ms: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass
class Arbiter:
    mode: str = "harness"
    filler_budget: int = 3
    unsolicited_budget: int = 6
    window_ms: float = 600_000.0
    fillers_spoken: int = 0
    spoken: list[str] = field(default_factory=list)
    unsolicited: deque[float] = field(default_factory=deque)
    threshold: Tier = Tier.NORMAL
    threshold_until_ms: float = 0.0
    queue: list[Pending] = field(default_factory=list)
    dropped_stale: int = 0
    dropped_budget: int = 0
    suppressed_claims: int = 0
    audit: list[dict] = field(default_factory=list)

    # ---- user-directed speech ---------------------------------------------
    def allow(self, kind: str, text: str, *, completed_kinds: set[str]) -> bool:
        """Gate for replies. Returns False when the message must not be said."""
        text = text.strip()
        if len(text) < 3:
            return False
        claimed = claims(text) - completed_kinds
        if claimed:
            self.suppressed_claims += 1
            self._log("drop", kind, text, "premature completion claim")
            return False
        if kind == ACK:
            if self.fillers_spoken >= self.filler_budget:
                self.dropped_budget += 1
                self._log("drop", kind, text, "filler budget")
                return False
            if text.lower() in (s.lower() for s in self.spoken):
                self._log("drop", kind, text, "verbatim repeat")
                return False
            self.fillers_spoken += 1
        self.spoken.append(text)
        self._log("say", kind, text, "")
        return True

    def fillers_left(self) -> int:
        return max(self.filler_budget - self.fillers_spoken, 0)

    # ---- agent-initiated interruptions (desktop) --------------------------
    def not_now(self, now_ms: float, minutes: float = 10.0) -> None:
        self.threshold = Tier.CRITICAL
        self.threshold_until_ms = now_ms + minutes * 60_000

    def submit(self, item: Pending) -> None:
        self.queue = [q for q in self.queue if q.key != item.key]
        self.queue.append(item)

    def due(self, *, now_ms: float, user_speaking: bool, typing: bool, boundary: bool) -> list[Pending]:
        """Messages that may be spoken now. Stale ones are dropped silently."""
        if now_ms >= self.threshold_until_ms:
            self.threshold = Tier.NORMAL
        while self.unsolicited and now_ms - self.unsolicited[0] > self.window_ms:
            self.unsolicited.popleft()
        ready, keep = [], []
        for item in sorted(self.queue, key=lambda q: -q.tier):
            if item.still_valid is not None and not item.still_valid():
                self.dropped_stale += 1
                self._log("drop", "notice", item.text, "stale: user already fixed it")
                continue
            if item.tier < self.threshold and item.tier != Tier.CRITICAL:
                keep.append(item)
                continue
            if item.tier == Tier.CRITICAL:
                ok = True
            elif user_speaking:
                ok = False
            elif item.tier == Tier.HIGH:
                ok = not typing
            elif item.tier == Tier.NORMAL:
                ok = boundary
            else:
                ok = False          # LOW: batched digest, only when asked
            if ok and item.tier != Tier.CRITICAL and len(self.unsolicited) >= self.unsolicited_budget:
                ok = False
            if ok:
                ready.append(item)
                if item.tier != Tier.CRITICAL:
                    self.unsolicited.append(now_ms)
            else:
                keep.append(item)
        self.queue = keep
        for item in ready:
            self._log("say", "notice", item.text, f"tier={item.tier.name}")
        return ready

    def digest(self) -> list[Pending]:
        low = [q for q in self.queue if q.tier == Tier.LOW and (q.still_valid is None or q.still_valid())]
        self.queue = [q for q in self.queue if q.tier != Tier.LOW]
        return low

    def _log(self, action: str, kind: str, text: str, reason: str) -> None:
        self.audit.append({"action": action, "kind": kind, "text": text, "reason": reason})
        del self.audit[:-200]
