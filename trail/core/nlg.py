"""Phrasing. Content-aware, truthful, never ahead of the tool timeline.

Deterministic templates keep the harness reproducible (3 reps, median). An
optional model polish can be layered on top by the runtime, never below.
"""

from __future__ import annotations

import re
from typing import Any

from . import entities as ent
from .goals import Goal, Plan, flights_from
from .tools import Manifest, ToolSpec

_NEUTRAL_ACKS = ("One moment.", "Just a second.", "Okay, one sec.", "Sure, give me a moment.")


def neutral_ack(n: int) -> str:
    return _NEUTRAL_ACKS[n % len(_NEUTRAL_ACKS)]


def _date_phrase(date: str | None) -> str:
    if not date:
        return ""
    d = str(date)
    if d.lower() in {"today", "tonight", "tomorrow", "this weekend", "next weekend", "next week", "this week",
                     "day after tomorrow", "tomorrow morning", "tomorrow night"}:
        return f" {d.lower()}"
    if re.match(r"(?:next )?(?:mon|tue|wed|thu|fri|sat|sun)", d, re.I):
        return f" on {d}"
    return f" on {d}"


def _price_text(item: dict[str, Any]) -> str | None:
    for k, v in item.items():
        if isinstance(v, (int, float)) and re.search(r"price|fare|cost|amount|total", k):
            cur = "USD" if k.endswith("usd") else "INR" if k.endswith(("inr", "rupees")) else \
                "EUR" if k.endswith("eur") else "GBP" if k.endswith("gbp") else "USD"
            return ent.format_price(float(v), cur)
    return None


def _time_text(item: dict[str, Any]) -> str | None:
    for k in ("depart", "departure", "depart_time", "time"):
        if item.get(k):
            m = ent.parse_clock(str(item[k]))
            return ent.format_clock(m) if m is not None else str(item[k])
    return None


def flight_line(item: dict[str, Any]) -> str:
    fid = item.get("flight_id") or item.get("id") or "a flight"
    parts = [str(fid)]
    t = _time_text(item)
    if t:
        parts.append(f"at {t}")
    p = _price_text(item)
    if p:
        parts.append(f"for {p}")
    return " ".join(parts)


