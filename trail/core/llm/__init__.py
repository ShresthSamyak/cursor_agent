"""Model clients. The core runs with none of them: rules first, models settle the remainder.

Roles (PDF p. 13): a small local model for reflexes (ambiguous interrupts,
routing, vision fallback) and a cloud model for reasoning. The harness path
must work with the cloud model alone, or with rules alone.
"""

from .base import LLM, ModelError, extract_json
from .select import select_llm

__all__ = ["LLM", "ModelError", "extract_json", "select_llm"]
