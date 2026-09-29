"""Tool manifest: schema-driven argument building, validation and routing.

Nothing here is hardcoded to a tool name except optional domain hints for the
five public tools; hidden tools are handled from their schema alone
(docs/kit/TOOLS.md). Saga tags are derived from each tool's `kind`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import entities as ent
from .nlu import Parse, content_words, stem

REVERSIBLE, COMPENSABLE, IRREVERSIBLE = "reversible", "compensable", "irreversible"
_GENERIC_VERBS = {"search", "lookup", "look", "get", "find", "fetch", "list", "check", "query", "quote", "create",
                  "make", "open", "submit", "request", "book", "cancel", "delete", "update", "set", "add", "remove",
                  "send", "retrieve", "show", "compute", "calculate", "estimate", "convert", "track", "info"}
_SYNONYMS: dict[str, set[str]] = {
    "weather": {"weather", "forecast", "temperature", "temp", "rain", "raining", "sunny", "snow", "hot", "cold", "humid", "wind", "umbrella", "degree"},
    "hotel": {"hotel", "stay", "room", "inn", "accommodation", "lodging", "motel", "resort", "hostel", "night"},
    "car": {"car", "rental", "rent", "vehicle", "suv", "drive", "sedan"},
    "rental": {"rental", "rent", "hire"},
    "flight": {"flight", "fly", "plane", "airline", "seat", "ticket"},
    "restaurant": {"restaurant", "dinner", "lunch", "eat", "food", "table", "reservation"},
    "currency": {"currency", "exchange", "convert", "rate", "dollar", "euro", "rupee"},
    "baggage": {"baggage", "luggage", "bag", "suitcase", "carry"},
    "seat": {"seat", "aisle", "window", "legroom"},
    "status": {"status", "delay", "delayed", "late", "on-time", "arrival", "departure"},
    "train": {"train", "rail", "railway"},
    "ticket": {"ticket", "case", "complaint", "report"},
    "manual": {"manual", "guide", "instruction", "documentation", "port", "how"},
    "translate": {"translate", "translation", "language", "say"},
    "stock": {"stock", "share", "price", "ticker", "market"},
    "news": {"news", "headline", "headlines"},
    "time": {"time", "clock", "timezone"},
    "refund": {"refund", "money", "reimburse", "back"},
    "upgrade": {"upgrade", "business", "first"},
    "insurance": {"insurance", "cover", "coverage", "policy"},
    "visa": {"visa", "passport", "entry"},
    "traffic": {"traffic", "commute", "route", "directions"},
    "reminder": {"reminder", "remind", "alarm"},
    "calendar": {"calendar", "event", "meeting", "schedule", "block"},
    "email": {"email", "mail", "message", "send"},
    "order": {"order", "package", "delivery", "shipment", "tracking"},
    "warranty": {"warranty", "guarantee", "coverage"},
    "appointment": {"appointment", "booking", "slot", "technician", "visit"},
    "parking": {"parking", "park", "garage"},
    "loyalty": {"loyalty", "points", "miles", "rewards"},
}
_ENUM_SYNONYMS = {
    "metric": {"celsius", "centigrade", "metric", "°c", "kilometers", "km"},
    "imperial": {"fahrenheit", "imperial", "°f", "miles"},
    "economy": {"economy", "cheap", "cheapest", "budget", "compact", "small"},
    "suv": {"suv", "big", "large", "family"},
    "luxury": {"luxury", "premium", "fancy", "nice"},
    "high": {"urgent", "high", "critical", "emergency", "severe", "asap", "serious"},
    "low": {"low", "minor", "small", "trivial", "cosmetic"},
    "medium": {"medium", "moderate", "normal"},
    "window": {"window"},
    "aisle": {"aisle"},
    "business": {"business"},
    "first": {"first class"},
}


@dataclass(frozen=True)
class ArgSpec:
    name: str
    type: str = "string"
    required: bool = False
    enum: tuple[Any, ...] = ()
    items: str | None = None
    properties: tuple["ArgSpec", ...] = ()
    description: str = ""

    @classmethod
    def parse(cls, name: str, raw: Any) -> "ArgSpec":
        if not isinstance(raw, dict):
            return cls(name=name)
        props = raw.get("properties") or {}
        items = raw.get("items")
        if isinstance(items, dict):
            items = items.get("type")
        return cls(
            name=name, type=str(raw.get("type") or "string"), required=bool(raw.get("required")),
            enum=tuple(raw.get("enum") or ()), items=items if isinstance(items, str) else None,
            properties=tuple(cls.parse(k, v) for k, v in props.items()) if isinstance(props, dict) else (),
            description=str(raw.get("description") or ""),
        )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str = "read_only"
    description: str = ""
    args: tuple[ArgSpec, ...] = ()
    delay_range_ms: tuple[float, float] = (500.0, 2000.0)
    default_result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, name: str, raw: Any) -> "ToolSpec":
        raw = raw if isinstance(raw, dict) else {}
        args = raw.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        rng = raw.get("delay_range_ms") or (500, 2000)
        try:
            lo, hi = float(rng[0]), float(rng[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 500.0, 2000.0
        default = raw.get("default_result")
        return cls(
            name=name, kind=str(raw.get("kind") or "read_only"), description=str(raw.get("description") or ""),
            args=tuple(ArgSpec.parse(k, v) for k, v in args.items()), delay_range_ms=(lo, hi),
            default_result=dict(default) if isinstance(default, dict) else {},
        )

    @property
    def state_modifying(self) -> bool:
        return self.kind == "state_modifying"

    @property
    def required(self) -> list[ArgSpec]:
        return [a for a in self.args if a.required]

    def arg(self, name: str) -> ArgSpec | None:
        return next((a for a in self.args if a.name == name), None)

    @property
    def name_words(self) -> list[str]:
        return [stem(w) for w in re.split(r"[_\-\s]+", self.name.lower()) if w]

    @property
    def verb(self) -> str:
        words = self.name.lower().split("_")
        for w in words:
            if w in _GENERIC_VERBS:
                return w
        return ""

    @property
    def noun(self) -> str:
        words = [w for w in self.name.lower().split("_") if w not in _GENERIC_VERBS]
        return " ".join(words) or self.name.replace("_", " ")


class Manifest:
    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self.tools: dict[str, ToolSpec] = {}
        for name, raw in (tools or {}).items():
            if isinstance(name, str) and name:
                self.tools[name] = ToolSpec.parse(name, raw)
        self._compensators = {t.name: self._find_compensator(t) for t in self.tools.values()}

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    def __bool__(self) -> bool:
        return bool(self.tools)

    def get(self, name: str) -> ToolSpec | None:
        return self.tools.get(name)

    # ---- saga tags -------------------------------------------------------
    def tag(self, name: str) -> str:
        spec = self.tools.get(name)
        if spec is None or not spec.state_modifying:
            return REVERSIBLE
        return COMPENSABLE if self._compensators.get(name) else IRREVERSIBLE

    def compensator(self, name: str) -> str | None:
        return self._compensators.get(name)

    def _find_compensator(self, spec: ToolSpec) -> str | None:
        if not spec.state_modifying:
            return None
        produced = set(spec.default_result) | {a.name for a in spec.args}
        if spec.name == "book_flight":
            produced |= {"booking_id"}
        # What the action creates, in words: its noun plus the thing its verb makes
        # ("book" makes a booking, "reserve" a reservation, "create_ticket" a ticket).
        made = {w[:5] for w in spec.noun.split()} | ({spec.verb[:4]} if spec.verb else set())
        made |= {k[:-3][:5] for k in spec.default_result if k.endswith("_id")}
        best = None
        for other in self.tools.values():
            if other.name == spec.name or not other.state_modifying:
                continue
            if not re.match(r"(?:cancel|delete|remove|revoke|void|undo|release|refund|close)(?:_|$)", other.name):
                continue
            needed = {a.name for a in other.required}
            if not needed or not needed <= produced:
                continue
            undoes = {w[:5] for w in other.noun.split()} | {w[:4] for w in other.noun.split()}
            if not (made & undoes):
                continue      # cancel_booking does not undo a seat upgrade
            overlap = len(made & undoes) + len(needed & set(spec.default_result))
            if best is None or overlap > best[0]:
                best = (overlap, other.name)
        if best is None and spec.name == "book_flight" and "cancel_booking" in self.tools:
            return "cancel_booking"
        return best[1] if best else None

    # ---- routing -----------------------------------------------------------
    def rank(self, parse: Parse, *, exclude: Iterable[str] = ()) -> list[tuple[float, str]]:
        """Score tools against an utterance using name, description and synonyms."""
        words = set(parse.content_words)
        raw_words = set(re.findall(r"[a-z]+", parse.text.lower()))
        scored = []
        for spec in self.tools.values():
            if spec.name in exclude:
                continue
            name_words = [w for w in spec.name_words if w not in {stem(v) for v in _GENERIC_VERBS}]
            score = 0.0
            for w in name_words:
                if w in words or w in raw_words:
                    score += 3.0
                syn = _SYNONYMS.get(w)
                if syn and (syn & raw_words or {stem(s) for s in syn} & words):
                    score += 2.0
            desc = set(content_words(spec.description)) - {stem(v) for v in _GENERIC_VERBS}
            score += 0.75 * len(desc & words)
            if score <= 0:
                continue
            # Fillability: required args we can supply make the tool more plausible.
            fillable = sum(1 for a in spec.required if _can_fill(a, parse))
            score += 0.5 * fillable - 0.25 * (len(spec.required) - fillable)
            scored.append((round(score, 3), spec.name))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return scored


def _can_fill(arg: ArgSpec, parse: Parse) -> bool:
    cls = arg_class(arg)
    if cls in {"place", "origin", "destination"}:
        return bool(parse.places)
    if cls == "date":
        return bool(parse.dates)
    if cls == "person":
        return bool(parse.person)
    if cls in {"query", "summary"}:
        return True
    if cls == "count":
        return bool(parse.counts) or bool(ent.find_numbers(parse.text))
    if arg.enum:
        return bool(enum_from_text(arg, parse.text))
    return False


def arg_class(arg: ArgSpec) -> str:
    n = arg.name.lower()
    d = arg.description.lower()
    if arg.type == "array" and (arg.items in {"number", "float", "integer"} or "embedding" in n or "vector" in n):
        return "embedding"
    if arg.type == "object":
        return "object"
    if re.search(r"(?:^|_)(?:origin|from|departure_city|source)(?:_|$)", n):
        return "origin"
    if re.search(r"(?:^|_)(?:destination|dest|to_city|arrival_city)(?:_|$)", n):
        return "destination"
    if re.search(r"city|location|place|town|airport|where|region|country|pickup|dropoff|address|area", n) or \
            (arg.type == "string" and re.search(r"\bcity\b|\blocation\b|\bairport\b", d) and "name" not in n):
        return "place"
    if re.search(r"date|day|when|check_?in|check_?out|depart(?:ure)?_on", n):
        return "date"
    if re.search(r"(?:^|_)time|hour", n):
        return "time"
    if n.endswith("_id") or n == "id":
        return "id"
    if re.search(r"passenger|guest_name|customer|traveller|traveler|full_name|contact_name|^name$|person|attendee", n):
        return "person"
    if re.search(r"severity|priority|urgency", n):
        return "severity"
    if re.search(r"model|device", n):
        return "model"
    if re.search(r"query|question|text|search|keyword|prompt|message|^q$|topic|term", n):
        return "query"
    if re.search(r"summary|description|issue|problem|reason|note|details|complaint", n):
        return "summary"
    if arg.type in {"number", "integer"} or re.search(r"nights|count|number|num_|quantity|qty|guests|passengers|rooms|days|adults|children|amount|size", n):
        return "count"
    if arg.type == "boolean":
        return "boolean"
    return "text"


def enum_from_text(arg: ArgSpec, text: str) -> Any:
    if not arg.enum:
        return None
    hit = ent.find_enum_mention(text, [v for v in arg.enum if isinstance(v, str)])
    if hit is not None:
        return hit
    low = text.lower()
    for value in arg.enum:
        syns = _ENUM_SYNONYMS.get(str(value).lower(), set())
        if any(re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", low) for s in syns):
            return value
    return None


@dataclass
class ArgContext:
    """Everything an argument may be drawn from, most specific first."""
    slots: dict[str, Any]
    text: str
    parse: Parse | None = None
    results: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    embedding: list[float] | None = None
    device_hint: str | None = None
    visual_subject: str | None = None
    query: str | None = None


def build_args(spec: ToolSpec, ctx: ArgContext) -> tuple[dict[str, Any], list[str]]:
    """Fill every argument we can justify; report required ones we cannot."""
    args: dict[str, Any] = {}
    missing: list[str] = []
    for arg in spec.args:
        value = _value_for(arg, ctx, spec)
        if value is None:
            if arg.required:
                missing.append(arg.name)
            continue
        args[arg.name] = value
    return args, missing


def _value_for(arg: ArgSpec, ctx: ArgContext, spec: ToolSpec) -> Any:
    slots = ctx.slots
    if arg.name in slots and slots[arg.name] not in (None, ""):
        return _coerce(arg, slots[arg.name])
    cls = arg_class(arg)
    text = ctx.text
    if arg.enum and cls not in {"model", "severity"}:
        hit = enum_from_text(arg, text)
        if hit is not None:
            return hit
        return None
    if cls == "embedding":
        return list(ctx.embedding) if ctx.embedding else None
    if cls == "object":
        sub_ctx = ctx
        obj: dict[str, Any] = {}
        for prop in arg.properties:
            v = _object_field(arg, prop, sub_ctx, spec)
            if v is None:
                if prop.required:
                    return None if arg.required is False else _partial(obj)
                continue
            obj[prop.name] = v
        return obj or None
    if cls == "origin":
        return slots.get("origin")
    if cls == "destination":
        return slots.get("destination") or slots.get("place") or slots.get("location")
    if cls == "place":
        for key in ("location", "place", "destination", "_last_place", "city"):
            if slots.get(key):
                return slots[key]
        return None
    if cls == "date":
        return slots.get("date")
    if cls == "time":
        pref = slots.get("flight_pref") or {}
        if isinstance(pref, dict) and pref.get("time") is not None:
            return ent.format_clock(pref["time"])
        return slots.get("time")
    if cls == "person":
        return slots.get("passenger_name") or slots.get("name")
    if cls == "id":
        return _id_value(arg, ctx)
    if cls == "severity":
        sev = slots.get("severity")
        if arg.enum:
            hit = enum_from_text(arg, text)
            if hit is not None:
                return hit
            if sev in arg.enum:
                return sev
            return "medium" if "medium" in arg.enum else (arg.enum[len(arg.enum) // 2] if arg.required else None)
        return sev
    if cls == "model":
        return _model_value(arg, ctx)
    if cls == "query":
        return ctx.query or _query_text(ctx)
    if cls == "summary":
        return slots.get("issue_summary") or (_query_text(ctx) if arg.required else None)
    if cls == "count":
        return _count_value(arg, ctx)
    if cls == "boolean":
        return None
    if arg.required and arg.type == "string":
        return None
    return None


def _partial(obj: dict[str, Any]) -> None:
    return None


def _object_field(parent: ArgSpec, prop: ArgSpec, ctx: ArgContext, spec: ToolSpec) -> Any:
    name = prop.name.lower()
    slots = ctx.slots
    if parent.name.lower() in {"device"} or "device" in parent.name.lower():
        if name in {"model", "device_model"}:
            return _model_value(prop, ctx)
        if name in {"serial", "serial_number"}:
            return slots.get("serial")
    if parent.name.lower() in {"issue", "problem"} or "issue" in parent.name.lower():
        if name in {"summary", "description", "title"}:
            return slots.get("issue_summary") or (_query_text(ctx) if prop.required else None)
        if name in {"severity", "priority"}:
            return _value_for(ArgSpec(name="severity", type=prop.type, required=prop.required, enum=prop.enum), ctx, spec)
    return _value_for(prop, ctx, spec)


def _model_value(arg: ArgSpec, ctx: ArgContext) -> Any:
    slots = ctx.slots
    options = [str(v) for v in arg.enum] if arg.enum else []
    candidates = []
    if slots.get("device_model"):
        candidates.append(str(slots["device_model"]))
    if ctx.parse:
        candidates.extend(ctx.parse.model_tokens)
    for c in candidates:
        if not options:
            return c
        for o in options:
            if c.upper() == o.upper() or o.upper() in c.upper():
                return o
    if options:
        hit = enum_from_text(arg, ctx.text)
        if hit is not None:
            return hit
        if ctx.device_hint and ctx.device_hint in options:
            return ctx.device_hint
        return None
    return slots.get("device") if arg.required else None


def _id_value(arg: ArgSpec, ctx: ArgContext) -> Any:
    name = arg.name
    if ctx.slots.get(name):
        return ctx.slots[name]
    for tool, result in reversed(ctx.results):
        if isinstance(result, dict) and result.get(name):
            return result[name]
    ids = ent.GENERIC_ID_RE.findall(ctx.text)
    return ids[-1] if ids else None


def _count_value(arg: ArgSpec, ctx: ArgContext) -> Any:
    n = arg.name.lower()
    parse = ctx.parse
    unit_map = {"night": "night", "guest": "passenger", "passenger": "passenger", "room": "room", "day": "day",
                "adult": "passenger", "people": "passenger", "bag": "bag", "hour": "hour", "week": "week"}
    wanted = next((u for k, u in unit_map.items() if k in n), None)
    if parse:
        for c in reversed(parse.counts):
            if wanted is None or c.unit == wanted:
                return _coerce(arg, c.value)
    for key in ("passengers", "nights", "rooms", "days"):
        if wanted and key.startswith(wanted) and ctx.slots.get(key) is not None:
            return _coerce(arg, ctx.slots[key])
    if arg.required:
        nums = ent.find_numbers(ctx.text)
        if nums:
            return _coerce(arg, nums[-1])
    return None


def _query_text(ctx: ArgContext) -> str:
    base = ctx.parse.effective if ctx.parse else ctx.text
    base = re.sub(r"^\W*(?:hey|hi|so|um+|uh+|okay|ok|well|please)[,\s]+", "", base, flags=re.I).strip()
    if ctx.visual_subject:
        return f"{ctx.visual_subject}: {base}" if base else ctx.visual_subject
    return base


def _coerce(arg: ArgSpec, value: Any) -> Any:
    if arg.type in {"number", "integer"}:
        try:
            f = float(value)
            return int(f) if arg.type == "integer" or f == int(f) else f
        except (TypeError, ValueError):
            return None
    if arg.type == "string" and not isinstance(value, str):
        if isinstance(value, (dict, list)):
            return None
        return str(value)
    if arg.enum and value not in arg.enum:
        for e in arg.enum:
            if str(e).lower() == str(value).lower():
                return e
        return None
    return value


def validate(spec: ToolSpec, args: dict[str, Any]) -> list[str]:
    """Mirror of the kit's validation so we never burn time on invalid_args."""
    problems = []
    if not isinstance(args, dict):
        return ["args must be an object"]
    for arg in spec.args:
        if arg.required and arg.name not in args:
            problems.append(f"missing required arg '{arg.name}'")
            continue
        if arg.name not in args:
            continue
        val = args[arg.name]
        if arg.enum and val not in arg.enum:
            problems.append(f"arg '{arg.name}' must be one of {list(arg.enum)}")
        if arg.type == "object":
            if not isinstance(val, dict):
                problems.append(f"arg '{arg.name}' must be an object")
            else:
                for sub in arg.properties:
                    if sub.required and sub.name not in val:
                        problems.append(f"missing required field '{arg.name}.{sub.name}'")
                    elif sub.name in val and sub.enum and val[sub.name] not in sub.enum:
                        problems.append(f"field '{arg.name}.{sub.name}' must be one of {list(sub.enum)}")
        if arg.type == "array" and not isinstance(val, list):
            problems.append(f"arg '{arg.name}' must be an array")
        if arg.type == "string" and not isinstance(val, str):
            problems.append(f"arg '{arg.name}' must be a string")
        if arg.type in {"number", "integer"} and (isinstance(val, bool) or not isinstance(val, (int, float))):
            problems.append(f"arg '{arg.name}' must be a number")
        if arg.type == "boolean" and not isinstance(val, bool):
            problems.append(f"arg '{arg.name}' must be a boolean")
    return problems


def normalized_key(tool: str, args: dict[str, Any]) -> str:
    """Idempotency key: same tool, same normalised arguments."""
    import json

    def norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: norm(x) for k, x in sorted(v.items())}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return str(v).strip().lower()

    return tool + "|" + json.dumps(norm(args), sort_keys=True)
