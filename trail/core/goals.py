"""Goal stack and planner.

A plan is re-derived from a goal's slots on every change and reconciled
against live calls, so a correction re-runs only the steps whose arguments
actually changed (slot dependencies), and a resumed goal reuses cached results
(checkpoints) instead of redoing retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import entities as ent
from .nlu import Parse
from .tools import ArgContext, Manifest, ToolSpec, build_args, normalized_key, validate

ACTIVE, WAITING, PARKED, DONE, ABANDONED = "active", "waiting", "parked", "done", "abandoned"
# Intents understood natively; anything else is a manifest tool name.
FLIGHT_INTENTS = {"flight_search", "book_flight"}
DEVICE_INTENTS = {"device_support", "create_support_ticket"}
TALK_INTENTS = {"capabilities", "thanks", "chitchat", "none", "clarify_answer", "unknown"}


@dataclass
class Question:
    goal_id: str
    slots: tuple[str, ...]            # "destination", "passenger_name", "flight_choice", "confirm:<tool>", "arg:<name>"
    text: str
    candidates: tuple[str, ...] = ()
    turn: int = 0


@dataclass
class Goal:
    id: str
    intent: str
    domain: str
    slots: dict[str, Any] = field(default_factory=dict)
    status: str = ACTIVE
    text: str = ""
    parse: Parse | None = None
    tool: str | None = None                      # primary manifest tool for generic goals
    authorized: set[str] = field(default_factory=set)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)   # normalised call key -> result
    failed: dict[str, str] = field(default_factory=dict)               # call key -> terminal error code
    attempts: dict[str, int] = field(default_factory=dict)             # call key -> calls made
    step_calls: dict[str, str] = field(default_factory=dict)           # step -> live call_id
    superseded: dict[str, set[str]] = field(default_factory=dict)      # slot -> values the user abandoned
    answer: str | None = None
    answered_key: str | None = None
    question: Question | None = None
    epochs: dict[str, int] = field(default_factory=dict)
    created_turn: int = 0
    awaiting_media: str | None = None
    offered_back: bool = False

    def set_slot(self, name: str, value: Any) -> bool:
        if value in (None, "") or self.slots.get(name) == value:
            return False
        old = self.slots.get(name)
        if old not in (None, "") and isinstance(old, str):
            self.superseded.setdefault(name, set()).add(old.lower())
        self.slots[name] = value
        self.epochs[name] = self.epochs.get(name, 0) + 1
        return True

    def snapshot_slots(self) -> dict[str, Any]:
        out = {}
        for k, v in self.slots.items():
            if k.startswith("_") or k in {"flight_pref", "severity", "place", "location", "device"}:
                continue
            if isinstance(v, (str, int, float, bool)):
                out[k] = v
        return out


class GoalStack:
    """Park, resume, patch a single slot. The top active/waiting goal is current."""

    def __init__(self) -> None:
        self.goals: list[Goal] = []
        self._seq = 0

    def new_id(self) -> str:
        self._seq += 1
        return f"g{self._seq}"

    @property
    def current(self) -> Goal | None:
        for g in reversed(self.goals):
            if g.status in {ACTIVE, WAITING}:
                return g
        return None

    @property
    def last(self) -> Goal | None:
        return self.goals[-1] if self.goals else None

    def push(self, goal: Goal) -> Goal:
        self.goals.append(goal)
        return goal

    def park(self, goal: Goal) -> None:
        if goal.status in {ACTIVE, WAITING}:
            goal.status = PARKED

    def parked(self) -> list[Goal]:
        return [g for g in self.goals if g.status == PARKED]

    def resume(self, goal: Goal) -> Goal:
        self.goals.remove(goal)
        goal.status = ACTIVE
        self.goals.append(goal)
        return goal

    def find_parked(self, hint: str | None) -> Goal | None:
        parked = self.parked() or [g for g in self.goals[:-1] if g.status == DONE][-3:]
        if not parked:
            return None
        if hint:
            h = hint.lower().rstrip("s")
            for g in reversed(parked):
                hay = f"{g.intent} {g.domain} {g.tool or ''} {g.text}".lower()
                if h in hay or (h in {"flight", "trip", "booking", "search"} and g.domain == "flight"):
                    return g
        return parked[-1]

    def clear(self) -> None:
        self.goals.clear()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
@dataclass
class Step:
    name: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    missing: tuple[str, ...] = ()
    needs_auth: bool = False
    wait_for: str | None = None

    @property
    def key(self) -> str | None:
        return normalized_key(self.tool, self.args or {}) if self.tool else None


@dataclass
class Plan:
    steps: list[Step]
    derived: dict[str, Any] = field(default_factory=dict)
    question: tuple[tuple[str, ...], str, tuple[str, ...]] | None = None   # (slots, text, candidates)
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanEnv:
    manifest: Manifest
    embedding: list[float] | None = None
    device_hint: str | None = None
    visual_subject: str | None = None
    vision_pending: bool = False
    has_frame: bool = False
    bookings: list[dict[str, Any]] = field(default_factory=list)


def _find_tool(manifest: Manifest, *words: str, kind: str | None = None) -> ToolSpec | None:
    for spec in manifest.tools.values():
        if all(w in spec.name for w in words) and (kind is None or spec.kind == kind):
            return spec
    return None


def plan(goal: Goal, env: PlanEnv) -> Plan:
    if goal.intent in FLIGHT_INTENTS:
        return _plan_flight(goal, env)
    if goal.intent == "cancel_booking":
        return _plan_cancel(goal, env)
    if goal.intent in DEVICE_INTENTS:
        return _plan_device(goal, env)
    if goal.intent == "trail_book":
        return _plan_chain(goal, env, (("hold", "hold_fare", False), ("book", "book_fare", True), ("pay", "pay_booking", True)))
    if goal.tool and goal.tool in env.manifest:
        return _plan_tool(goal, env, env.manifest.tools[goal.tool])
    return Plan(steps=[])


def _plan_chain(goal: Goal, env: PlanEnv, chain) -> Plan:
    """A chain of tools where later steps take ids and amounts from earlier results."""
    steps: list[Step] = []
    extra: dict[str, Any] = {}
    for name, tool, auth in chain:
        spec = env.manifest.get(tool)
        if spec is None:
            continue
        args, missing = build_args(spec, _ctx(goal, env, extra))
        step = Step(name, tool, args, tuple(missing), needs_auth=auth)
        steps.append(step)
        if missing:
            return Plan(steps, extra, question=(tuple(f"arg:{a}" for a in missing), "", ()))
        result = goal.results.get(step.key or "")
        if result is None:
            return Plan(steps, extra)
        for k, v in result.items():
            if k != "status" and (k.endswith("_id") or k.startswith("amount")):
                extra[k] = v
    return Plan(steps, extra)


def _ctx(goal: Goal, env: PlanEnv, extra: dict[str, Any] | None = None, query: str | None = None) -> ArgContext:
    slots = dict(goal.slots)
    if extra:
        slots.update(extra)
    results = [(k.split("|", 1)[0], v) for k, v in goal.results.items()]
    return ArgContext(slots=slots, text=goal.text, parse=goal.parse, results=results, embedding=env.embedding,
                      device_hint=env.device_hint, visual_subject=env.visual_subject, query=query)


def flights_from(result: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("flights", "results", "options", "items"):
        v = result.get(key)
        if isinstance(v, list):
            return [f for f in v if isinstance(f, dict)]
    return []


def _price(item: dict[str, Any]) -> float | None:
    for k, v in item.items():
        if re.search(r"price|fare|cost|amount|total", k) and isinstance(v, (int, float)):
            return float(v)
    return None


def _depart(item: dict[str, Any]) -> int | None:
    for k in ("depart", "departure", "depart_time", "time", "departs"):
        if item.get(k):
            m = ent.parse_clock(str(item[k]))
            if m is not None:
                return m
    fid = str(item.get("flight_id", ""))
    m = re.search(r"(\d{1,2})(AM|PM)$", fid, re.I)
    if m:
        return (int(m[1]) % 12 + (12 if m[2].upper() == "PM" else 0)) * 60
    return None


def choose(options: list[dict[str, Any]], pref: dict[str, Any] | None, explicit_id: str | None = None) -> dict[str, Any] | None:
    if not options:
        return None
    if explicit_id:
        for o in options:
            if str(o.get("flight_id", "")).upper() == explicit_id.upper():
                return o
    pref = pref or {}
    if pref.get("time") is not None:
        exact = [o for o in options if _depart(o) == pref["time"]]
        if exact:
            return exact[0]
        timed = [o for o in options if _depart(o) is not None]
        if timed:
            return min(timed, key=lambda o: abs(_depart(o) - pref["time"]))
    if pref.get("period"):
        for o in options:
            d = _depart(o)
            if d is not None and ent.in_period(d, pref["period"]):
                return o
    rank = pref.get("rank")
    if rank == "cheapest":
        priced = [o for o in options if _price(o) is not None]
        if priced:
            return min(priced, key=_price)
    if rank in {"earliest", "latest"}:
        timed = [o for o in options if _depart(o) is not None]
        if timed:
            return (min if rank == "earliest" else max)(timed, key=_depart)
    if "ordinal" in pref:
        idx = pref["ordinal"]
        if -len(options) <= idx < len(options):
            return options[idx]
    if len(options) == 1:
        return options[0]
    return None


def _plan_flight(goal: Goal, env: PlanEnv) -> Plan:
    m = env.manifest
    search = m.get("flight_search") or _find_tool(m, "flight", "search") or _find_tool(m, "search", "flight")
    book = m.get("book_flight") or _find_tool(m, "book", kind="state_modifying")
    steps: list[Step] = []
    derived: dict[str, Any] = {}
    options: list[dict[str, Any]] = []
    wants_booking = goal.intent == "book_flight"
    explicit_id = goal.slots.get("flight_id") if goal.slots.get("_flight_id_explicit") else None

    if search is None and not explicit_id:
        return Plan(steps=[])
    if not explicit_id and search is not None:
        args, missing = build_args(search, _ctx(goal, env))
        step = Step("search", search.name, args, tuple(missing))
        steps.append(step)
        if missing:
            return Plan(steps, question=(("destination",), "", ()))
        result = goal.results.get(step.key or "")
        if result is None:
            return Plan(steps)
        options = flights_from(result)
    if wants_booking and book is not None:
        picked = choose(options, goal.slots.get("flight_pref"), explicit_id or goal.slots.get("flight_id"))
        if picked is not None and picked.get("flight_id"):
            derived["flight_id"] = picked["flight_id"]
        elif explicit_id:
            derived["flight_id"] = explicit_id
        args, missing = build_args(book, _ctx(goal, env, derived))
        step = Step("book", book.name, args, tuple(missing), needs_auth=True)
        steps.append(step)
        if missing:
            wanted = []
            if "flight_id" in missing:
                wanted.append("flight_choice")
            if "passenger_name" in missing:
                wanted.append("passenger_name")
            wanted += [f"arg:{a}" for a in missing if a not in {"flight_id", "passenger_name"}]
            return Plan(steps, derived, question=(tuple(wanted), "", ()), options=options)
    elif options:
        picked = choose(options, goal.slots.get("flight_pref"), goal.slots.get("flight_id"))
        if picked is not None and goal.slots.get("flight_pref"):
            derived["flight_id"] = picked.get("flight_id")
    return Plan(steps, derived, options=options)


def _plan_cancel(goal: Goal, env: PlanEnv) -> Plan:
    tool = env.manifest.get("cancel_booking") or _find_tool(env.manifest, "cancel", kind="state_modifying")
    if tool is None:
        return Plan(steps=[])
    extra = {}
    if not goal.slots.get("booking_id") and len(env.bookings) == 1:
        extra["booking_id"] = env.bookings[0].get("booking_id")
    args, missing = build_args(tool, _ctx(goal, env, extra))
    step = Step("cancel", tool.name, args, tuple(missing), needs_auth=True)
    if missing:
        return Plan([step], extra, question=(("booking_id",), "", ()))
    return Plan([step], extra)


def visual_query(goal: Goal, env: PlanEnv) -> str:
    base = goal.parse.effective if goal.parse else goal.text
    base = re.sub(r"^\W*(?:hey|hi|so|um+|uh+|okay|ok|well|please)[,\s]+", "", base, flags=re.I).strip()
    base = re.sub(r"^(?:forget|never\s*mind|scratch|drop)\s+(?:about\s+)?(?:the|my|that|this)\s+\w+[,.;]?\s*", "", base, flags=re.I).strip()
    subject = env.visual_subject
    if subject:
        # Replace the deictic with what the frame shows: "what is this port" -> "what is the HDMI port".
        replaced = re.sub(r"\b(this|that|these|those|the)\s+(port|button|light|cable|thing|part|one|connector|jack|slot|icon|led)\b",
                          lambda mm: f"the {subject}" if subject.lower().split()[-1] in {"port", "button", "light", "cable", "connector", "jack", "slot", "icon", "led"} else f"the {subject} {mm[2]}",
                          base, count=1, flags=re.I)
        if replaced == base:
            replaced = f"{subject}: {base}"
        return replaced
    return base


def _plan_device(goal: Goal, env: PlanEnv) -> Plan:
    m = env.manifest
    steps: list[Step] = []
    if goal.intent == "create_support_ticket":
        tool = m.get("create_support_ticket") or _find_tool(m, "ticket", kind="state_modifying")
        if tool is None:
            return Plan(steps=[])
        args, missing = build_args(tool, _ctx(goal, env))
        problems = validate(tool, args) if not missing else []
        if problems and not missing:
            missing = [p.split("'")[1] if "'" in p else p for p in problems]
        step = Step("ticket", tool.name, args, tuple(missing), needs_auth=True)
        if missing:
            want = []
            for a in missing:
                if "device" in a or "model" in a:
                    want.append("device_model")
                elif "issue" in a or "summary" in a:
                    want.append("issue_summary")
                else:
                    want.append(f"arg:{a}")
            return Plan([step], question=(tuple(dict.fromkeys(want)), "", ()))
        return Plan([step])
    tool = m.get("lookup_manual") or _find_tool(m, "manual") or _find_tool(m, "lookup", kind="read_only")
    if tool is None:
        return Plan(steps=[])
    needs_vision = env.has_frame and (goal.parse is None or goal.parse.deictic or not goal.parse.model_tokens)
    if needs_vision and env.vision_pending:
        return Plan([Step("lookup", tool.name, None, wait_for="vision")])
    query = visual_query(goal, env)
    args, missing = build_args(tool, _ctx(goal, env, query=query))
    step = Step("lookup", tool.name, args, tuple(missing))
    if missing:
        return Plan([step], question=(tuple(f"arg:{a}" for a in missing), "", ()))
    return Plan([step])


def _plan_tool(goal: Goal, env: PlanEnv, spec: ToolSpec) -> Plan:
    args, missing = build_args(spec, _ctx(goal, env))
    step = Step("call", spec.name, args, tuple(missing), needs_auth=spec.state_modifying)
    if missing:
        return Plan([step], question=(tuple(f"arg:{a}" for a in missing), "", ()))
    return Plan([step])
