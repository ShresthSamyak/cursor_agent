"""Voice for desktop mode: VAD barge-in, streaming partial transcripts, and interruptible TTS.

    python -m trail speech [--token trail-dev]

* VAD: frame energy with an adaptive noise floor and hysteresis; `vad_start` is sent within one
  30 ms frame of speech onset (the PDF's "speech onset to output ducked < 150 ms" path needs no model).
* STT: faster-whisper on the rolling utterance buffer every ~600 ms -> `speech_partial`, and once more
  at end of speech -> `speech_final`. Uses the same GPU/CPU loader and domain prompt as the kit path.
* TTS: Windows SAPI via PowerShell (no extra dependency). A `duck` or a new answer kills playback at once.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections import deque

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME = SAMPLE_RATE * FRAME_MS // 1000
PROMPT: str | None = None


class EnergyVAD:
    """Onset after 2 loud frames, offset after ~600 ms of quiet; the noise floor adapts."""

    def __init__(self, *, ratio: float = 3.0, onset_frames: int = 2, hang_frames: int = 20) -> None:
        self.floor = 1e-4
        self.ratio = ratio
        self.onset_frames = onset_frames
        self.hang_frames = hang_frames
        self.loud = 0
        self.quiet = 0
        self.speaking = False

    def step(self, rms: float) -> str | None:
        if not self.speaking:
            self.floor = 0.97 * self.floor + 0.03 * rms
        loud = rms > max(self.floor * self.ratio, 0.004)
        if loud:
            self.loud += 1
            self.quiet = 0
        else:
            self.quiet += 1
            self.loud = 0
        if not self.speaking and self.loud >= self.onset_frames:
            self.speaking = True
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


async def run(url: str, token: str) -> None:
    import numpy as np
    import sounddevice as sd
    from websockets.asyncio.client import connect

    from ..core import media
    from .tools import MANIFEST

    global PROMPT
    PROMPT = media.asr_prompt(list(MANIFEST), [])      # same domain vocabulary as the kit path
    await media.load_stt()
    loop = asyncio.get_running_loop()
    frames: asyncio.Queue = asyncio.Queue()
    vad = EnergyVAD()
    speaker = Speaker()
    utter: list = []
    pre = deque(maxlen=10)          # 300 ms of pre-roll so the first syllable is kept

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
                    speaker.say(o.get("text", ""))

        async def transcribe(final: bool) -> None:
            if not utter or not media.stt_ready():
                return
            audio = np.concatenate(utter).astype("float32")
            tr = await asyncio.to_thread(lambda: media._WHISPER.transcribe(audio, language="en", beam_size=1 if not final else 5,
                                                                          condition_on_previous_text=False,
                                                                          initial_prompt=PROMPT)[0])
            text = " ".join(s.text.strip() for s in await asyncio.to_thread(list, tr)).strip()
            if text:
                await ws.send(json.dumps({"type": "event", "event": {"type": "speech_final" if final else "speech_partial",
                                                                        "text": text, "source": "speech"}}))

        bridge_task = asyncio.create_task(listen_bridge())
        last_partial = 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME, dtype="float32", callback=on_audio):
            print("listening (Ctrl+C to stop)")
            while True:
                chunk = await frames.get()
                rms = float(np.sqrt(np.mean(chunk * chunk)))
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
                    await transcribe(True)
                    utter = []
        bridge_task.cancel()


def main(token: str | None = None, port: int = 8765) -> None:
    token = token or os.environ.get("TRAIL_TOKEN") or "trail-dev"
    try:
        asyncio.run(run(f"ws://127.0.0.1:{port}/ws", token))
    except KeyboardInterrupt:
        pass
