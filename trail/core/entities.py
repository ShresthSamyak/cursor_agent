"""Rule-based entity parsers: places, dates, times, prices, names, numbers, ids.

Rules run first and cost nothing (PDF p. 12). The model hook only sees what
these parsers could not settle. Everything here is pure and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------
# canonical name -> spoken aliases (lowercase). Upper-case airport codes are
# matched separately and only when written in capitals, because many codes are
# ordinary words ("sea", "den", "mia").
_CITIES: dict[str, tuple[str, ...]] = {
    "New York": ("new york", "new york city", "nyc", "manhattan", "the big apple"),
    "Boston": ("boston",), "Chicago": ("chicago",), "Denver": ("denver",),
    "Seattle": ("seattle",), "Miami": ("miami",), "Austin": ("austin",),
    "Los Angeles": ("los angeles", "l.a."), "San Francisco": ("san francisco", "san fran", "frisco"),
    "Las Vegas": ("las vegas", "vegas"), "Washington": ("washington dc", "washington d.c.", "d.c."),
    "Atlanta": ("atlanta",), "Dallas": ("dallas",), "Houston": ("houston",),
    "Phoenix": ("phoenix",), "Philadelphia": ("philadelphia", "philly"), "San Diego": ("san diego",),
    "San Jose": ("san jose",), "San Antonio": ("san antonio",), "Portland": ("portland",),
    "Orlando": ("orlando",), "Tampa": ("tampa",), "Nashville": ("nashville",),
    "New Orleans": ("new orleans",), "Detroit": ("detroit",), "Minneapolis": ("minneapolis",),
    "St. Louis": ("st. louis", "st louis", "saint louis"), "Salt Lake City": ("salt lake city", "salt lake"),
    "Kansas City": ("kansas city",), "Charlotte": ("charlotte",), "Pittsburgh": ("pittsburgh",),
    "Baltimore": ("baltimore",), "Cleveland": ("cleveland",), "Honolulu": ("honolulu",),
    "Anchorage": ("anchorage",), "Sacramento": ("sacramento",), "Raleigh": ("raleigh",),
    "Indianapolis": ("indianapolis",), "Columbus": ("columbus",), "Milwaukee": ("milwaukee",),
    "Albuquerque": ("albuquerque",), "Tucson": ("tucson",), "Oklahoma City": ("oklahoma city",),
    "Memphis": ("memphis",), "Louisville": ("louisville",), "Buffalo": ("buffalo",),
    "Toronto": ("toronto",), "Vancouver": ("vancouver",), "Montreal": ("montreal",),
    "Mexico City": ("mexico city",), "Cancun": ("cancun",),
    "London": ("london",), "Paris": ("paris",), "Berlin": ("berlin",), "Madrid": ("madrid",),
    "Barcelona": ("barcelona",), "Rome": ("rome",), "Milan": ("milan",), "Amsterdam": ("amsterdam",),
    "Dublin": ("dublin",), "Lisbon": ("lisbon",), "Vienna": ("vienna",), "Prague": ("prague",),
    "Zurich": ("zurich",), "Munich": ("munich",), "Frankfurt": ("frankfurt",), "Brussels": ("brussels",),
    "Copenhagen": ("copenhagen",), "Stockholm": ("stockholm",), "Oslo": ("oslo",), "Helsinki": ("helsinki",),
    "Athens": ("athens",), "Istanbul": ("istanbul",), "Moscow": ("moscow",), "Reykjavik": ("reykjavik",),
    "Dubai": ("dubai",), "Abu Dhabi": ("abu dhabi",), "Doha": ("doha",), "Tel Aviv": ("tel aviv",),
    "Cairo": ("cairo",), "Nairobi": ("nairobi",), "Cape Town": ("cape town",), "Johannesburg": ("johannesburg",),
    "Tokyo": ("tokyo",), "Osaka": ("osaka",), "Seoul": ("seoul",), "Beijing": ("beijing",),
    "Shanghai": ("shanghai",), "Hong Kong": ("hong kong",), "Singapore": ("singapore",),
    "Bangkok": ("bangkok",), "Kuala Lumpur": ("kuala lumpur",), "Jakarta": ("jakarta",), "Bali": ("bali",),
    "Manila": ("manila",), "Hanoi": ("hanoi",), "Sydney": ("sydney",), "Melbourne": ("melbourne",),
    "Auckland": ("auckland",), "Sao Paulo": ("sao paulo", "são paulo"), "Rio de Janeiro": ("rio de janeiro", "rio"),
    "Buenos Aires": ("buenos aires",), "Lima": ("lima",), "Bogota": ("bogota", "bogotá"),
    "Delhi": ("delhi", "new delhi"), "Mumbai": ("mumbai", "bombay"), "Bengaluru": ("bengaluru", "bangalore"),
    "Chennai": ("chennai", "madras"), "Kolkata": ("kolkata", "calcutta"), "Hyderabad": ("hyderabad",),
    "Pune": ("pune",), "Goa": ("goa",), "Chandigarh": ("chandigarh",), "Jaipur": ("jaipur",),
    "Ahmedabad": ("ahmedabad",), "Kochi": ("kochi", "cochin"), "Lucknow": ("lucknow",),
    "Srinagar": ("srinagar",), "Amritsar": ("amritsar",), "Varanasi": ("varanasi",),
}
_CODES: dict[str, str] = {
    "NYC": "New York", "JFK": "New York", "LGA": "New York", "EWR": "New York", "BOS": "Boston",
    "ORD": "Chicago", "CHI": "Chicago", "DEN": "Denver", "SEA": "Seattle", "MIA": "Miami",
    "AUS": "Austin", "LAX": "Los Angeles", "LA": "Los Angeles", "SFO": "San Francisco", "SF": "San Francisco",
    "LAS": "Las Vegas", "DC": "Washington", "DCA": "Washington", "IAD": "Washington", "ATL": "Atlanta",
    "DFW": "Dallas", "IAH": "Houston", "PHX": "Phoenix", "PHL": "Philadelphia", "SAN": "San Diego",
    "MCO": "Orlando", "MSP": "Minneapolis", "DTW": "Detroit", "SLC": "Salt Lake City", "HNL": "Honolulu",
    "YYZ": "Toronto", "YVR": "Vancouver", "LHR": "London", "CDG": "Paris", "DXB": "Dubai",
    "HND": "Tokyo", "NRT": "Tokyo", "SIN": "Singapore", "HKG": "Hong Kong", "SYD": "Sydney",
    "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru", "MAA": "Chennai", "CCU": "Kolkata",
    "HYD": "Hyderabad", "GOI": "Goa", "IXC": "Chandigarh",
}
_ALIAS_TO_CITY = {alias: city for city, aliases in _CITIES.items() for alias in aliases}
_CITY_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(a) for a in sorted(_ALIAS_TO_CITY, key=len, reverse=True)) + r")(?![\w-])",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(_CODES, key=len, reverse=True)) + r")(?![\w-])")
# Unknown places: a capitalised phrase after a travel preposition.
_PLACE_AFTER = re.compile(
    r"\b(?:to|in|into|from|at|near|around|visit(?:ing)?|towards?)\s+((?:[A-Z][a-zà-ÿ'.-]+)(?:\s+[A-Z][a-zà-ÿ'.-]+){0,2})"
)


@dataclass(frozen=True)
class Place:
    name: str
    start: int
    end: int
    known: bool
    role: str = ""        # "destination", "origin", "location" or ""


def city_names() -> tuple[str, ...]:
    return tuple(_CITIES)


def canonical_city(text: str) -> str | None:
    t = text.strip().strip(".,!?").lower()
    if t in _ALIAS_TO_CITY:
        return _ALIAS_TO_CITY[t]
    up = text.strip().strip(".,!?").upper()
    return _CODES.get(up) if up == text.strip().strip(".,!?") else None


def find_places(text: str) -> list[Place]:
    """All place mentions in order, with a travel role when one is signalled."""
    found: list[Place] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    for m in _CITY_RE.finditer(text):
        alias = m[1].lower()
        # "rio" and "la" as ordinary words: require capitals for very short aliases.
        if len(alias) <= 3 and not m[1][0].isupper():
            continue
        found.append(Place(_ALIAS_TO_CITY[alias], m.start(), m.end(), True))
        taken.append((m.start(), m.end()))
    for m in _CODE_RE.finditer(text):
        if free(m.start(), m.end()):
            found.append(Place(_CODES[m[1]], m.start(), m.end(), True))
            taken.append((m.start(), m.end()))
    for m in _PLACE_AFTER.finditer(text):
        phrase = m[1]
        a, b = m.start(1), m.end(1)
        if not free(a, b):
            continue
        words = phrase.split()
        # Trim trailing non-place words (weekdays, months, "Airport").
        while words and (words[-1].lower().strip(".,") in _NOT_PLACE or _is_temporal(words[-1])):
            words.pop()
        if not words or words[0].lower().strip(".,") in _NOT_PLACE or _is_temporal(words[0]):
            continue
        name = " ".join(words).strip(".,")
        found.append(Place(name, a, a + len(name), False))
        taken.append((a, a + len(name)))
    found.sort(key=lambda p: p.start)
    return [_with_role(text, p) for p in found]


_NOT_PLACE = {
    "i", "me", "my", "you", "we", "us", "the", "a", "an", "it", "this", "that", "please", "there",
    "here", "home", "work", "town", "flight", "flights", "hotel", "airport", "economy", "business",
    "first", "morning", "afternoon", "evening", "night", "noon", "am", "pm", "mr", "mrs", "ms", "dr",
    "celsius", "fahrenheit", "metric", "imperial", "okay", "ok", "yes", "no", "hdmi", "usb", "tv",
}


def _with_role(text: str, place: Place) -> Place:
    before = text[max(0, place.start - 24):place.start].lower()
    role = ""
    if re.search(r"\b(?:from|leaving|departing|out of)\s+(?:the\s+)?$", before):
        role = "origin"
    elif re.search(r"\b(?:to|into|towards?|for|visit(?:ing)?|fly(?:ing)?|get to|go(?:ing)? to|trip to|flights?|seats?|tickets?|arrive in|land in)\s+(?:the\s+)?$", before):
        role = "destination"
    elif re.search(r"\b(?:in|at|near|around)\s+(?:the\s+)?$", before):
        role = "location"
    elif re.search(r"\b(?:make it|make that|change it to|switch to|instead|actually|rather|no,?)\s*,?\s*$", before):
        role = "destination"
    return Place(place.name, place.start, place.end, place.known, role)


# ---------------------------------------------------------------------------
# Dates and times
# ---------------------------------------------------------------------------
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_WD_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
_MONTH_ABBR = {m[:3].lower(): i for i, m in enumerate(MONTHS)} | {"sept": 8}

_WEEKDAY_RE = re.compile(
    r"\b(?:(this|next|coming|on)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.I)
_WD_ABBR_RE = re.compile(r"\b(Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)\b\.?")
_RELATIVE_RE = re.compile(
    r"\b(day after tomorrow|tomorrow night|tomorrow morning|tomorrow|tonight|today|this weekend|next weekend|next week|this week)\b", re.I)
_MONTH_DAY_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.I)
_ISO_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def _is_temporal(word: str) -> bool:
    w = word.lower().strip(".,")
    return w in {d.lower() for d in WEEKDAYS} or w in {m.lower() for m in MONTHS} or w in _WD_ABBR or w in {
        "today", "tomorrow", "tonight", "weekend", "week", "next", "this"}


@dataclass(frozen=True)
class DateMention:
    value: str          # canonical spoken form: "Friday", "next Friday", "tomorrow", "September 12"
    start: int
    end: int
    weekday: int | None = None


def find_dates(text: str) -> list[DateMention]:
    out: list[DateMention] = []
    for m in _RELATIVE_RE.finditer(text):
        out.append(DateMention(m[1].lower(), m.start(), m.end()))
    for m in _WEEKDAY_RE.finditer(text):
        day = m[2].capitalize()
        mod = (m[1] or "").lower()
        value = f"next {day}" if mod == "next" else day
        out.append(DateMention(value, m.start(), m.end(), WEEKDAYS.index(day)))
    for m in _WD_ABBR_RE.finditer(text):
        if any(d.start <= m.start() < d.end for d in out):
            continue
        idx = _WD_ABBR[m[1].lower().rstrip(".")]
        out.append(DateMention(WEEKDAYS[idx], m.start(), m.end(), idx))
    for m in _MONTH_DAY_RE.finditer(text):
        month = MONTHS[_MONTH_ABBR[m[1][:4].lower() if m[1].lower().startswith("sept") else m[1][:3].lower()]]
        if m[1].lower() == "may" and not re.search(r"\d", m[0]):
            continue
        out.append(DateMention(f"{month} {int(m[2])}", m.start(), m.end()))
    for m in _DAY_MONTH_RE.finditer(text):
        if any(d.start <= m.start() < d.end for d in out):
            continue
        key = m[2].lower()
        month = MONTHS[_MONTH_ABBR["sept" if key.startswith("sept") else key[:3]]]
        out.append(DateMention(f"{month} {int(m[1])}", m.start(), m.end()))
    for m in _ISO_RE.finditer(text):
        out.append(DateMention(m[0], m.start(), m.end()))
    for m in _SLASH_RE.finditer(text):
        a, b = int(m[1]), int(m[2])
        if 1 <= a <= 12 and 1 <= b <= 31:
            out.append(DateMention(f"{MONTHS[a - 1]} {b}", m.start(), m.end()))
    out.sort(key=lambda d: d.start)
    # Drop mentions nested inside another ("tomorrow" inside "day after tomorrow").
    kept: list[DateMention] = []
    for d in out:
        if kept and d.start < kept[-1].end:
            if d.end - d.start > kept[-1].end - kept[-1].start:
                kept[-1] = d
            continue
        kept.append(d)
    return kept


@dataclass(frozen=True)
class TimeMention:
    minutes: int | None     # minutes after midnight when exact
    period: str | None      # "morning", "afternoon", "evening", "night"
    start: int
    end: int

    @property
    def label(self) -> str:
        if self.minutes is None:
            return self.period or ""
        return format_clock(self.minutes)


_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
_CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_OCLOCK_RE = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+o'?clock\b", re.I)
_WORD_TIME_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)
_PERIOD_RE = re.compile(r"\b(early morning|morning|afternoon|evening|night|red-?eye|noon|midday|midnight)\b", re.I)


def format_clock(minutes: int) -> str:
    h, m = divmod(minutes % (24 * 60), 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def find_times(text: str) -> list[TimeMention]:
    out: list[TimeMention] = []
    for m in _TIME_RE.finditer(text):
        h = int(m[1]) % 12 + (12 if m[3].lower().startswith("p") else 0)
        out.append(TimeMention(h * 60 + int(m[2] or 0), None, m.start(), m.end()))
    for m in _WORD_TIME_RE.finditer(text):
        h = WORD_NUMBERS[m[1].lower()] % 12 + (12 if m[2].lower().startswith("p") else 0)
        out.append(TimeMention(h * 60, None, m.start(), m.end()))
    for m in _CLOCK_RE.finditer(text):
        if not any(t.start <= m.start() < t.end for t in out):
            out.append(TimeMention(int(m[1]) * 60 + int(m[2]), None, m.start(), m.end()))
    for m in _OCLOCK_RE.finditer(text):
        raw = m[1].lower()
        h = int(raw) if raw.isdigit() else WORD_NUMBERS[raw]
        out.append(TimeMention((h % 24) * 60, None, m.start(), m.end()))
    for m in _PERIOD_RE.finditer(text):
        word = m[1].lower()
        if word in {"noon", "midday"}:
            out.append(TimeMention(12 * 60, None, m.start(), m.end()))
        elif word == "midnight":
            out.append(TimeMention(0, None, m.start(), m.end()))
        else:
            out.append(TimeMention(None, "night" if "red" in word else word.replace("early ", ""), m.start(), m.end()))
    out.sort(key=lambda t: t.start)
    return out


def parse_clock(value: str) -> int | None:
    """'08:00' / '8 AM' / '14:30' -> minutes after midnight."""
    times = find_times(str(value))
    return times[0].minutes if times and times[0].minutes is not None else None


def in_period(minutes: int, period: str) -> bool:
    return {
        "morning": 5 * 60 <= minutes < 12 * 60,
        "afternoon": 12 * 60 <= minutes < 17 * 60,
        "evening": 17 * 60 <= minutes < 21 * 60,
        "night": minutes >= 21 * 60 or minutes < 5 * 60,
    }.get(period, False)


# ---------------------------------------------------------------------------
# Prices and numbers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Price:
    amount: float
    currency: str
    start: int
    end: int


_PRICE_RE = re.compile(
    r"(?P<sym>₹|\$|€|£|¥|rs\.?|inr|usd|eur|gbp)\s*(?P<num>\d{1,3}(?:[,\s]\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<k>k\b)?"
    r"|(?P<num2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<k2>k\b)?\s*(?P<word>rupees|dollars|bucks|euros|pounds|inr|usd)\b",
    re.I,
)
_CURRENCY = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "$": "USD", "usd": "USD",
             "dollars": "USD", "bucks": "USD", "€": "EUR", "eur": "EUR", "euros": "EUR", "£": "GBP",
             "gbp": "GBP", "pounds": "GBP", "¥": "JPY"}


def find_prices(text: str) -> list[Price]:
    out = []
    for m in _PRICE_RE.finditer(text):
        raw = m["num"] or m["num2"]
        amount = float(re.sub(r"[,\s]", "", raw))
        if m["k"] or m["k2"]:
            amount *= 1000
        cur = _CURRENCY[(m["sym"] or m["word"]).lower()]
        out.append(Price(amount, cur, m.start(), m.end()))
    return out


def format_price(amount: float, currency: str) -> str:
    whole = f"{amount:,.0f}" if amount == int(amount) else f"{amount:,.2f}"
    return {"USD": f"${whole}", "INR": f"₹{whole}", "EUR": f"€{whole}", "GBP": f"£{whole}"}.get(
        currency, f"{whole} {currency}")


WORD_NUMBERS = {
    "zero": 0, "one": 1, "a single": 1, "two": 2, "a couple of": 2, "a couple": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30,
}
_NUM_WORDS_RE = "|".join(re.escape(w) for w in sorted(WORD_NUMBERS, key=len, reverse=True))
_COUNT_RE = re.compile(
    rf"\b(\d+|{_NUM_WORDS_RE})\s+(?:(?:more|extra|adult|adults|child|children|kids)\s+)?"
    r"(passengers?|people|persons?|adults?|travell?ers?|tickets?|seats?|nights?|rooms?|days?|bags?|guests?|hours?|weeks?)\b",
    re.I,
)


@dataclass(frozen=True)
class Count:
    value: int
    unit: str       # normalised singular: passenger, night, room, day, bag, guest, hour, week
    start: int
    end: int


def _unit(word: str) -> str:
    w = word.lower()
    if w in {"people", "persons", "person", "adult", "adults", "travelers", "travellers", "traveler",
             "traveller", "passengers", "passenger", "tickets", "ticket", "seats", "seat"}:
        return "passenger"
    return w.rstrip("s") if w.endswith("s") else w


def find_counts(text: str) -> list[Count]:
    out = []
    for m in _COUNT_RE.finditer(text):
        raw = m[1].lower()
        value = int(raw) if raw.isdigit() else WORD_NUMBERS[raw]
        out.append(Count(value, _unit(m[2]), m.start(), m.end()))
    return out


def find_numbers(text: str) -> list[float]:
    nums = [float(x.replace(",", "")) for x in re.findall(r"(?<![\w.-])\d+(?:,\d{3})*(?:\.\d+)?(?![\w-])", text)]
    for m in re.finditer(rf"\b({_NUM_WORDS_RE})\b", text, re.I):
        nums.append(float(WORD_NUMBERS[m[1].lower()]))
    return nums


# ---------------------------------------------------------------------------
# People, ids, devices
# ---------------------------------------------------------------------------
_NAME_STOP = {
    "me", "myself", "us", "you", "him", "her", "them", "it", "that", "this", "the", "a", "an", "my",
    "friday", "monday", "tuesday", "wednesday", "thursday", "saturday", "sunday", "today", "tomorrow",
    "tonight", "now", "later", "morning", "afternoon", "evening", "business", "economy", "first",
    "one", "two", "three", "both", "all", "everyone", "please", "real", "sure", "whom", "who",
}
_NAME_TOKEN = r"[A-Z][a-zà-ÿ'’-]+"
_NAME_PATTERNS = (
    re.compile(rf"\bpassenger(?:'s)?(?:\s+name)?(?:\s+is|:)?\s+({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)"),
    re.compile(rf"\b(?:[Nn]ame\s+is|[Nn]ame's|under\s+(?:the\s+name\s+(?:of\s+)?)?)\s*({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)"),
    re.compile(rf"\b(?:[Ii]t's|[Ii]t is|[Tt]his is|[Ii]'m|[Ii] am)\s+for\s+({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)"),
    re.compile(rf"\b(?:book|reserve|get|buy|hold|put)\b[^.?!]{{0,40}}?\bfor\s+({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)"),
    re.compile(rf"\bfor\s+({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)\s*(?:[.!?,]|$)"),
    re.compile(rf"\b(?:[Mm]ake it|[Mm]ake that|[Ss]witch it to|[Cc]hange it to|[Ii]nstead)\s+(?:for\s+)?({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})?)\s*(?:[.!?,]|$)"),
)


def find_person(text: str, *, exclude: Iterable[str] = ()) -> str | None:
    """A passenger-style name, excluding places, dates and pronouns."""
    blocked = {e.lower() for e in exclude}
    for pattern in _NAME_PATTERNS:
        for m in pattern.finditer(text):
            words = m[1].split()
            while words and (words[-1].lower().strip(".,") in _NAME_STOP or _is_temporal(words[-1])):
                words.pop()
            if not words:
                continue
            name = " ".join(words).strip(".,")
            low = name.lower()
            if low in _NAME_STOP or low in blocked or canonical_city(name) or _is_temporal(words[0]):
                continue
            if any(w.lower() in _NAME_STOP for w in words):
                continue
            return name
    return None


def bare_name(text: str) -> str | None:
    """A reply that is just a name, e.g. to "what name should I book under?"."""
    t = re.sub(r"^(?:it's|it is|that's|the name is|name is|for|um+|uh+|oh)\s+", "", text.strip(), flags=re.I)
    t = t.strip(" .!?,")
    words = t.split()
    if 1 <= len(words) <= 3 and all(re.fullmatch(r"[A-Za-zà-ÿ'’-]+", w) for w in words):
        if not any(w.lower() in _NAME_STOP or _is_temporal(w) for w in words) and not canonical_city(t):
            return " ".join(w[:1].upper() + w[1:] for w in words)
    return None


FLIGHT_ID_RE = re.compile(r"\bFL-[A-Z]{2,4}-[A-Z0-9]{2,6}\b", re.I)
BOOKING_ID_RE = re.compile(r"\bBK-\d{2,8}\b", re.I)
GENERIC_ID_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{2,6})?\b")

DEVICE_WORDS = {
    "tv": "TV", "television": "TV", "telly": "TV", "phone": "phone", "smartphone": "phone", "mobile": "phone",
    "cell": "phone", "washer": "washer", "washing machine": "washer", "dryer": "dryer", "laptop": "laptop",
    "notebook": "laptop", "computer": "computer", "pc": "computer", "fridge": "fridge", "refrigerator": "fridge",
    "router": "router", "modem": "router", "printer": "printer", "monitor": "monitor", "tablet": "tablet",
    "camera": "camera", "speaker": "speaker", "soundbar": "soundbar", "watch": "watch", "headphones": "headphones",
    "earbuds": "earbuds", "console": "console", "microwave": "microwave", "dishwasher": "dishwasher",
    "oven": "oven", "projector": "projector", "thermostat": "thermostat",
}
_DEVICE_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, DEVICE_WORDS), key=len, reverse=True)) + r")\b", re.I)


def find_device(text: str) -> str | None:
    m = _DEVICE_RE.search(text)
    return DEVICE_WORDS[m[1].lower()] if m else None


def find_enum_mention(text: str, values: Iterable[str]) -> str | None:
    """A literal enum value in the text (case-insensitive, word-bounded)."""
    low = text.lower()
    best = None
    for v in values:
        if not isinstance(v, str) or not v:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(v.lower())}(?![a-z0-9])", low):
            if best is None or len(v) > len(best):
                best = v
    return best


@lru_cache(maxsize=4096)
def phonetic_key(word: str) -> str:
    """A crude consonant skeleton used to spot confusable place names (Boston/Austin)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "kw").replace("x", "ks")
    w = re.sub(r"[ck](?=[eiy])", "s", w)
    w = w.replace("c", "k").replace("z", "s").replace("v", "f")
    w = re.sub(r"[aeiouyhw]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def confusable_cities(city: str, limit: int = 2) -> list[str]:
    """Known cities that sound close to `city`, most similar first."""
    key = phonetic_key(city)
    if not key:
        return []
    scored = []
    for other in _CITIES:
        if other == city:
            continue
        okey = phonetic_key(other)
        if not okey:
            continue
        dist = _edit_distance(key, okey)
        tail = 0 if key[-2:] == okey[-2:] else 1
        vowel_tail = 0 if city.lower()[-3:-1] == other.lower()[-3:-1] or city.lower()[-2:] == other.lower()[-2:] else 1
        if dist <= 1 and abs(len(key) - len(okey)) <= 1:
            scored.append((dist + tail + 0.5 * vowel_tail, other))
    scored.sort()
    return [name for _, name in scored[:limit]]


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
