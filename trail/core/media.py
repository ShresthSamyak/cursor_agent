"""Raw media: speech to text with confidence, frame understanding, frame embeddings.

Heavy resources load in setup() and are cached at module level, so every
scenario after the first starts warm (docs/kit/PROTOCOL.md section 5.3).
Nothing here blocks the event loop: model work runs in worker threads.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import entities as ent

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WHISPER = None


def resolve_media(ref: str | None) -> Path | None:
    """Kit media paths are relative to the kit root (the harness cwd)."""
    if not ref or not isinstance(ref, str) or "\x00" in ref:
        return None
    p = Path(ref)
    roots = [Path.cwd(), _REPO_ROOT]
    extra = os.environ.get("TRAIL_MEDIA_ROOT")
    if extra:
        roots.insert(0, Path(extra))
    candidates = [p] if p.is_absolute() else [r / p for r in roots]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------
@dataclass
class Transcript:
    text: str
    words: list[tuple[str, float]] = field(default_factory=list)
    avg_logprob: float = 0.0
    source: str = "whisper"
    uncertain: dict[str, list[str]] = field(default_factory=dict)   # heard value -> alternatives

    def word_confidence(self, phrase: str) -> float:
        """Lowest probability among the words that make up `phrase` (1.0 if unknown)."""
        target = [w for w in re.findall(r"[a-z0-9']+", phrase.lower()) if w]
        if not target or not self.words:
            return 1.0
        probs = []
        toks = [(re.sub(r"[^a-z0-9']", "", w.lower()), p) for w, p in self.words]
        for t in target:
            hits = [p for w, p in toks if w == t]
            if hits:
                probs.append(max(hits))
        return min(probs) if probs else 1.0


def _cuda_dll_dirs() -> list[str]:
    """Where CUDA 12 / cuDNN 9 runtime DLLs may already live (pip wheels or a PyTorch install)."""
    import sys

    found: list[str] = []
    env = os.environ.get("TRAIL_CUDA_DLL_DIR")
    if env:
        found.extend(p for p in env.split(os.pathsep) if p)
    roots = {Path(p) for p in sys.path if p} | {Path(sys.base_prefix) / "Lib" / "site-packages",
                                                 Path(sys.prefix) / "Lib" / "site-packages"}
    for root in roots:
        try:
            for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin", "torch/lib"):
                d = root / sub
                if (d / "cublas64_12.dll").exists() or (d / "cudnn_ops64_9.dll").exists() or sub.startswith("nvidia") and d.is_dir():
                    found.append(str(d))
        except OSError:
            continue
    return list(dict.fromkeys(found))


def _load_whisper(name: str | None):
    from faster_whisper import WhisperModel

    device, compute = "cpu", "int8"
    want = os.environ.get("TRAIL_WHISPER_DEVICE", "auto")
    try:
        import ctranslate2

        if want != "cpu" and ctranslate2.get_cuda_device_count() > 0:
            if os.name == "nt":
                for d in _cuda_dll_dirs():
                    try:
                        os.add_dll_directory(d)
                        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                    except OSError:
                        pass
            device, compute = "cuda", "float16"
    except Exception:
        pass
    # small.en is accurate and ~0.2 s on a laptop GPU; base.en keeps CPU-only hosts inside the budget.
    chosen = name or ("small.en" if device == "cuda" else "base.en")
    if device == "cuda":
        try:
            model = WhisperModel(chosen, device="cuda", compute_type=compute)
            list(model.transcribe(_silence_wav(), language="en")[0])   # surface missing DLLs now
            return model, "cuda", chosen
        except Exception:
            chosen = name or "base.en"
    return WhisperModel(chosen, device="cpu", compute_type="int8"), "cpu", chosen


def _silence_wav() -> Any:
    import numpy as np

    return np.zeros(16000, dtype="float32")


STT_INFO: dict[str, str] = {}


async def load_stt() -> bool:
    global _WHISPER
    if _WHISPER is not None:
        return True
    if os.environ.get("TRAIL_STT", "whisper") == "none":
        return False
    try:
        model, device, name = await asyncio.to_thread(_load_whisper, os.environ.get("TRAIL_WHISPER") or None)
        _WHISPER = model
        STT_INFO.update(device=device, model=name)
        # Warm the decoder once so the first real clip is not slow.
        await asyncio.to_thread(lambda: list(model.transcribe(_silence_wav(), language="en")[0]))
        return True
    except Exception:
        _WHISPER = None
        return False


def stt_ready() -> bool:
    return _WHISPER is not None


def asr_prompt(tool_names: list[str], enums: list[str]) -> str:
    """Domain vocabulary for decoding: what this assistant can do and the places it knows.

    Built from the tool manifest and the gazetteer, never from scenario text.
    """
    actions = ", ".join(n.replace("_", " ") for n in tool_names[:12]) or "book a flight, find flights"
    places = ", ".join(ent.city_names()[:40])
    models = f" Device models: {', '.join(enums[:12])}." if enums else ""
    return f"Voice requests to an assistant that can: {actions}. Places: {places}.{models}"


def _transcribe_sync(path: str, prompt: str | None) -> Transcript:
    kw = {"initial_prompt": prompt} if prompt else {}
    segments, _info = _WHISPER.transcribe(
        path, language="en", beam_size=5, word_timestamps=True, vad_filter=False,
        condition_on_previous_text=False, temperature=0.0, **kw)
    segments = list(segments)
    kept = []
    for i, s in enumerate(segments):
        probs = [float(w.probability) for w in (s.words or [])]
        # Trailing low-confidence fragments are decoder hallucinations (often echoes of the prompt).
        if i > 0 and s.avg_logprob < -1.0 and (not probs or max(probs) < 0.3):
            continue
        kept.append(s)
    words: list[tuple[str, float]] = []
    for s in kept:
        for w in s.words or []:
            words.append((w.word.strip(), float(w.probability)))
    text = " ".join(s.text.strip() for s in kept).strip()
    avg = sum(s.avg_logprob for s in kept) / len(kept) if kept else -5.0
    return Transcript(text=text, words=words, avg_logprob=avg)


async def transcribe(path: Path, llm=None, *, timeout: float = 8.0, prompt: str | None = None) -> Transcript | None:
    """Local whisper first (word confidences); a cloud audio model otherwise."""
    if _WHISPER is not None:
        try:
            return await asyncio.to_thread(_transcribe_sync, str(path), prompt)
        except Exception:
            pass
    if llm is not None and getattr(llm, "supports_audio", False):
        try:
            out = await llm.transcribe(str(path), timeout=timeout)
        except Exception:
            out = None
        if out and isinstance(out.get("text"), str):
            unc: dict[str, list[str]] = {}
            for item in out.get("uncertain") or []:
                if isinstance(item, dict) and isinstance(item.get("word"), str):
                    alts = [a for a in item.get("alternatives") or [] if isinstance(a, str)]
                    unc[item["word"]] = alts
            return Transcript(text=out["text"], source=llm.name, uncertain=unc)
    return None


# Place names below this confidence are confirmed before any tool uses them.
PLACE_CONFIDENCE = float(os.environ.get("TRAIL_PLACE_CONFIDENCE", "0.80"))


def shaky_places(tr: Transcript) -> list[tuple[str, list[str]]]:
    """Places the recogniser was unsure about, with the confusable alternatives."""
    out = []
    for place in ent.find_places(tr.text):
        alts = ent.confusable_cities(place.name) if place.known else []
        for heard, options in tr.uncertain.items():
            if heard.lower() in place.name.lower():
                alts = [a for a in options if a.lower() != place.name.lower()] or alts
                out.append((place.name, alts))
                break
        else:
            conf = tr.word_confidence(place.name)
            if tr.words and conf < PLACE_CONFIDENCE and (alts or conf < 0.5):
                out.append((place.name, alts))
    return out


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------
def frame_embedding(path: Path) -> list[float] | None:
    """A compact, deterministic visual descriptor of the frame (our own embedding).

    8x8 luminance layout + 4x4 colour layout + 12-bin hue histogram, L2-normalised.
    Cheap enough to compute on arrival; used for hybrid manual search.
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            gray = im.convert("L").resize((8, 8))
            color = im.resize((4, 4))
            hsv = im.resize((64, 64)).convert("HSV")
            vec: list[float] = [v / 255.0 for v in gray.getdata()]
            for r, g, b in color.getdata():
                vec.extend((r / 255.0, g / 255.0, b / 255.0))
            hist = [0.0] * 12
            for h, s, v in hsv.getdata():
                if s > 40 and v > 40:
                    hist[min(h * 12 // 256, 11)] += 1
            total = sum(hist) or 1.0
            vec.extend(x / total for x in hist)
    except Exception:
        return None
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 5) for x in vec]


