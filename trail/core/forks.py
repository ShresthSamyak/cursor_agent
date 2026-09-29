"""Speculative forks: pre-compute the 2-3 corrections the user is most likely to make.

A fork is a hypothesis (slot -> value) plus a coroutine that computes what the
agent would do under it. Forks run in idle time, carry a budget, and die on
any change to a slot they did not hypothesise (PDF p. 8).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import entities as ent

RUNNING, READY, DEAD, SERVED = "running", "ready", "dead", "served"


@dataclass
class Fork:
    id: str
    goal_id: str
    hypothesis: dict[str, Any]
    basis: dict[str, int]             # slot epochs this fork assumed for everything else
    status: str = RUNNING
    result: Any = None
    task: asyncio.Task | None = None
    cost: int = 0


def likely_corrections(slots: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Cheap alternatives for the slots a user most often corrects."""
    alts: list[dict[str, Any]] = []
    date = slots.get("date")
    if isinstance(date, str):
        days = [d for d in ent.find_dates(date) if d.weekday is not None]
        if days:
            wd = days[0].weekday
            for delta in (1, -1):
                alts.append({"date": ent.WEEKDAYS[(wd + delta) % 7]})
        elif date.lower() == "today":
            alts.append({"date": "tomorrow"})
        elif date.lower() == "tomorrow":
            alts.append({"date": "day after tomorrow"})
    pax = slots.get("passengers")
    if isinstance(pax, int):
        alts.append({"passengers": pax + 1})
    elif slots.get("destination"):
        alts.append({"passengers": 2})
    pref = slots.get("flight_pref") or {}
    if isinstance(pref, dict) and pref.get("rank") != "cheapest":
        alts.append({"flight_pref": {"rank": "cheapest"}})
    return alts[:limit]


@dataclass
class ForkManager:
    max_forks: int = 3
    budget: int = 1500
    forks: dict[str, Fork] = field(default_factory=dict)
    seq: int = 0
    spawned: int = 0
    hits: int = 0
    killed: int = 0

    def spawn(self, goal_id: str, hypothesis: dict[str, Any], basis: dict[str, int],
              factory: Callable[[], Awaitable[Any]]) -> Fork | None:
        live = [f for f in self.forks.values() if f.goal_id == goal_id and f.status in {RUNNING, READY}]
        if len(live) >= self.max_forks:
            return None
        if any(f.hypothesis == hypothesis for f in live):
            return None
        self.seq += 1
        fork = Fork(id=f"f{self.seq}", goal_id=goal_id, hypothesis=hypothesis, basis=dict(basis))

        async def run() -> None:
            try:
                fork.result = await factory()
                fork.status = READY
            except asyncio.CancelledError:
                fork.status = DEAD
                raise
            except Exception:
                fork.status = DEAD

        fork.task = asyncio.create_task(run(), name=f"trail-fork-{fork.id}")
        self.forks[fork.id] = fork
        self.spawned += 1
        return fork

    def match(self, goal_id: str, patch: dict[str, Any]) -> Fork | None:
        """A ready fork whose hypothesis is exactly the correction the user made."""
        wanted = {k: v for k, v in patch.items() if not k.startswith("_")}
        for f in self.forks.values():
            if f.goal_id != goal_id or f.status != READY:
                continue
            if all(_same(wanted.get(k), v) for k, v in f.hypothesis.items()) and set(f.hypothesis) >= set(wanted):
                f.status = SERVED
                self.hits += 1
                return f
        return None

    def invalidate(self, goal_id: str, epochs: dict[str, int], changed: set[str]) -> None:
        """Kill forks whose unhypothesised basis changed."""
        for f in list(self.forks.values()):
            if f.goal_id != goal_id or f.status not in {RUNNING, READY}:
                continue
            if any(k not in f.hypothesis for k in changed) or any(
                    epochs.get(k, 0) != v for k, v in f.basis.items() if k not in f.hypothesis):
                self.kill(f)

    def kill(self, fork: Fork) -> None:
        if fork.task and not fork.task.done():
            fork.task.cancel()
        fork.status = DEAD
        self.killed += 1

    def kill_goal(self, goal_id: str) -> None:
        for f in list(self.forks.values()):
            if f.goal_id == goal_id and f.status in {RUNNING, READY}:
                self.kill(f)

    def tree(self) -> list[dict[str, Any]]:
        return [{"id": f.id, "goal": f.goal_id, "hypothesis": f.hypothesis, "status": f.status}
                for f in self.forks.values()]

    async def shutdown(self) -> None:
        tasks = [f.task for f in self.forks.values() if f.task and not f.task.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.forks.clear()


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b
