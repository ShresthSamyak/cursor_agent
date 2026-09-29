"""Defence in depth for explicit context; desktop source filtering comes later."""

import re

from .bus import Target

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)


def _is_luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit = digit * 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return total % 10 == 0


def redact(text: str) -> str:
    text = _SECRET.sub("[redacted secret]", text)
    return _CARD.sub(lambda m: "[redacted card]" if _is_luhn(m[0]) else m[0], text)


def safe_target(target: Target) -> Target | None:
    if target.sensitive or target.role.lower() in {"password", "payment", "otp"}:
        return None
    return target.model_copy(update={"text": redact(target.text), "context": redact(target.context)})
