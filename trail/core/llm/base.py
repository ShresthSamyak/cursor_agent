from __future__ import annotations

import json
import re
from typing import Any, Protocol


class ModelError(Exception):
    """A model call failed. Messages never include request bodies or keys."""


class LLM(Protocol):
    name: str
    supports_images: bool
    supports_audio: bool

    async def json(self, system: str, prompt: str, *, images: list[str] | None = None,
                   timeout: float = 8.0) -> dict[str, Any] | None:
        """Return a JSON object, or None on failure. Must honour cancellation."""
        ...

    async def transcribe(self, path: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
        """{'text': str, 'uncertain': [words], 'alternatives': {word: [..]}} or None."""
        ...

    async def warm(self) -> bool:
        """Load weights / open connections off the clock. True when usable."""
        ...

    async def close(self) -> None:
        ...


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse the first JSON object in a model reply (tolerates fences and prose)."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.M)
    try:
        value = json.loads(t)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(t[start:i + 1])
                        return value if isinstance(value, dict) else None
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None
