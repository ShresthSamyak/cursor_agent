"""Voice for desktop mode: VAD barge-in, streaming partial transcripts, and interruptible TTS.

    python -m trail speech [--token trail-dev]

* VAD: frame energy with an adaptive noise floor and hysteresis; `vad_start` is sent within one
  30 ms frame of speech onset (the PDF's "speech onset to output ducked < 150 ms" path needs no model).
* STT: faster-whisper on the rolling utterance buffer every ~600 ms -> `speech_partial`, and once more
  at end of speech -> `speech_final`. Uses the same GPU/CPU loader and domain prompt as the kit path.
* TTS: Windows SAPI via PowerShell (no extra dependency). A `duck` or a new answer kills playback at once.
* Room noise: the first second calibrates the noise floor; an utterance needs >= 240 ms of loud frames, and
  Whisper's stock hallucinations on non-speech ("Thank you.", "Thanks for watching") or low-confidence
  segments are dropped. While Trail is talking, barge-in needs a louder onset so its own voice from the
  speakers does not interrupt it. `TRAIL_MIC_MIN_RMS` overrides the absolute onset level.
* `--wake` (noisy rooms, a video playing): only requests addressed as "Trail, ..." are sent, plus follow-ups
  within FOLLOW_UP_S of Trail speaking (so "Actually, two passengers" after an answer still works).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections import deque

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME = SAMPLE_RATE * FRAME_MS // 1000
PROMPT: str | None = None
MIN_VOICED_FRAMES = 8          # 240 ms of loud frames before an utterance counts
HALLUCINATIONS = re.compile(
    r"^(?:(?:thank you|thanks)(?: (?:very|so) much)?(?: for watching)?|you|bye|oh|um+|uh+|hmm+|so|"
    r"okay|wow|eh|ah|(?:please )?(?:like and )?subscribe.*|subtitles by.*|i don't know|i'm sorry)[.!?…]*$", re.I)
WAKE = re.compile(r"^\W*(?:(?:hey|hi|ok|okay)[,\s]+)?(?:trail|trial|trel|tray?l)\b[,.!?:\s]*", re.I)
FOLLOW_UP_S = 8.0


def addressed(text: str, since_trail_spoke: float) -> str | None:
    """Wake-word gate: the request with the wake word removed, or None if it was not meant for Trail."""
    m = WAKE.match(text)
    if m:
        rest = text[m.end():].strip()
        return rest or None
    return text if since_trail_spoke <= FOLLOW_UP_S else None


def keep_transcript(text: str, segments) -> bool:
    """Drop Whisper's stock outputs on non-speech and segments it is itself unsure about."""
    t = text.strip()
    if not t or HALLUCINATIONS.match(t):
        return False
    segs = list(segments)
    if segs and all(getattr(x, "no_speech_prob", 0.0) > 0.6 or getattr(x, "avg_logprob", 0.0) < -1.0 for x in segs):
        return False
    return True