_VISION_SYSTEM = (
    "You look at one camera frame for a voice assistant. Reply with JSON only. "
    "Be literal: read printed labels and logos, do not guess brand names."
)
_VISION_PROMPT = (
    "The user is pointing their camera at something and will ask about it. "
    "Identify the device, and the one component the user is showing you: the component in the sharpest focus "
    "and most prominent in the photo (not simply the one nearest the geometric center). Read the printed label "
    "next to it, if there is one, and name it from that label. Reply as JSON: "
    '{"device_type": "laptop|tv|phone|washer|router|other", "device_model": "model text printed on the device, or null", '
    '"focus": "short name of the component the user is showing, e.g. \\"power button\\"", '
    '"components": ["every visible component, left to right"], "visible_text": ["printed text you can read"], '
    '"summary": "one sentence describing the frame"}'
)


async def describe_frame(path: Path, llm, *, question: str | None = None, timeout: float = 12.0) -> dict[str, Any] | None:
    if llm is None or not getattr(llm, "supports_images", False):
        return None
    prompt = _VISION_PROMPT
    if question:
        prompt += f"\nThe user's question about the frame: {question!r}"
    try:
        out = await llm.json(_VISION_SYSTEM, prompt, images=[str(path)], timeout=timeout)
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    for key in ("device_model", "focus", "device_type"):
        if isinstance(out.get(key), str) and out[key].strip().lower() in {"", "null", "none", "unknown", "n/a"}:
            out[key] = None
    focus = out.get("focus")
    if isinstance(focus, str):
        out["focus"] = _clean_focus(focus)
    return out


def _clean_focus(focus: str) -> str:
    f = re.sub(r"\s+", " ", focus).strip(" .\"'")
    f = re.sub(r"^(?:the|a|an)\s+", "", f, flags=re.I)
    low = f.lower()
    for label, canon in (("hdmi", "HDMI port"), ("thunderbolt", "Thunderbolt port"), ("usb-c", "USB-C port"),
                         ("usb c", "USB-C port"), ("type-c", "USB-C port"), ("usb-a", "USB-A port"),
                         ("usb", "USB port"), ("ethernet", "Ethernet port"), ("rj45", "Ethernet port"),
                         ("headphone", "headphone jack"), ("audio jack", "headphone jack"),
                         ("displayport", "DisplayPort"), ("sd card", "SD card slot"), ("power", "power port")):
        if label in low and ("port" in low or "jack" in low or "slot" in low or len(low) <= 12):
            return canon
    return f[:60]