def _join(items: list[str], conj: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


# ---------------------------------------------------------------------------
# Acknowledgements (fillers). Short, content-aware, future tense.
# ---------------------------------------------------------------------------
def ack_for_goal(goal: Goal, manifest: Manifest, *, reason: str = "new", changed: str | None = None) -> str:
    s = goal.slots
    dest = s.get("destination")
    date = _date_phrase(s.get("date"))
    if reason == "retry":
        noun = _tool_noun(manifest.get(goal.tool or "") if goal.tool else None, goal)
        return f"The {noun} timed out, so I'm trying it once more."
    if reason == "correction" and changed:
        value = s.get(changed)
        if changed == "destination" and dest:
            return f"Got it, {dest} instead. Checking flights there now."
        if changed == "date" and s.get("date"):
            return f"Okay, {s.get('date')} instead. Rechecking flights now."
        if changed == "passenger_name" and value:
            return f"Sure, I'll put it under {value} instead."
        if changed == "flight_pref":
            return "Okay, switching flights. One moment."
        if changed == "device_model" and value:
            return f"Got it, the {value}. Checking that now."
        if value not in (None, ""):
            return f"Got it, {value} instead. Updating that now."
        return "Got it, updating that now."
    if reason == "addition" and changed:
        value = s.get(changed)
        label = changed.replace("_", " ")
        if changed == "passengers":
            return f"Noted, {value} passengers. I'll factor that in."
        return f"Noted, {label} {value}. I'll factor that in." if value not in (None, "") else "Noted, I'll factor that in."
    if reason == "resume":
        return f"Back to {_goal_label(goal)}. Picking up where we left off."
    if goal.intent in {"flight_search", "book_flight"}:
        if goal.intent == "book_flight" and dest:
            pref = s.get("flight_pref") or {}
            who = s.get("passenger_name")
            which = ""
            if pref.get("time") is not None:
                which = f" the {ent.format_clock(pref['time'])} one"
            elif pref.get("rank") == "cheapest":
                which = " the cheapest one"
            if which and who:
                return f"Searching flights to {dest}{date}, then I'll book{which} for {who}."
            return f"Looking up flights to {dest}{date} for you now."
        if dest:
            return f"Checking flights to {dest}{date}."
        return "Let me look up those flights."
    if goal.intent == "trail_book":
        day = s.get("day") or "that"
        pax = int(s.get("passengers") or 1)
        who = f" for {pax}" if pax > 1 else ""
        return f"I'll hold {day}'s fare and book it{who} now."
    if goal.intent == "cancel_booking":
        bid = s.get("booking_id")
        return f"I'll cancel booking {bid} now." if bid else "I'll cancel that booking now."
    if goal.intent == "create_support_ticket":
        model = s.get("device_model") or s.get("device")
        return f"I'll open a support ticket for your {model} now." if model else "I'll open a support ticket for that now."
    if goal.intent == "device_support":
        return "Let me check the manual for that."
    spec = manifest.get(goal.tool) if goal.tool else None
    return _tool_ack(spec, goal)


def _tool_noun(spec: ToolSpec | None, goal: Goal) -> str:
    if goal.intent in {"flight_search", "book_flight"}:
        return "flight search"
    if goal.intent == "device_support":
        return "manual lookup"
    if spec is None:
        return "request"
    return spec.name.replace("_", " ")


def _subject(spec: ToolSpec | None, goal: Goal) -> str | None:
    if spec is None:
        return None
    for a in spec.required + list(spec.args):
        v = goal.slots.get(a.name)
        if v is None:
            for key in ("location", "place", "destination", "_last_place"):
                if key in goal.slots and re.search(r"city|location|place|town|airport|where|pickup", a.name):
                    v = goal.slots[key]
                    break
        if isinstance(v, str) and v:
            return v
    return None


def _tool_ack(spec: ToolSpec | None, goal: Goal) -> str:
    if spec is None:
        return "Let me check that for you."
    subj = _subject(spec, goal)
    noun = spec.noun
    where = f" in {subj}" if subj and _is_place(subj) else (f" for {subj}" if subj else "")
    verb = spec.verb
    if spec.state_modifying:
        return f"I'll {verb or 'take care of'} that {noun} now." if verb else f"I'll take care of that {noun} now."
    if verb in {"quote", "estimate"} or "quote" in spec.name:
        return f"Getting a {noun} quote{where}."
    if verb in {"search", "find", "list"}:
        return f"Searching {noun}s{where}." if not noun.endswith("s") else f"Searching {noun}{where}."
    return f"Checking the {noun}{where}."


def _is_place(v: str) -> bool:
    return bool(ent.canonical_city(v)) or bool(re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)*", v))


def _goal_label(goal: Goal) -> str:
    if goal.domain == "flight":
        dest = goal.slots.get("destination")
        return f"the flights to {dest}" if dest else "the flights"
    if goal.domain == "device":
        return "your device question"
    if goal.domain == "trail":
        return "the fares you were comparing"
    return f"the {goal.tool.replace('_', ' ')}" if goal.tool else "what we were doing"


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
def question_text(goal: Goal, slots: tuple[str, ...], plan: Plan | None, manifest: Manifest,
                  candidates: tuple[str, ...] = ()) -> str:
    wanted = list(slots)
    if wanted == ["destination"] and candidates:
        if len(candidates) >= 2:
            return f"Sorry, did you say {candidates[0]} or {candidates[1]}?"
        return f"Just to confirm, did you say {candidates[0]}?"
    if wanted == ["destination"]:
        return "Sure, which city would you like to fly to?"
    parts = []
    options = plan.options if plan else []
    if "flight_choice" in wanted:
        if options:
            choices = [f"the {_time_text(o)}" if _time_text(o) else str(o.get("flight_id")) for o in options[:3]]
            parts.append(f"Which one should I book, {_join(choices, 'or')}")
        else:
            parts.append("Which flight should I book")
    if "passenger_name" in wanted:
        parts.append("what name should the booking be under" if parts else "What name should I book it under")
    if parts:
        return _cap(" and ".join(parts)) + "?"
    if "device_model" in wanted:
        dev = goal.slots.get("device") or "device"
        return f"What's the model of your {dev}?"
    if "issue_summary" in wanted:
        return "What's going wrong with it?"
    if "booking_id" in wanted:
        return "Which booking should I cancel? Please tell me the booking ID."
    for w in wanted:
        if w.startswith("confirm:"):
            tool = w.split(":", 1)[1].replace("_", " ")
            return f"Should I go ahead with the {tool}?"
    arg_q = []
    spec = manifest.get(goal.tool) if goal.tool else None
    for w in wanted:
        if w.startswith("arg:"):
            name = w[4:]
            desc = ""
            if spec and spec.arg(name):
                desc = spec.arg(name).description.strip().rstrip(".")
            arg_q.append(_arg_question(name, desc, spec.arg(name) if spec else None))
    if arg_q:
        return arg_q[0] if len(arg_q) == 1 else _cap(_join([q.rstrip("?").lower() for q in arg_q])) + "?"
    return "Could you tell me a bit more about what you need?"


def _arg_question(name: str, desc: str, arg) -> str:
    n = name.replace("_", " ")
    if re.search(r"city|location|place|pickup|airport", name):
        return "Which city should I use?"
    if re.search(r"date|day", name):
        return "For which date?"
    if re.search(r"name|passenger|guest", name):
        return "What name should I use?"
    if arg is not None and arg.enum:
        return f"Which {n} would you like: {_join([str(e) for e in arg.enum], 'or')}?"
    if desc:
        return f"What should I use for the {n} ({desc[:60].lower()})?"
    return f"What {n} should I use?"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ---------------------------------------------------------------------------
# Final answers, grounded in tool results
# ---------------------------------------------------------------------------
def flight_answer(goal: Goal, plan: Plan, booked: dict[str, Any] | None, *, question: str | None) -> str:
    s = goal.slots
    dest = s.get("destination") or "your destination"
    date = _date_phrase(s.get("date"))
    options = plan.options
    if booked:
        fid = booked.get("flight_id") or s.get("flight_id")
        bid = booked.get("booking_id")
        item = next((o for o in options if o.get("flight_id") == fid), None)
        when = f" at {_time_text(item)}" if item and _time_text(item) else ""
        who = f" for {s.get('passenger_name')}" if s.get("passenger_name") else ""
        ref = f" Your booking ID is {bid}." if bid else ""
        return f"Done. {fid} to {dest}{when} is booked{who}.{ref}"
    if not options:
        return f"I didn't find any flights to {dest}{date}. Want me to try a different date?"
    lines = [flight_line(o) for o in options[:3]]
    head = f"I found {len(options)} flight{'s' if len(options) != 1 else ''} to {dest}{date}: {_join(lines, 'or')}."
    cheapest = None
    priced = [o for o in options if _price_text(o)]
    if len(priced) > 1:
        def amount(o):
            return next((float(v) for k, v in o.items() if isinstance(v, (int, float)) and re.search(r"price|fare|cost", k)), 1e18)
        cheapest = min(priced, key=amount)
    chosen = s.get("flight_id")
    tail = ""
    if chosen and s.get("flight_pref") and goal.intent == "flight_search":
        tail = f" {chosen} matches what you asked for."
    elif cheapest is not None:
        t = _time_text(cheapest)
        tail = f" The {t} one is the cheapest." if t else f" {cheapest.get('flight_id')} is the cheapest."
    if question:
        return f"{head}{tail} {question}"
    return head + tail


_PORT_USES = {
    "hdmi": "sending video and audio to an external monitor, TV or projector",
    "usb-c": "charging, data and connecting displays or docks",
    "thunderbolt": "fast data, displays and charging",
    "usb": "connecting accessories like flash drives, keyboards and mice",
    "ethernet": "a wired network connection",
    "lan": "a wired network connection",
    "headphone": "headphones, a headset or external speakers",
    "audio": "headphones, a headset or external speakers",
    "displayport": "connecting an external monitor",
    "vga": "connecting older monitors and projectors",
    "sd": "reading memory cards from cameras",
    "power": "charging the device",
}


def manual_answer(goal: Goal, result: dict[str, Any], subject: str | None, device_model: str | None) -> str:
    pages = [p for p in result.get("pages", []) if isinstance(p, dict)]
    if not pages:
        return ("I couldn't find anything about that in the device manuals. "
                "Could you tell me the device model, or describe what you're seeing?")
    subj_words = [w for w in re.findall(r"[a-z0-9-]+", (subject or "").lower()) if w not in {"port", "the", "a", "button", "light"}]
    relevant = [p for p in pages if subj_words and any(w in str(p.get("title", "")).lower() for w in subj_words)]
    if not relevant:
        relevant = pages[:1]
    if device_model:
        relevant.sort(key=lambda p: 0 if device_model.lower() in str(p.get("doc", "")).lower() else 1)
    best = relevant[0]
    title = best.get("title", "")
    doc = str(best.get("doc", "the manual")).replace("-", " ")
    page = best.get("page")
    cite = f"the {doc}, page {page}" if page is not None else f"the {doc}"
    use = None
    key_text = f"{subject or ''} {title}".lower()
    for key, text in _PORT_USES.items():
        if key in key_text:
            use = text
            break
    if subject and use:
        return f"That's the {subject.strip()}. It's used for {use}. The {doc} covers it on page {page}, “{title}”."
    if subject:
        return f"That looks like the {subject.strip()}. The {doc} covers it on page {page}, “{title}”."
    return f"The {doc} covers this on page {page}, under “{title}”." if page is not None else f"Check “{title}” in the {doc}."


def ticket_answer(goal: Goal, result: dict[str, Any], args: dict[str, Any]) -> str:
    tid = result.get("ticket_id") or next((v for k, v in result.items() if k.endswith("_id")), None)
    dev = (args.get("device") or {}).get("model") if isinstance(args.get("device"), dict) else None
    issue = args.get("issue") if isinstance(args.get("issue"), dict) else {}
    summary = issue.get("summary")
    sev = issue.get("severity")
    bits = []
    if dev:
        bits.append(f"for your {dev}")
    detail = f" ({summary}{', ' + sev + ' priority' if sev else ''})" if summary else ""
    ref = f" ticket {tid}" if tid else " a support ticket"
    return f"I've opened{ref} {' '.join(bits)}{detail}. Support will follow up with you.".replace("  ", " ")


def cancel_answer(result: dict[str, Any], args: dict[str, Any]) -> str:
    bid = result.get("cancelled") or args.get("booking_id")
    return f"Booking {bid} has been cancelled." if bid else "That booking has been cancelled."


def error_answer(goal: Goal, code: str | None, *, state_modifying: bool, manifest: Manifest) -> str:
    what = _tool_noun(manifest.get(goal.tool or "") if goal.tool else None, goal)
    dest = goal.slots.get("destination")
    where = f" to {dest}" if dest and goal.domain == "flight" else ""
    if state_modifying and code not in {"invalid_args", "not_found", "unknown_tool"}:
        return (f"Sorry, the {what} request timed out and I can't confirm whether it went through. "
                "I won't retry it blindly. Want me to check on it, or try again?")
    if code == "not_found":
        return f"Sorry, I couldn't find that. Could you double-check the details?"
    if code == "invalid_args":
        return f"Sorry, I was unable to complete the {what}: some details were missing or invalid. Could you rephrase?"
    return f"Sorry, I was unable to reach the {what}{where} right now. Want me to try again in a moment?"


_UNIT_SUFFIX = {"_f": "°F", "_c": "°C", "_usd": "USD", "_inr": "INR", "_eur": "EUR", "_pct": "%", "_percent": "%",
                "_km": " km", "_mi": " miles", "_min": " min", "_mins": " min", "_minutes": " min", "_hours": " hours",
                "_kg": " kg", "_mb": " MB", "_gb": " GB"}


def _fmt_value(key: str, value: Any) -> str:
    k = key.lower()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        for suf, unit in _UNIT_SUFFIX.items():
            if k.endswith(suf):
                if unit in {"USD", "INR", "EUR"}:
                    txt = ent.format_price(float(value), unit)
                    return f"{txt} per day" if "daily" in k or "per_day" in k else (f"{txt} per night" if "night" in k else txt)
                return f"{value:g}{unit}"
        if re.search(r"price|cost|fare|amount|total|fee", k):
            return ent.format_price(float(value), "USD")
        return f"{value:g}"
    return str(value)


def _label(key: str) -> str:
    k = key.lower()
    for suf in _UNIT_SUFFIX:
        if k.endswith(suf):
            k = k[: -len(suf)]
            break
    return k.replace("_", " ").strip()


_NAME_KEYS = ("name", "title", "label", "day", "date")
_PRICE_KEY = re.compile(r"price|fare|cost|amount|total|fee|rate", re.I)


def _is_id(key: str) -> bool:
    return key.endswith("_id") or key == "id"


def _item_price(it: dict[str, Any]) -> tuple[str, float] | None:
    return next(((kk, float(x)) for kk, x in it.items()
                 if isinstance(x, (int, float)) and not isinstance(x, bool) and _PRICE_KEY.search(kk)), None)


def _money(key: str, value: float, currency: Any) -> str:
    if isinstance(currency, str) and len(currency) == 3 and not any(key.lower().endswith(s) for s in _UNIT_SUFFIX):
        return ent.format_price(value, currency.upper())
    return _fmt_value(key, value)


def _item_text(it: dict[str, Any], currency: Any) -> str:
    """One list item for speech: its human name, and its price if it has one (never a count like stops: 0)."""
    cur = it.get("currency") or currency
    name = next((it[kk] for kk in _NAME_KEYS if isinstance(it.get(kk), str) and it[kk]), None)
    ident = next((x for kk, x in it.items() if _is_id(kk)), None)
    if name is None:
        name = next((x for kk, x in it.items() if isinstance(x, str) and x and not _is_id(kk) and len(x) <= 40), None)
    d = str(name) if name is not None else str(ident or "")
    if name is not None and ident and ident != name and it.get("name"):
        d += f" ({ident})"
    price = _item_price(it)
    if price:
        d += f" at {_money(price[0], price[1], cur)}"
    else:
        extra = [_fmt_value(kk, x) for kk, x in it.items()
                 if isinstance(x, (int, float)) and not isinstance(x, bool) and not _is_id(kk)]
        if extra:
            d += f" ({extra[0]})"
    return d.strip()


def _price_notes(items: list[dict[str, Any]], goal: Goal, currency: Any) -> list[str]:
    """The cheapest option, and the total when the user said how many people (prices are per person)."""
    priced = [(it, p) for it in items if (p := _item_price(it))]
    if not priced:
        return []
    notes = []
    best, (key, amount) = min(priced, key=lambda t: t[1][1])
    cur = best.get("currency") or currency
    if len(priced) > 1:
        notes.append(f"cheapest is {_item_text(best, currency)}")
    try:
        pax = int(goal.slots.get("passengers") or 1)
    except (TypeError, ValueError):
        pax = 1
    if pax > 1:
        total = _money(key, amount * pax, cur)
        notes.append(f"for {pax} passengers the cheapest comes to {total}" if len(priced) > 1
                     else f"for {pax} passengers that's {total}")
    return notes


def describe_result(spec: ToolSpec | None, goal: Goal, result: dict[str, Any]) -> str:
    fields = {k: v for k, v in result.items() if k not in {"status", "currency"} and v not in (None, "", [], {})}
    prose = [v for k, v in fields.items() if k in {"answer", "text", "summary", "message", "reply", "detail"}
             and isinstance(v, str) and len(v.split()) >= 5]
    if prose:
        return prose[0]
    subj = _subject(spec, goal)
    noun = spec.noun if spec else "result"
    where = f" in {subj}" if subj and _is_place(subj) else (f" for {subj}" if subj else "")
    # Weather-like results read best as a sentence.
    if "condition" in fields and any(k.startswith("temp") for k in fields):
        temp_key = next(k for k in fields if k.startswith("temp"))
        s = f"It's {fields['condition']} and {_fmt_value(temp_key, fields[temp_key])}{where} right now"
        if fields.get("forecast"):
            s += f", with {fields['forecast']}"
        return s + "."
    parts: list[str] = []
    for k, v in fields.items():
        if isinstance(v, list):
            all_items = [i for i in v if isinstance(i, dict)]
            items = all_items[:3]
            if items:
                cur = result.get("currency")
                descs = [_item_text(it, cur) for it in items]
                parts.append(f"{_label(k)}: {_join(descs)}")
                parts.extend(_price_notes(all_items, goal, cur))
            elif v:
                parts.append(f"{_label(k)}: {_join([str(x) for x in v[:5]])}")
        elif isinstance(v, dict):
            inner = ", ".join(f"{_label(kk)} {_fmt_value(kk, x)}" for kk, x in list(v.items())[:4])
            parts.append(f"{_label(k)}: {inner}")
        else:
            if k.endswith("_id") or k == "id":
                parts.insert(0, f"{_label(k).replace(' id', '')} ID {v}")
            else:
                parts.append(f"{_label(k)} {_fmt_value(k, v)}")
    if not parts:
        return f"The {noun}{where} came back with no details."
    lead = f"Here's the {noun}{where}: " if not spec or not spec.state_modifying else f"Done with the {noun}{where}: "
    return lead + "; ".join(parts) + "."


def capabilities(manifest: Manifest) -> str:
    bits = []
    names = set(manifest.tools)
    if {"flight_search", "book_flight"} & names or not names:
        bits.append("search and book flights")
    if "cancel_booking" in names:
        bits.append("cancel bookings")
    if "lookup_manual" in names:
        bits.append("look things up in device manuals")
    if "create_support_ticket" in names:
        bits.append("open support tickets")
    extra = [n for n in names if n not in {"flight_search", "book_flight", "cancel_booking", "lookup_manual", "create_support_ticket"}]
    for n in extra[:3]:
        bits.append(n.replace("_", " "))
    return f"Hi! I can help you {_join(bits)}. Just tell me what you need, and feel free to interrupt me anytime."


def stopped_text(goal: Goal | None, rolled_back: list[str], held: list[str]) -> str:
    if goal is None:
        return "Okay, I've stopped."
    notes = []
    if rolled_back:
        notes.append("I've undone " + _join(rolled_back))
    if held:
        notes.append(_join(held) + " already went through and can't be undone")
    if goal.domain == "flight" and not rolled_back and not held:
        notes.append("no booking was made")
    tail = (" " + _cap("; ".join(notes)) + ".") if notes else ""
    return f"Okay, I've stopped.{tail}".replace(".;", ";")


def offer_back(goal: Goal) -> str:
    return f"Want me to get back to {_goal_label(goal)}?"
