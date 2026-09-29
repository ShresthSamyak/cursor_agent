"""Cloud models: Gemini (text, image, audio) and any OpenAI-compatible endpoint.

Keys come only from environment variables (SECRET_* on the event portal).
Error messages never include keys or request bodies.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

from .base import ModelError, extract_json

_TRANSCRIBE_PROMPT = (
    "Transcribe this short voice command exactly as spoken, including hesitations and self-corrections. "
    "Then list any words you are not sure about (for example a place name that could be heard two ways) "
    "with the plausible alternatives. Reply as JSON: "
    '{"text": "...", "uncertain": [{"word": "...", "alternatives": ["..."]}]}'
)


class GeminiLLM:
    supports_images = True
    supports_audio = True

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._key = api_key
        self.model = model
        self.name = f"gemini:{model}"
        self._client = None

    def _c(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._key)
        return self._client

    async def warm(self) -> bool:
        try:
            self._c()
            return True
        except Exception:
            return False

    async def _generate(self, parts: list[Any], system: str, timeout: float) -> str:
        from google.genai import types

        cfg = types.GenerateContentConfig(system_instruction=system, temperature=0, seed=7,
                                          response_mime_type="application/json")
        try:
            resp = await asyncio.wait_for(
                self._c().aio.models.generate_content(model=self.model, contents=parts, config=cfg), timeout)
            return resp.text or ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ModelError(f"gemini call failed: {type(exc).__name__}") from None

    async def json(self, system: str, prompt: str, *, images: list[str] | None = None,
                   timeout: float = 8.0) -> dict[str, Any] | None:
        from google.genai import types

        parts: list[Any] = []
        for path in images or []:
            data = await asyncio.to_thread(Path(path).read_bytes)
            parts.append(types.Part.from_bytes(data=data, mime_type=mimetypes.guess_type(path)[0] or "image/png"))
        parts.append(prompt)
        return extract_json(await self._generate(parts, system, timeout))

    async def transcribe(self, path: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
        from google.genai import types

        data = await asyncio.to_thread(Path(path).read_bytes)
        mime = mimetypes.guess_type(path)[0] or "audio/mpeg"
        out = extract_json(await self._generate([types.Part.from_bytes(data=data, mime_type=mime), _TRANSCRIBE_PROMPT],
                                                "You are a careful speech transcriber.", timeout))
        return out

    async def close(self) -> None:
        self._client = None


class OpenAICompatLLM:
    """OpenRouter or any OpenAI-compatible chat endpoint."""

    supports_audio = False

    def __init__(self, api_key: str, model: str, *, base_url: str = "https://openrouter.ai/api/v1",
                 vision: bool = True) -> None:
        self._key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"openai-compat:{model}"
        self.supports_images = vision
        self._client = None

    def _http(self):
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(30.0, connect=3.0),
                                             headers={"Authorization": f"Bearer {self._key}"})
        return self._client

    async def warm(self) -> bool:
        return bool(self._key)

    async def json(self, system: str, prompt: str, *, images: list[str] | None = None,
                   timeout: float = 8.0) -> dict[str, Any] | None:
        content: list[dict[str, Any]] | str
        if images:
            content = [{"type": "text", "text": prompt}]
            for path in images:
                data = await asyncio.to_thread(Path(path).read_bytes)
                mime = mimetypes.guess_type(path)[0] or "image/png"
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
        else:
            content = prompt
        body = {"model": self.model, "temperature": 0, "seed": 7, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        try:
            r = await asyncio.wait_for(self._http().post("/chat/completions", json=body), timeout)
            r.raise_for_status()
            return extract_json(r.json()["choices"][0]["message"]["content"] or "")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ModelError(f"model call failed: {type(exc).__name__}") from None

    async def transcribe(self, path: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
        return None

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
