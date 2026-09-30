"""Live-microphone robustness: room noise, Whisper hallucinations, overheard speech (no audio device needed)."""

import asyncio

import numpy as np
import pytest

from trail.core.bus import EventType
from trail.desktop.speech import FOLLOW_UP_S, FRAME, MIN_VOICED_FRAMES, EnergyVAD, addressed, keep_transcript

from .test_runtime import Live


def _rms(x):
    return float(np.sqrt(np.mean(x * x)))


def _utterances(vad, audio):
    out = []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        if vad.step(_rms(audio[i:i + FRAME])) == "end":
            out.append(vad.voiced)
    if vad.speaking:
        out.append(vad.voiced)
    return [u for u in out if u >= MIN_VOICED_FRAMES]


def test_background_talk_below_calibrated_level_is_not_an_utterance():
    rng = np.random.default_rng(1)
    room = (rng.normal(0, 0.0015, 16000 * 5) * (1 + np.sin(np.arange(16000 * 5) / 900))).astype("float32")
    vad = EnergyVAD()
    vad.calibrate([_rms(room[i:i + FRAME]) for i in range(0, 16000, FRAME)])
    assert _utterances(vad, room) == []


def test_voice_over_room_noise_is_one_utterance_and_own_voice_needs_louder_barge_in():
    rng = np.random.default_rng(2)
    room = rng.normal(0, 0.0015, 16000 * 4).astype("float32")
    t = np.arange(16000 * 2) / 16000
    voice = (0.05 * np.sin(2 * np.pi * 180 * t) * (1 + np.sin(2 * np.pi * 4 * t))).astype("float32")
    audio = room.copy()
    audio[16000:48000] += voice
    vad = EnergyVAD()
    vad.calibrate([_rms(room[i:i + FRAME]) for i in range(0, 16000, FRAME)])
    assert len(_utterances(vad, audio)) == 1
    quiet_echo = np.full(FRAME, vad.threshold() * 1.5, dtype="float32")
    vad.boost = 2.5
    assert all(vad.step(_rms(quiet_echo)) is None for _ in range(10))


class _Seg:
    def __init__(self, lp, ns):
        self.avg_logprob, self.no_speech_prob = lp, ns


@pytest.mark.parametrize("text,seg,keep", [
    ("Thank you very much.", _Seg(-0.3, 0.1), False),
    ("Thanks for watching!", _Seg(-0.3, 0.1), False),
    ("Thank you very much for watching.", _Seg(-0.3, 0.1), False),
    ("I don't know.", _Seg(-0.2, 0.1), False),
    ("Book a flight.", _Seg(-1.4, 0.2), False),
    ("Book a flight to Mumbai on Friday.", _Seg(-0.3, 0.05), True),
    ("Thank you, book the Monday one.", _Seg(-0.3, 0.05), True),
])
def test_whisper_hallucinations_and_unsure_segments_are_dropped(text, seg, keep):
    assert keep_transcript(text, [seg]) is keep


@pytest.mark.parametrize("text,since,want", [
    ("Trail, book the Monday one.", 99.0, "book the Monday one."),
    ("Hey Trail what's cheaper?", 99.0, "what's cheaper?"),
    ("Trial, cancel that.", 99.0, "cancel that."),
    ("God is near.", 99.0, None),
    ("Actually, two passengers.", 2.0, "Actually, two passengers."),
    ("Actually, two passengers.", FOLLOW_UP_S + 1, None),
    ("Trail.", 99.0, None),
    ("The trailhead is closed.", 99.0, None),
])
def test_wake_word_gate(text, since, want):
    assert addressed(text, since) == want


def _spoke(o):
    return o.type in {"speak", "speak_start", "speak_end"} and o.kind in {"final", None}


def test_overheard_mic_speech_is_ignored_and_fallback_is_not_repeated():
    async def check():
        live = Live(mode="desktop")
        await live.send(EventType.SPEECH_FINAL, text="I think that's it in the whole video.", source="speech")
        with pytest.raises(TimeoutError):
            await live.until(_spoke, timeout=0.6)
        await live.send(EventType.SPEECH_FINAL, text="Blorp the wibble.", source="cli")
        await live.until(_spoke, timeout=2.0)
        await live.send(EventType.SPEECH_FINAL, text="Zorp the wobble.", source="cli")
        with pytest.raises(TimeoutError):
            await live.until(_spoke, timeout=0.6)
        await live.close()
    asyncio.run(check())
