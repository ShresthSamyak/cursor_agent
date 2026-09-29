"""Rule-based utterance understanding: dialog acts, intent, slots, self-repairs.

The parser is deliberately conservative. It reports a confidence, and the
runtime asks a model (or the user) only when rules cannot settle a turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import entities as ent

# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
BACKCHANNEL = {
    "mm", "mmm", "mm-hm", "mm-hmm", "mhm", "mhmm", "uh-huh", "uh huh", "okay", "ok", "k", "right", "yeah",
    "yep", "yup", "sure", "got it", "i see", "cool", "great", "alright", "all right", "nice", "hmm", "hm",
    "oh", "ah", "aha", "interesting", "good", "fine", "perfect", "yes", "uh", "um", "and", "so",
}
_FILLER_WORDS = {"uh", "um", "erm", "er", "hmm", "like", "you know", "so", "well", "oh", "okay", "ok"}
_STOP_RE = re.compile(
    r"^\W*(?:(?:oh|ok(?:ay)?|no|wait|actually|sorry|um|uh|hey|please)[\s,.!]+)*"
    r"(?:stop(?: (?:it|that|talking|there|searching|everything))?|never\s*mind(?: that| it)?|forget (?:it|about it|that)|"
    r"cancel (?:that|it|this|everything)|scratch that|abort|halt|don'?t (?:do|book|bother)(?: (?:it|that))?|"
    r"leave it|skip it|no thanks|no thank you|that'?s all|drop it|quit)\b",
    re.I,
)
_ABANDON_RE = re.compile(
    r"\b(?:forget|never\s*mind|scratch|drop|skip|ditch|cancel)\s+(?:about\s+)?(?:the|my|that|this)\s+"
    r"(flights?|booking|reservation|search|ticket|trip|hotel|car|weather|manual|tv|order|request|(?:\w+))\b"
    r"|\binstead\b|\bforget (?:it|that|about it)\b|\bnever\s*mind\b",
    re.I,
)
_PAUSE_RE = re.compile(r"^\W*(?:wait|hold on|hang on|one sec(?:ond)?|just a (?:sec|second|moment)|pause)\W*$", re.I)
_RESUME_RE = re.compile(r"^\W*(?:go on|continue|keep going|carry on|go ahead and continue|resume|as you were)\W*$", re.I)
_BACK_TO_RE = re.compile(
    r"\b(?:back to|go back to|return to|returning to|resume|where were we|anyway|as i was saying)\b(?:\s+(?:the|my))?\s*(\w+)?", re.I)
_NOT_NOW_RE = re.compile(r"\bnot now\b|\blater\b|\bdon'?t interrupt\b|\bstop interrupting\b|\bquiet\b", re.I)
_CONFIRM_RE = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|correct|right|exactly|that'?s (?:right|correct|it)|sure|go ahead|do it|please do|"
    r"confirm(?:ed)?|sounds good|ok(?:ay)?(?: do it)?|affirmative|book it|yes please|definitely|of course)\b",
    re.I,
)
_DENY_RE = re.compile(r"^\W*(?:no|nope|nah|not (?:that|really|quite)|wrong|incorrect|negative)\b", re.I)
_REPAIR_RE = re.compile(
    r"\b(?:actually|i mean|i meant|sorry|no wait|wait|make (?:it|that)|change (?:it|that) to|switch (?:it )?to|"
    r"rather|correction|instead|scratch that|let'?s (?:do|make it|say)|how about|what about|on second thought)\b"
    r"|(?:^|[,.;])\s*no[,.]?\s+(?=\w)",
    re.I,
)
_NEGATED_RE = re.compile(
    r"\b(?:not|instead of|rather than|except|no longer|not to)\s+((?:[A-Z][\w'.-]+)(?:\s+[A-Z][\w'.-]+){0,2}|\w+day|tomorrow|today)", re.I)
_CLARIFY_Q_RE = re.compile(
    r"\b(?:what (?:does|do) (?:that|this|it|you) mean|what do you mean|what is that|what'?s that mean|"
    r"what'?s an? \w+|meaning of|explain|say (?:that|it) again|repeat (?:that|it)|come again|pardon|"
    r"why (?:is|did|would)|how come|which one did you (?:mean|say))\b",
    re.I,
)
_GREETING_RE = re.compile(r"^\W*(?:hi|hello|hey|good (?:morning|afternoon|evening)|yo|howdy)\b", re.I)
_CAPABILITY_RE = re.compile(
    r"\bwhat (?:can|could|do) you (?:help|do|assist)|how can you help|what are you|who are you|what do you do|"
    r"help me with\??$|what can i ask|what are your (?:capabilities|features)|can you help\??$", re.I)
_THANKS_RE = re.compile(r"\b(?:thanks|thank you|cheers|appreciate it)\b", re.I)
_DEICTIC_RE = re.compile(r"\b(?:this|that|these|those|here|it)\b(?:\s+(?:one|port|button|light|thing|cable|part|icon|page|cell|item))?", re.I)

_BOOK_RE = re.compile(r"\b(?:book|booking|reserve|reservation|buy|purchase|get me (?:a )?(?:seat|ticket)|hold (?:a|the) seat)\b", re.I)
_FLIGHT_RE = re.compile(
    r"\b(?:flights?|fly|flying|plane|airline|airfare|fares?|seats?|tickets?|nonstop|red-?eye|departures?|"
    r"get (?:me )?to|head(?:ing)? to|travel(?:l?ing)? to|go(?:ing)? to|trip to|getaway to)\b", re.I)
_CANCEL_BOOKING_RE = re.compile(r"\bcancel\b[^.?!]{0,30}\b(?:booking|reservation|bk-\d+)\b|\bbk-\d+\b[^.?!]{0,20}\bcancel", re.I)
_TICKET_RE = re.compile(
    r"\b(?:(?:open|file|create|raise|submit|log|start|make)\s+(?:a\s+|an\s+)?(?:support\s+|service\s+|repair\s+)?(?:ticket|case|request|complaint)|"
    r"support ticket|service ticket|report (?:it|this|the (?:issue|problem))|get (?:it|this) (?:fixed|repaired)|send (?:a )?technician|"
    r"escalate)\b", re.I)
_DEVICE_ISSUE_RE = re.compile(
    r"\b(?:broken|broke|not working|isn'?t working|doesn'?t work|stopped working|won'?t (?:turn on|start|charge|connect|work|power on|spin|drain)|"
    r"keeps? (?:\w+ing)|blinking|flashing|error|error code|no signal|no sound|no picture|dead|cracked|overheating|frozen|"
    r"stuck|leaking|noise|troubleshoot|fix|reset|set ?up|connect|pair|install|manual|port|cable|button|led|light is|"
    r"used for|what'?s this for|what is this for)\b", re.I)
_HOW_TO_RE = re.compile(r"\bhow (?:do|can|to|should) (?:i|we|you)\b", re.I)
_QUESTION_RE = re.compile(r"\?\s*$|^\W*(?:what|which|who|whom|where|when|why|how|is|are|can|could|would|will|do|does|did|should|shall)\b", re.I)

# Preferences over search results.
_PREF_RE = re.compile(
    r"\b(cheapest|cheaper|lowest(?: price| fare)?|least expensive|most affordable|budget|"
    r"earliest|earlier|first flight|latest|later one|last flight|fastest|shortest|"
    r"first|second|third|last|other) (?:one|option|flight|fare)?", re.I)


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Parse:
    text: str
    effective: str                         # text after self-repair (last repair wins)
    acts: frozenset[str]
    intent: str | None
    domain: str | None                     # "flight", "device", "smalltalk" or None
    slots: dict[str, Any] = field(default_factory=dict)
    superseded: dict[str, list[str]] = field(default_factory=dict)
    places: tuple[ent.Place, ...] = ()
    dates: tuple[ent.DateMention, ...] = ()
    times: tuple[ent.TimeMention, ...] = ()
    counts: tuple[ent.Count, ...] = ()
    prices: tuple[ent.Price, ...] = ()
    person: str | None = None
    device: str | None = None
    model_tokens: tuple[str, ...] = ()
    confidence: float = 1.0
    question: bool = False
    deictic: bool = False
    abandon_target: str | None = None
    back_to: str | None = None

    def has(self, act: str) -> bool:
        return act in self.acts

    @property
    def content_words(self) -> list[str]:
        return content_words(self.effective)


_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "in", "on", "at", "and", "or", "is", "are", "be", "me", "my", "i",
    "you", "your", "it", "this", "that", "can", "could", "would", "please", "what", "whats", "what's", "like",
    "right", "now", "with", "some", "any", "do", "does", "get", "find", "show", "tell", "about", "how",
    "much", "many", "there", "here", "just", "want", "need", "would", "like", "hey", "hi", "so", "um", "uh",
    "we", "our", "us", "from", "by", "up", "out", "if", "is", "was", "be", "am", "will", "shall", "let",
    "lets", "let's", "ok", "okay", "yes", "no", "not", "one", "also", "then", "too", "very", "really",
}


def stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            base = w[: -len(suffix)]
            return base + "y" if suffix == "ies" else base
    return w


def content_words(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-zA-Z][a-zA-Z'-]+", text.lower()) if w not in _STOPWORDS]


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t


def _segments(text: str) -> list[str]:
    """Split a turn at self-repair markers; the last segment is the user's final say."""
    cuts = [m.start() for m in _REPAIR_RE.finditer(text) if m.start() > 0]
    if not cuts:
        return [text]
    parts, prev = [], 0
    for c in cuts:
        parts.append(text[prev:c])
        prev = c
    parts.append(text[prev:])
    return [p for p in parts if p.strip()]