class EnergyVAD:
    """Onset after 2 loud frames, offset after ~600 ms of quiet; the noise floor adapts."""

    def __init__(self, *, ratio: float = 3.0, onset_frames: int = 3, hang_frames: int = 20,
                 min_rms: float = 0.006) -> None:
        self.floor = 1e-4
        self.ratio = ratio
        self.onset_frames = onset_frames
        self.hang_frames = hang_frames
        self.min_rms = min_rms
        self.boost = 1.0            # raised while Trail's own voice plays through the speakers
        self.loud = 0
        self.quiet = 0
        self.voiced = 0             # loud frames in the current utterance
        self.speaking = False

    def calibrate(self, levels: list[float]) -> None:
        """Seed the floor from ambient frames so background talk sits below the onset level."""
        if levels:
            srt = sorted(levels)
            self.floor = max(1e-4, srt[len(srt) // 2])
            self.min_rms = max(self.min_rms, 1.5 * srt[min(len(srt) - 1, int(len(srt) * 0.98))])

    def threshold(self) -> float:
        return max(self.floor * self.ratio, self.min_rms) * self.boost

    def step(self, rms: float) -> str | None:
        if not self.speaking:
            self.floor = 0.97 * self.floor + 0.03 * min(rms, self.floor * 4)     # speech bursts don't drag the floor up
        loud = rms > self.threshold()
        if loud:
            self.loud += 1
            self.quiet = 0
            if self.speaking:
                self.voiced += 1
        else:
            self.quiet += 1
            self.loud = 0
        if not self.speaking and self.loud >= self.onset_frames:
            self.speaking = True
            self.voiced = self.loud
            return "start"
        if self.speaking and self.quiet >= self.hang_frames:
            self.speaking = False
            return "end"
        return None


class Speaker:
    """Interruptible TTS through Windows SAPI (System.Speech)."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None

    def say(self, text: str) -> None:
        self.stop()
        if sys.platform != "win32" or not text.strip():
            return
        safe = text.replace("'", "''")[:1200]
        cmd = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               f"$s.Rate = 1; $s.Speak('{safe}')")
        self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def playing(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)


async def run(url: str, token: str, *, wake: bool = False) -> None:
    import numpy as np
    import sounddevice as sd
    from websockets.asyncio.client import connect

    from ..core import media
    from .tools import MANIFEST

    global PROMPT
    PROMPT = "Trail. " + media.asr_prompt(list(MANIFEST), [])      # the wake word + the kit path's domain vocabulary
    await media.load_stt()
    loop = asyncio.get_running_loop()
    frames: asyncio.Queue = asyncio.Queue()
    vad = EnergyVAD(min_rms=float(os.environ.get("TRAIL_MIC_MIN_RMS") or 0.006))
    speaker = Speaker()
    utter: list = []
    pre = deque(maxlen=10)          # 300 ms of pre-roll so the first syllable is kept
    last_spoke = [float("-inf")]    # monotonic time Trail last started talking

    def on_audio(indata, _frames, _time, _status) -> None:
        loop.call_soon_threadsafe(frames.put_nowait, indata[:, 0].copy())

    async with connect(f"{url}?client=speech&token={token}") as ws:
        await ws.send(json.dumps({"type": "hello", "client": "speech", "version": 1, "app": "speech"}))

        async def listen_bridge() -> None:
            async for raw in ws:
                f = json.loads(raw)
                o = f.get("output") or {}
                if o.get("type") == "duck":
                    speaker.stop()                       # yield the floor immediately
                elif o.get("type") == "speak_end" or (o.get("type") == "speak" and o.get("kind") in {"final", "clarify", "notice"}):
                    last_spoke[0] = time.monotonic()
                    speaker.say(o.get("text", ""))

        async def transcribe(final: bool) -> None:
            if not utter or not media.stt_ready():
                return
            audio = np.concatenate(utter).astype("float32")
            peak = float(np.abs(audio).max()) or 1.0
            audio = audio * min(20.0, 0.5 / peak)          # quiet laptop mics: normalise before Whisper
            tr = await asyncio.to_thread(lambda: media._WHISPER.transcribe(audio, language="en", beam_size=1 if not final else 5,
                                                                          condition_on_previous_text=False,
                                                                          initial_prompt=PROMPT)[0])
            segs = await asyncio.to_thread(list, tr)
            text = " ".join(s.text.strip() for s in segs).strip()
            if not keep_transcript(text, segs):
                if final and text:
                    print(f"dropped (noise/hallucination): {text}", flush=True)
                return
            if wake:
                since = 0.0 if speaker.playing() else time.monotonic() - last_spoke[0]
                req = addressed(text, since)
                if req is None:
                    if final:
                        print(f"ignored (not addressed to Trail): {text}", flush=True)
                    return
                text = req
            if text:
                if final:
                    print(f"heard: {text}", flush=True)
                await ws.send(json.dumps({"type": "event", "event": {"type": "speech_final" if final else "speech_partial",
                                                                        "text": text, "source": "speech"}}))

        bridge_task = asyncio.create_task(listen_bridge())
        last_partial = 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME, dtype="float32", callback=on_audio):
            print("calibrating room noise (stay quiet for a second)...", flush=True)
            ambient = []
            for _ in range(1000 // FRAME_MS):
                chunk = await frames.get()
                ambient.append(float(np.sqrt(np.mean(chunk * chunk))))
            vad.calibrate(ambient)
            print(f"listening (onset level {vad.threshold():.4f}; Ctrl+C to stop)", flush=True)
            while True:
                chunk = await frames.get()
                rms = float(np.sqrt(np.mean(chunk * chunk)))
                vad.boost = 2.5 if speaker.playing() else 1.0      # our own voice through the speakers is not a barge-in
                edge = vad.step(rms)
                if edge == "start":
                    speaker.stop()
                    utter = list(pre)
                    await ws.send(json.dumps({"type": "event", "event": {"type": "vad_start", "source": "speech"}}))
                if vad.speaking:
                    utter.append(chunk)
                    if time.monotonic() - last_partial > 0.6 and len(utter) > 10:
                        last_partial = time.monotonic()
                        asyncio.create_task(transcribe(False))
                else:
                    pre.append(chunk)
                if edge == "end":
                    await ws.send(json.dumps({"type": "event", "event": {"type": "vad_end", "source": "speech"}}))
                    if vad.voiced >= MIN_VOICED_FRAMES:
                        await transcribe(True)
                    utter = []
        bridge_task.cancel()


def main(token: str | None = None, port: int = 8765, *, wake: bool = False) -> None:
    token = token or os.environ.get("TRAIL_TOKEN") or "trail-dev"
    try:
        asyncio.run(run(f"ws://127.0.0.1:{port}/ws", token, wake=wake))
    except KeyboardInterrupt:
        pass
