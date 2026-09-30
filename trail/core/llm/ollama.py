"""Local models through Ollama's HTTP API (reflexes and vision on the laptop GPU)."""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from typing import Any

from .base import ModelError, extract_json

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaLLM:
    supports_audio = False

    def __init__(self, model: str, *, vision_model: str | None = None, url: str | None = None) -> None:
        self.model = model
        self.vision_model = vision_model
        self.url = (url or os.environ.get("OLLAMA_HOST") or DEFAULT_URL).rstrip("/")
        if not self.url.startswith("http"):
            self.url = "http://" + self.url
        self.name = f"ollama:{model}"
        self.supports_images = bool(vision_model)
        self._client = None
        self._loop = None

    def _http(self):
        import asyncio
        import httpx

        loop = asyncio.get_running_loop()
        # One client per event loop: the kit runs each scenario in a fresh loop.
        if self._client is None or self._loop is not loop:
            self._client = httpx.AsyncClient(base_url=self.url, timeout=httpx.Timeout(30.0, connect=2.0))
            self._loop = loop
        return self._client

    async def available_models(self) -> set[str]:
        try:
            r = await self._http().get("/api/tags", timeout=2.0)
            r.raise_for_status()
            return {m.get("name", "") for m in r.json().get("models", [])}
        except Exception:
            return set()

    async def warm(self) -> bool:
        models = await self.available_models()
        if not models:
            return False
        def has(name: str | None) -> bool:
            return bool(name) and (name in models or f"{name}:latest" in models)
        if not has(self.model):
            return False
        if self.vision_model and not has(self.vision_model):
            self.vision_model = None
            self.supports_images = False
        # Load weights now (off the clock) and keep them resident.
        for model in {self.model, self.vision_model} - {None}:
            try:
                await self._http().post("/api/generate", json={"model": model, "prompt": "", "keep_alive": "30m"}, timeout=120.0)
            except Exception:
                pass
        return True

    async def json(self, system: str, prompt: str, *, images: list[str] | None = None,
                   timeout: float = 8.0) -> dict[str, Any] | None:
        model = self.vision_model if images else self.model
        if images and not model:
            return None
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            encoded = []
            for path in images:
                data = await asyncio.to_thread(Path(path).read_bytes)
                encoded.append(base64.b64encode(data).decode())
            message["images"] = encoded
        body = {
            "model": model, "stream": False, "format": "json", "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, message],
            "think": False,          # Gemma 4 and other thinking models: answer directly
            "options": {"temperature": 0, "seed": 7, "num_predict": 400},
        }
        try:
            r = await asyncio.wait_for(self._http().post("/api/chat", json=body), timeout)
            r.raise_for_status()
            return extract_json(r.json().get("message", {}).get("content", ""))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leak bodies
            raise ModelError(f"ollama call failed: {type(exc).__name__}") from None

    async def transcribe(self, path: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
        return None

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