def model_tokens(text: str) -> tuple[str, ...]:
    """Alphanumeric product-model-like tokens: QN90, S24, WF45, XPS-13."""
    toks = re.findall(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{2,12}\b", text)
    out = []
    for t in toks:
        if re.fullmatch(r"\d+(?:st|nd|rd|th|am|pm|k)", t, re.I) or ent.FLIGHT_ID_RE.fullmatch(t) or ent.BOOKING_ID_RE.fullmatch(t):
            continue
        if re.fullmatch(r"(?:20\d\d|\d{1,2})-\d{1,2}(?:-\d{1,2})?", t):
            continue
        out.append(t.upper())
    return tuple(out)


def _is_backchannel(text: str) -> bool:
    t = re.sub(r"[^\w\s'-]", " ", text.lower()).strip()
    if not t:
        return False
    if t in BACKCHANNEL:
        return True
    words = t.split()
    if len(words) > 4:
        return False
    joined = " ".join(words)
    if joined in BACKCHANNEL:
        return True
    return all(w in BACKCHANNEL for w in words)


def parse(text: str) -> Parse:
    text = _clean(text)
    low = text.lower()
    acts: set[str] = set()
    segments = _segments(text)
    effective = segments[-1] if len(segments) > 1 else text
    if len(segments) > 1:
        acts.add("repair")

    question = bool(_QUESTION_RE.search(text))
    if question:
        acts.add("question")
    if _is_backchannel(text):
        acts.add("backchannel")
    if _STOP_RE.search(text):
        acts.add("stop")
    abandon = _ABANDON_RE.search(text)
    abandon_target = None
    if abandon:
        acts.add("abandon")
        abandon_target = (abandon[1] or "").lower() or None
    if _PAUSE_RE.match(text):
        acts.add("pause")
    if _RESUME_RE.match(text):
        acts.add("resume")
    back = _BACK_TO_RE.search(text)
    back_to = None
    if back and not re.search(r"\bcome back\b", low):
        acts.add("back_to")
        back_to = (back[1] or "").lower() or None
    if _NOT_NOW_RE.search(text):
        acts.add("not_now")
    if _CONFIRM_RE.match(text):
        acts.add("confirm")
    if _DENY_RE.match(text):
        acts.add("deny")
    if _CLARIFY_Q_RE.search(text):
        acts.add("clarify_q")
    if _GREETING_RE.match(text):
        acts.add("greeting")
    if _CAPABILITY_RE.search(text):
        acts.add("capability")
    if _THANKS_RE.search(text):
        acts.add("thanks")
    deictic = bool(_DEICTIC_RE.search(text)) and (question or "no," in low or low.startswith("no "))
    if deictic:
        acts.add("deictic")
    if re.search(r"\bi said\b|\bi meant\b|\bi mean\b", low):
        acts.add("restate")

    # ---- entities, repair-aware ------------------------------------------
    places_all = ent.find_places(text)
    negated = {m[1].strip(" .,").lower() for m in _NEGATED_RE.finditer(text)}
    superseded: dict[str, list[str]] = {}
    places = [p for p in places_all if p.name.lower() not in negated]
    for p in places_all:
        if p.name.lower() in negated:
            superseded.setdefault("place", []).append(p.name)

    def last_in_effective(items, key):
        if len(segments) > 1:
            eff_start = text.rfind(effective)
            later = [i for i in items if key(i) >= eff_start]
            if later:
                earlier = [i for i in items if key(i) < eff_start]
                return later, earlier
        return items, []

    places, dropped_places = last_in_effective(places, lambda p: p.start)
    for p in dropped_places:
        superseded.setdefault("place", []).append(p.name)
    dates_all = ent.find_dates(text)
    dates_all = [d for d in dates_all if d.value.lower() not in negated]
    dates, dropped_dates = last_in_effective(dates_all, lambda d: d.start)
    for d in dropped_dates:
        superseded.setdefault("date", []).append(d.value)
    times_all = ent.find_times(text)
    times, _ = last_in_effective(times_all, lambda t: t.start)
    counts = ent.find_counts(text)
    prices = ent.find_prices(text)
    exclude = [p.name for p in places_all]
    person = ent.find_person(effective, exclude=exclude) or ent.find_person(text, exclude=exclude)
    device = ent.find_device(text)
    models = model_tokens(text)

    slots: dict[str, Any] = {}
    dest = [p for p in places if p.role == "destination"]
    origin = [p for p in places if p.role == "origin"]
    located = [p for p in places if p.role == "location"]
    plain = [p for p in places if not p.role]
    if dest:
        slots["destination"] = dest[-1].name
    if origin:
        slots["origin"] = origin[-1].name
    if located:
        slots["location"] = located[-1].name
    if plain and "destination" not in slots and "location" not in slots:
        slots["place"] = plain[-1].name
    if places:
        slots["_last_place"] = places[-1].name
    if dates:
        slots["date"] = dates[-1].value
    if person:
        slots["passenger_name"] = person
    for c in counts:
        slots[{"passenger": "passengers"}.get(c.unit, c.unit + "s")] = c.value
    fid = ent.FLIGHT_ID_RE.search(text)
    if fid:
        slots["flight_id"] = fid[0].upper()
    bid = ent.BOOKING_ID_RE.search(text)
    if bid:
        slots["booking_id"] = bid[0].upper()
    if device:
        slots["device"] = device
    pref = _flight_pref(effective if len(segments) > 1 else text, times)
    if pref:
        slots["flight_pref"] = pref

    # ---- intent ------------------------------------------------------------
    intent, domain, confidence = _intent(text, low, acts, slots, device, models)
    if domain == "device":
        summary = issue_summary(text, device)
        if summary:
            slots["issue_summary"] = summary
        slots["severity"] = severity(text)
    return Parse(
        text=text, effective=effective, acts=frozenset(acts), intent=intent, domain=domain, slots=slots,
        superseded=superseded, places=tuple(places), dates=tuple(dates), times=tuple(times),
        counts=tuple(counts), prices=tuple(prices), person=person, device=device, model_tokens=models,
        confidence=confidence, question=question, deictic=deictic, abandon_target=abandon_target,
        back_to=back_to,
    )


def _flight_pref(text: str, times: list[ent.TimeMention]) -> dict[str, Any] | None:
    pref: dict[str, Any] = {}
    exact = [t for t in times if t.minutes is not None]
    periods = [t for t in times if t.period]
    if exact:
        pref["time"] = exact[-1].minutes
    elif periods:
        pref["period"] = periods[-1].period
    m = None
    for m in _PREF_RE.finditer(text):
        pass
    if m:
        word = m[1].lower()
        if word in {"cheapest", "cheaper", "lowest", "lowest price", "lowest fare", "least expensive", "most affordable", "budget"}:
            pref["rank"] = "cheapest"
        elif word in {"earliest", "earlier", "first flight"}:
            pref["rank"] = "earliest"
        elif word in {"latest", "later one", "last flight"}:
            pref["rank"] = "latest"
        elif word in {"fastest", "shortest"}:
            pref["rank"] = "fastest"
        elif word in {"first", "second", "third", "last"} and re.search(rf"\b{word}\s+(?:one|option|flight)\b", text, re.I):
            pref["ordinal"] = {"first": 0, "second": 1, "third": 2, "last": -1}[word]
        elif word == "other":
            pref["rank"] = "other"
    return pref or None


def _intent(text: str, low: str, acts: set[str], slots: dict[str, Any], device: str | None,
            models: tuple[str, ...]) -> tuple[str | None, str | None, float]:
    has_place = any(k in slots for k in ("destination", "origin", "place", "location"))
    flight_words = bool(_FLIGHT_RE.search(text))
    book = bool(_BOOK_RE.search(text))
    if _CANCEL_BOOKING_RE.search(text):
        return "cancel_booking", "flight", 0.95
    if _TICKET_RE.search(text):
        return "create_support_ticket", "device", 0.9
    device_issue = bool(_DEVICE_ISSUE_RE.search(text)) or bool(device) or (bool(models) and not flight_words)
    if device_issue and not (flight_words and has_place):
        conf = 0.9 if (device or models or re.search(r"\b(?:port|manual|led|cable|button)\b", low)) else 0.6
        if _HOW_TO_RE.search(text) or "question" in acts or re.search(r"\b(?:broken|not working|won'?t|error|blinking|issue|problem)\b", low):
            return "device_support", "device", conf
        return "device_support", "device", conf * 0.8
    if flight_words or (book and has_place) or ("destination" in slots and ("date" in slots or book)):
        if book or "flight_id" in slots:
            return "book_flight", "flight", 0.95 if has_place or "flight_pref" in slots or "flight_id" in slots else 0.8
        conf = 0.95 if has_place else 0.7
        return "flight_search", "flight", conf
    if book and ("flight_pref" in slots or "passenger_name" in slots):
        # "book the 8 AM one for Alice" after a search.
        return "book_flight", "flight", 0.85
    if "capability" in acts or ("greeting" in acts and len(low.split()) <= 8 and not has_place):
        return "capabilities", "smalltalk", 0.9
    if "thanks" in acts and len(low.split()) <= 6:
        return "thanks", "smalltalk", 0.9
    return None, None, 0.0


def issue_summary(text: str, device: str | None) -> str | None:
    t = _clean(text)
    t = re.sub(r"^(?:hey|hi|hello|ok(?:ay)?|so|um+|uh+|well|please|actually|forget (?:the|about the) \w+|never\s*mind \w+)[,.!\s]+",
               "", t, flags=re.I)
    m = re.search(r"\bmy\s+((?:[\w-]+\s){0,2}?(?:[\w-]+))\s+(is|keeps|won'?t|isn'?t|doesn'?t|has|stopped|shows|won't)\b(.*)", t, re.I)
    if m:
        s = f"{m[1]} {m[2]}{m[3]}".strip(" .!?")
        return s[:1].upper() + s[1:]
    m = re.search(r"\b(?:the|this|its|it'?s)\s+((?:[\w-]+\s){0,2}?(?:led|light|screen|port|display|drum|door|button|speaker|battery))\s+(.*)", t, re.I)
    if m:
        s = f"{m[1]} {m[2]}".strip(" .!?")
        return s[:1].upper() + s[1:]
    words = t.strip(" .!?")
    if len(words) > 3:
        return words[:1].upper() + words[1:120]
    return None


def severity(text: str) -> str:
    low = text.lower()
    if re.search(r"\b(?:urgent|emergency|asap|critical|fire|smoke|sparks?|dangerous|burning|shock|flood(?:ing)?|leaking)\b", low):
        return "high"
    if re.search(r"\b(?:won'?t (?:turn on|power|start)|dead|completely|not working at all|broken)\b", low):
        return "high"
    if re.search(r"\b(?:minor|cosmetic|small|slight|a bit|sometimes|occasionally)\b", low):
        return "low"
    return "medium"


def strip_fillers(text: str) -> str:
    t = re.sub(r"\b(?:uh+|um+|erm|er|hmm+|you know|like,)\s*,?\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" ,")
