"""Session trail: what the user attended to, and resolution of what they mean.

In-memory only, cleared at session end (PDF p. 11-12). Entries are untrusted
page text: they can ground an answer, never authorise a tool call.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import entities as ent

_CELL_RE = re.compile(r"\b([A-Z]{1,3})([1-9]\d{0,5})\b")
_SYMBOL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\(")


@dataclass(frozen=True)
class TrailEntry:
    id: str
    ts: float                  # seconds
    app: str
    source: str
    text: str
    context: str = ""
    role: str = "text"
    dwell_ms: float = 0.0
    kind: str = "dwell"        # dwell, select, frame, hover
    prices: tuple[ent.Price, ...] = ()
    dates: tuple[str, ...] = ()
    weekdays: tuple[int, ...] = ()
    times: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    cells: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    flight_ids: tuple[str, ...] = ()
    image_ref: str | None = None
    untrusted: bool = True

    @property
    def price(self) -> ent.Price | None:
        return self.prices[0] if self.prices else None

    @property
    def label(self) -> str:
        return self.dates[0] if self.dates else (self.text[:40] if self.text else self.context[:40])


def make_entry(*, id: str, ts: float, app: str, source: str, text: str, context: str = "", role: str = "text",
               dwell_ms: float = 0.0, kind: str = "dwell", image_ref: str | None = None) -> TrailEntry:
    both = f"{text} {context}"
    dates = ent.find_dates(text) or ent.find_dates(context)
    return TrailEntry(
        id=id, ts=ts, app=app, source=source, text=text, context=context, role=role, dwell_ms=dwell_ms,
        kind=kind, prices=tuple(ent.find_prices(text)), dates=tuple(d.value for d in dates),
        weekdays=tuple(d.weekday for d in dates if d.weekday is not None),
        times=tuple(t.label for t in ent.find_times(text)), places=tuple(p.name for p in ent.find_places(both)),
        cells=tuple(f"{a}{b}" for a, b in _CELL_RE.findall(text)) if role in {"cell", "dataitem"} else (),
        symbols=tuple(dict.fromkeys(_SYMBOL_RE.findall(text))), flight_ids=tuple(m.upper() for m in ent.FLIGHT_ID_RE.findall(text)),
        image_ref=image_ref,
    )


@dataclass
class TrailStore:
    max_entries: int = 256
    tau_s: float = 600.0
    dwell0_ms: float = 350.0
    entries: list[TrailEntry] = field(default_factory=list)

    def add(self, entry: TrailEntry) -> TrailEntry:
        # One entry per (app, text, context): re-dwelling refreshes recency and accumulates attention.
        prev = next((e for e in self.entries if (e.app, e.text, e.context) == (entry.app, entry.text, entry.context)), None)
        if prev is not None:
            self.entries.remove(prev)
            entry = TrailEntry(**{**entry.__dict__, "dwell_ms": prev.dwell_ms + entry.dwell_ms})
        self.entries.append(entry)
        del self.entries[:-self.max_entries]
        return entry

    def clear(self) -> None:
        self.entries.clear()

    def score(self, entry: TrailEntry, match: float, now_s: float) -> float:
        age = max(now_s - entry.ts, 0.0)
        return match * math.exp(-age / self.tau_s) * (1 + math.log(1 + entry.dwell_ms / max(self.dwell0_ms, 1.0)))

    # ---- referents ----------------------------------------------------------
    def current(self, *, kinds: Iterable[str] = ("dwell", "select", "frame")) -> TrailEntry | None:
        kinds = set(kinds)
        return next((e for e in reversed(self.entries) if e.kind in kinds), None)

    def previous(self) -> TrailEntry | None:
        seen = [e for e in reversed(self.entries) if e.kind in {"dwell", "select", "frame"}]
        return seen[1] if len(seen) > 1 else None

    def fares(self, *, context: str | None = None, app: str | None = None) -> list[TrailEntry]:
        out = [e for e in self.entries if e.prices and (e.dates or e.flight_ids or e.times)]
        if app:
            out = [e for e in out if app.lower() in e.app.lower() or app.lower() in e.source.lower()]
        if context is not None:
            out = [e for e in out if e.context == context]
        return out

    def active_route(self) -> str | None:
        fares = self.fares()
        return fares[-1].context if fares else None

    def cheapest(self, *, context: str | None = None) -> TrailEntry | None:
        fares = self.fares(context=context)
        return min(fares, key=lambda e: e.price.amount) if fares else None

    def budget_cell(self) -> TrailEntry | None:
        for e in reversed(self.entries):
            if e.prices and re.search(r"budget|remaining|left|balance|available", f"{e.text} {e.context}", re.I):
                return e
        return None

    def search(self, words: Iterable[str], now_s: float, *, app: str | None = None, limit: int = 5) -> list[tuple[float, TrailEntry]]:
        wanted = {w.lower() for w in words if len(w) > 2}
        scored = []
        for e in self.entries:
            if app and app.lower() not in e.app.lower():
                continue
            hay = f"{e.text} {e.context}".lower()
            hits = sum(1 for w in wanted if w in hay)
            if hits:
                scored.append((self.score(e, hits / max(len(wanted), 1), now_s), e))
        scored.sort(key=lambda s: -s[0])
        return scored[:limit]

    def summary(self) -> list[dict[str, Any]]:
        return [{"app": e.app, "text": e.text, "context": e.context, "kind": e.kind} for e in self.entries]
