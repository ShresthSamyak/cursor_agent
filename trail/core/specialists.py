"""App-aware specialists (PDF p. 10-11). Pure logic over the session trail; no desktop imports.

* Booking agent (hero): answers only from fares the user looked at, names them, and
  notices when newer evidence beats the live answer.
* Sheet join (cross-app): "can I afford it?" joins a browser fare with a budget cell.
* Code mentor (second act): secrets interrupt at once; bugs wait for a typing pause;
  drift from the declared goal waits for a natural boundary; every finding is
  re-verified against the current document before it is spoken.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from . import entities as ent
from .trail import TrailEntry, TrailStore

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
APP_SPECIALIST = {"chrome": "booking", "edge": "booking", "firefox": "booking", "browser": "booking",
                  "vscode": "code_mentor", "code": "code_mentor", "excel": "sheet", "sheet": "sheet",
                  "budget": "sheet", "pdf": "pdf_reader", "acrobat": "pdf_reader", "outlook": "calendar"}


def specialist_for(app: str, override: str | None = None) -> str:
    if override:
        return override
    a = (app or "").lower()
    for key, name in APP_SPECIALIST.items():
        if key in a:
            return name
    return "general"


_COMPARE_RE = re.compile(
    r"\b(?:which (?:one|day|date|flight|fare)?\s*(?:should|do|would|is)|which one|what'?s (?:the )?(?:cheapest|best)|"
    r"cheapest|best (?:option|deal|one)|should i book|which should)\b", re.I)
_AFFORD_RE = re.compile(r"\b(?:afford|within (?:my )?budget|over budget|enough (?:money|budget)|fit (?:in|within) (?:my )?budget)\b", re.I)


def wants_trail_compare(text: str, trail: TrailStore) -> bool:
    return bool(trail.fares()) and bool(_COMPARE_RE.search(text))


def wants_afford(text: str, trail: TrailStore) -> bool:
    return bool(_AFFORD_RE.search(text)) and bool(trail.fares())


# ---------------------------------------------------------------------------
# Booking over the trail
# ---------------------------------------------------------------------------
def _label(e: TrailEntry) -> str:
    return e.dates[0] if e.dates else (e.times[0] if e.times else e.text[:24])


def _money(amount: float, currency: str) -> str:
    return ent.format_price(amount, currency)


def fare_answer(trail: TrailStore, parse=None, *, passengers: int = 1) -> str | None:
    """'Of the three dates you checked, Saturday is cheapest at ₹5,000.'"""
    route = trail.active_route()
    fares = trail.fares(context=route)
    if not fares:
        return None
    best = min(fares, key=lambda e: e.price.amount)
    n = len(fares)
    words = {1: "the one date", 2: "the two dates", 3: "the three dates", 4: "the four dates", 5: "the five dates"}
    seen = words.get(n, f"the {n} dates")
    price = best.price
    head = f"Of {seen} you checked, {_label(best)} is cheapest at {_money(price.amount, price.currency)}"
    if passengers > 1:
        head += f", so {passengers} tickets come to {_money(price.amount * passengers, price.currency)}"
    others = sorted((e for e in fares if e is not best), key=lambda e: e.price.amount)
    if others:
        nxt = others[0]
        head += f"; next is {_label(nxt)} at {_money(nxt.price.amount, nxt.price.currency)}"
    return head + "."


def afford_answer(trail: TrailStore, *, passengers: int = 1) -> str | None:
    """Join the cheapest browser fare with the budget cell the user inspected."""
    route = trail.active_route()
    best = trail.cheapest(context=route)
    budget = trail.budget_cell()
    if best is None:
        return None
    if budget is None:
        return "I can see the fares you checked, but not your budget yet. Point me at the remaining-budget cell."
    cost = best.price.amount * passengers
    left = budget.price.amount
    cur = best.price.currency
    who = f"{passengers} tickets on {_label(best)} come to" if passengers > 1 else f"{_label(best)}'s fare is"
    if cost <= left:
        return (f"Yes: {who} {_money(cost, cur)} and you have {_money(left, cur)} left for flights, "
                f"so {_money(left - cost, cur)} to spare.")
    return f"Not quite: {who} {_money(cost, cur)}, which is {_money(cost - left, cur)} over the {_money(left, cur)} you have left."


def cheaper_than(trail: TrailStore, entry: TrailEntry, best_id: str | None) -> bool:
    """Does a newly attended fare beat the one the live answer is based on?"""
    if not entry.prices:
        return False
    route = trail.active_route()
    best = trail.cheapest(context=route)
    return best is not None and best.id == entry.id and best.id != best_id


# ---------------------------------------------------------------------------
# Code mentor
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    key: str
    tier: str                       # critical, high, normal, low
    file: str
    line: int
    line_hash: str
    doc_version: int
    message: str                    # fix-mode text
    question: str                   # teach-mode text
    rule: str

    def text(self, mode: str) -> str:
        return self.question if mode == "teach" and self.question else self.message


_SECRET_PATTERNS = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("assigned_secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|client[_-]?secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{16,}['\"]")),
]

_BUG_RULES: list[tuple[str, re.Pattern, str, str, str]] = [
    # (rule, pattern, tier, fix message, teach question)
    ("none_deref", re.compile(r"^\s*(?:return\s+)?(\w+)\.(\w+)\b(?!\s*\()"), "high",
     "`{0}` can be None here if the lookup found nothing; guard it before reading `.{1}`.",
     "What happens on this line if `{0}` is None?"),
    ("eq_none", re.compile(r"[=!]=\s*None\b"), "normal", "Use `is None` / `is not None` instead of comparing with ==.",
     "Is `==` the right way to check for None?"),
    ("bare_except", re.compile(r"^\s*except\s*:"), "high", "A bare `except:` swallows every error, including KeyboardInterrupt. Catch the specific exception.",
     "Which errors do you actually expect here?"),
    ("plain_password_compare", re.compile(r"(?i)password\w*\s*==|==\s*\w*password"), "high",
     "Comparing secrets with == leaks timing; use hmac.compare_digest, and compare hashes, never plaintext.",
     "What could an attacker learn from how long this comparison takes?"),
    ("tls_off", re.compile(r"verify\s*=\s*False"), "high", "`verify=False` turns off TLS certificate checks; remove it.",
     "What does `verify=False` let a man-in-the-middle do?"),
    ("weak_random", re.compile(r"\brandom\.(?:random|randint|choice)\("), "normal",
     "Use the `secrets` module for tokens and state values; `random` is predictable.", "Is `random` safe for a security token?"),
    ("sql_format", re.compile(r"execute\(\s*f?[\"'].*(?:\{|%s|\+)"), "high",
     "This builds SQL from strings; use a parameterised query.", "What happens if the input contains a quote?"),
]
_DRIFT_RULES: list[tuple[str, re.Pattern, str]] = [
    ("jwt_by_hand", re.compile(r"\.split\(\s*['\"]\.['\"]\s*\)|b64decode\([^)]*split|verify_signature['\"]?\s*:\s*False"),
     "This parses the JWT by hand, which drifts from the OAuth login you declared: verify it with the provider's library "
     "(signature, issuer, audience, expiry) instead."),
    ("implicit_flow", re.compile(r"response_type\s*=\s*['\"]token['\"]"),
     "The implicit flow is deprecated for OAuth login; use the authorization-code flow with PKCE."),
    ("state_missing", re.compile(r"authorize\?[^\"']*client_id(?![^\"']*state=)"),
     "The authorization URL has no `state` parameter, so the login is open to CSRF."),
]


def line_hash(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8", "replace")).hexdigest()[:12]


@dataclass
class CodeMentor:
    goal: str | None = None
    mode: str = "teach"
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)     # file -> {version, lines}
    hovered: list[str] = field(default_factory=list)
    diagnosis: dict[str, Any] | None = None
    dropped_as_fixed: list[str] = field(default_factory=list)

    def declare(self, text: str) -> str | None:
        m = re.search(r"\b(?:i'?m|i am|we'?re)\s+(?:building|writing|working on|implementing|adding)\s+(.+?)[.!]?$", text, re.I)
        if m:
            self.goal = m[1].strip()
        return self.goal

    def update(self, file: str, version: int, text: str) -> None:
        self.docs[file] = {"version": version, "lines": text.splitlines()}

    def still_there(self, f: Finding) -> bool:
        """Staleness check just before speaking: is the offending line unchanged?"""
        doc = self.docs.get(f.file)
        if doc is None:
            return False
        lines = doc["lines"]
        idx = f.line - 1
        for j in (idx, idx - 1, idx + 1):          # tolerate a line shifted by one
            if 0 <= j < len(lines) and line_hash(lines[j]) == f.line_hash:
                return True
        self.dropped_as_fixed.append(f.key)
        return False

    def check(self, file: str, version: int, changed: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> list[Finding]:
        out: list[Finding] = []
        doc = self.docs.get(file, {"lines": []})
        lines = doc["lines"]
        targets = changed or [{"line": i + 1, "text": t} for i, t in enumerate(lines)]
        for ch in targets:
            n = int(ch.get("line") or 0)
            text = str(ch.get("text") if ch.get("text") is not None else (lines[n - 1] if 0 < n <= len(lines) else ""))
            if not text.strip() or text.strip().startswith("#"):
                continue
            h = line_hash(text)
            for name, pat in _SECRET_PATTERNS:
                if pat.search(text):
                    out.append(Finding(f"{file}:{n}:{name}", "critical", file, n, h, version,
                                       f"That looks like a live credential ({name.replace('_', ' ')}) pasted into {file} line {n}. "
                                       "Move it to an environment variable and rotate it.", "", name))
                    break
            for rule, pat, tier, msg, q in _BUG_RULES:
                m = pat.search(text)
                if not m:
                    continue
                if rule == "none_deref" and not self._maybe_none(lines, n, m[1]):
                    continue
                groups = [g for g in m.groups()] if m.groups() else []
                out.append(Finding(f"{file}:{n}:{rule}", tier, file, n, h, version, msg.format(*groups, *[""] * 2),
                                   q.format(*groups, *[""] * 2), rule))
            if self.goal and re.search(r"oauth|login|auth|sign.?in", self.goal, re.I):
                for rule, pat, msg in _DRIFT_RULES:
                    if pat.search(text):
                        out.append(Finding(f"{file}:{n}:{rule}", "normal", file, n, h, version, msg,
                                           "Is hand-parsing this the OAuth way, or should the provider's library verify it?", rule))
        for d in diagnostics or []:
            if str(d.get("severity", "")).lower() in {"error", "0"}:
                n = int(d.get("line") or 0)
                text = lines[n - 1] if 0 < n <= len(lines) else ""
                out.append(Finding(f"{file}:{n}:lint", "high", file, n, line_hash(text), version,
                                   f"The linter flags line {n}: {d.get('message', 'error')}.", "", "lint"))
        return out

    @staticmethod
    def _maybe_none(lines: list[str], n: int, name: str) -> bool:
        """`name` was just assigned from a lookup that can return None, with no guard since."""
        start = max(0, n - 8)
        window = lines[start:n - 1]
        assigned = any(re.search(rf"\b{re.escape(name)}\s*=\s*.*\b(?:get|find|first|lookup|fetch|query|one_or_none|load)\w*\(", l)
                       for l in window)
        guarded = any(re.search(rf"\bif\s+(?:not\s+)?{re.escape(name)}\b|{re.escape(name)}\s+is\s+(?:not\s+)?None|"
                                rf"assert\s+{re.escape(name)}", l) for l in window)
        return assigned and not guarded

    # ---- terminal pre-diagnosis ---------------------------------------------
    def on_terminal(self, text: str) -> dict[str, Any] | None:
        """A stack trace starts a speculative diagnosis before the user asks."""
        if not re.search(r"Traceback \(most recent call last\)|\bError\b|Exception|FAILED|AssertionError", text):
            return None
        frames = re.findall(r'File "([^"]+)", line (\d+), in (\w+)', text)
        exc = re.findall(r"^(\w+(?:Error|Exception|Exit)?):\s*(.*)$", text, re.M)
        err = exc[-1] if exc else ("Error", text.strip().splitlines()[-1][:160] if text.strip() else "")
        where = frames[-1] if frames else None
        own = [f for f in frames if not re.search(r"site-packages|lib[/\\]python", f[0])]
        if own:
            where = own[-1]
        file = where[0].replace("\\", "/").split("/")[-1] if where else None
        line = int(where[1]) if where else None
        code = ""
        if file and file in self.docs and line and 0 < line <= len(self.docs[file]["lines"]):
            code = self.docs[file]["lines"][line - 1].strip()
        hint = ""
        if err[0] == "AttributeError" and "NoneType" in err[1]:
            hint = " Something returned None where an object was expected, so guard the lookup or fail with a clear error."
        elif err[0] in {"KeyError"}:
            hint = " A dictionary key is missing; check the key or use .get with a default."
        elif err[0] == "AssertionError":
            hint = " The assertion's expected and actual values differ; the diff above shows which."
        at = f" at {file} line {line}" if file else ""
        snippet = f" (`{code}`)" if code else ""
        related = [h for h in self.hovered if file and file in h][:1]
        seen = " You were looking at that file just before, too." if related else ""
        self.diagnosis = {"file": file, "line": line, "error": err[0], "detail": err[1],
                          "answer": f"It broke with {err[0]}: {err[1]}{at}{snippet}.{hint}{seen}".replace("..", ".")}
        return self.diagnosis
