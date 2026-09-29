"""Pick a model from the environment. Never guesses at paid keys.

TRAIL_LLM:
  none                      rules only (deterministic; the default in tests)
  auto (default)            SECRET_GEMINI_API_KEY -> Gemini, SECRET_OPENROUTER_API_KEY -> OpenRouter,
                            else a local Ollama server if one answers, else rules only
  ollama[:model]            local model; TRAIL_VISION_MODEL picks the vision model
  gemini[:model]            needs SECRET_GEMINI_API_KEY or GEMINI_API_KEY
  openrouter[:model]        needs SECRET_OPENROUTER_API_KEY or OPENROUTER_API_KEY (explicit opt-in)
"""

from __future__ import annotations

import os

from .cloud import GeminiLLM, OpenAICompatLLM
from .ollama import OllamaLLM

DEFAULT_OLLAMA_TEXT = "gemma3:4b"
DEFAULT_OLLAMA_VISION = "gemma3:4b"
DEFAULT_GEMINI = "gemini-2.5-flash"
DEFAULT_OPENROUTER = "google/gemini-2.5-flash"


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


async def select_llm(choice: str | None = None):
    choice = (choice or os.environ.get("TRAIL_LLM") or "auto").strip()
    kind, _, model = choice.partition(":")
    kind = kind.lower()
    if kind == "none":
        return None
    candidates = []
    if kind in {"gemini", "auto"}:
        key = _env("SECRET_GEMINI_API_KEY", "GEMINI_API_KEY", "SECRET_GOOGLE_API_KEY")
        if key:
            candidates.append(GeminiLLM(key, model or DEFAULT_GEMINI))
        elif kind == "gemini":
            return None
    if kind in {"openrouter", "auto"}:
        names = ("SECRET_OPENROUTER_API_KEY",) if kind == "auto" else ("SECRET_OPENROUTER_API_KEY", "OPENROUTER_API_KEY")
        key = _env(*names)
        if key:
            candidates.append(OpenAICompatLLM(key, model or os.environ.get("TRAIL_OPENROUTER_MODEL") or DEFAULT_OPENROUTER))
        elif kind == "openrouter":
            return None
    if kind in {"ollama", "auto"}:
        text_model = model or os.environ.get("TRAIL_OLLAMA_MODEL") or DEFAULT_OLLAMA_TEXT
        vision = os.environ.get("TRAIL_VISION_MODEL") or DEFAULT_OLLAMA_VISION
        candidates.append(OllamaLLM(text_model, vision_model=vision))
    for llm in candidates:
        try:
            if await llm.warm():
                return llm
        except Exception:
            pass
        await llm.close()
    return None
