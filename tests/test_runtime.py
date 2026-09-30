"""Runtime invariants: single writer, stale work never reaches the user, session-only memory."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from harness.mock_env import TOOL_REGISTRY
from trail.core.bus import Event, EventType, Target
from trail.core.config import RuntimeConfig
from trail.core.runtime import Proposal, Runtime


class Live:
    def __init__(self, **cfg):
        self.rt = Runtime(RuntimeConfig(**cfg))
        self.rt._debug = True
        self.inq: asyncio.Queue = asyncio.Queue()
        self.outq: asyncio.Queue = asyncio.Queue()
        self.seen: list = []
        self.task = asyncio.create_task(self.rt.run(self.inq, self.outq))

    async def send(self, type_, **kw):
        await self.inq.put(Event(type=type_, **kw))

    async def until(self, pred, timeout=2.0):
        async with asyncio.timeout(timeout):
            while True:
                o = await self.outq.get()
                self.seen.append(o)
                if pred(o):
                    return o

    async def close(self):
        if not self.task.done():
            await self.inq.put(Event(type=EventType.SESSION_END))
        await asyncio.wait_for(self.task, 2)


def speak(kind):
    return lambda o: o.type == "speak" and o.kind == kind


def test_correction_cancels_only_the_dependent_call_and_state_is_versioned():
    async def check():
        live = Live()
        await live.send(EventType.MANIFEST, tools=TOOL_REGISTRY)
        await live.send(EventType.SPEECH_FINAL, text="Find flights to Boston for Friday.", ts=100)
        first = await live.until(lambda o: o.type == "tool_call")
        v1 = live.rt.version
        await live.send(EventType.SPEECH_FINAL, text="Actually make it New York.", barge_in=True, ts=900)
        cancel = await live.until(lambda o: o.type == "tool_cancel")
        second = await live.until(lambda o: o.type == "tool_call")
        assert cancel.call_id == first.call_id
        assert second.args == {"destination": "New York", "date": "Friday"}
        assert live.rt.version > v1
        ack = await live.until(lambda o: o.type == "speak" and o.kind == "ack" and "New York" in o.text)
        assert ack.snapshot["slots"]["destination"] == "New York"
        # The stale result arrives anyway: it must be ignored, never spoken.
        await live.send(EventType.TOOL_RESULT, call_id=first.call_id, status="success",
                        result={"status": "success", "flights": [{"flight_id": "FL-BOS-8AM", "depart": "08:00", "price_usd": 1}]})
        await live.send(EventType.TOOL_RESULT, call_id=second.call_id, status="success",
                        result={"status": "success", "flights": [{"flight_id": "FL-NYC-8AM", "depart": "08:00", "price_usd": 129}]})
        final = await live.until(speak("final"))
        assert "FL-NYC-8AM" in final.text and "BOS" not in final.text
        assert live.rt.metrics.stale_results_ignored == 1
        await live.close()
    asyncio.run(check())


def test_backchannel_cancels_nothing():
    async def check():
        live = Live()
        await live.send(EventType.MANIFEST, tools=TOOL_REGISTRY)
        await live.send(EventType.SPEECH_FINAL, text="Find flights to Miami.", ts=100)
        await live.until(lambda o: o.type == "tool_call")
        for word in ["mm-hm", "okay", "right"]:
            await live.send(EventType.SPEECH_FINAL, text=word, barge_in=True, ts=500)
        await live.send(EventType.APP_SWITCH, app="x")
        await asyncio.sleep(0.05)
        assert not any(o.type == "tool_cancel" for o in live.seen + [live.outq.get_nowait() for _ in range(live.outq.qsize())])
        assert live.rt.metrics.backchannel_false_stops == 0 and live.rt.metrics.calls_cancelled == 0
        await live.close()
    asyncio.run(check())


def test_topic_switch_parks_and_back_resumes_from_checkpoint_without_redoing_retrieval():
    async def check():
        live = Live()
        weather = {"kind": "read_only", "delay_range_ms": [900, 1800], "description": "Current weather for a city.",
                   "args": {"city": {"type": "string", "required": True}}, "default_result": {"condition": "sunny", "temp_f": 70}}
        await live.send(EventType.MANIFEST, tools={**TOOL_REGISTRY, "weather_lookup": weather})
        await live.send(EventType.SPEECH_FINAL, text="Find flights to Denver for Friday.", ts=100)
        search = await live.until(lambda o: o.type == "tool_call")
        await live.send(EventType.TOOL_RESULT, call_id=search.call_id, status="success",
                        result={"status": "success", "flights": [{"flight_id": "FL-DEN-8AM", "depart": "08:00", "price_usd": 99}]})
        await live.until(speak("final"))
        await live.send(EventType.SPEECH_FINAL, text="What's the weather in Denver?", ts=3000)
        w = await live.until(lambda o: o.type == "tool_call")
        assert w.api_name == "weather_lookup"
        await live.send(EventType.TOOL_RESULT, call_id=w.call_id, status="success", result={"status": "success", "condition": "sunny", "temp_f": 70})
        await live.until(speak("final"))
        await live.send(EventType.SPEECH_FINAL, text="Okay, back to the flight.", ts=6000)
        final = await live.until(speak("final"))
        assert "FL-DEN-8AM" in final.text
        assert sum(1 for o in live.seen if o.type == "tool_call" and o.api_name == "flight_search") == 1
        await live.close()
    asyncio.run(check())


def test_stale_proposals_are_dropped_by_epoch():
    async def check():
        live = Live()
        await live.send(EventType.MANIFEST, tools=TOOL_REGISTRY)
        await live.send(EventType.SPEECH_FINAL, text="hello there", ts=10)
        await live.until(speak("final"))
        stale = Proposal("emit", (("turn", -5),), ("final", "STALE", {}))
        live.rt._proposals.put_nowait(stale)
        await live.send(EventType.APP_SWITCH, app="x")
        await asyncio.sleep(0.05)
        assert live.rt.metrics.stale_proposals_dropped >= 1
        assert not any(o.text == "STALE" for o in live.seen)
        await live.close()
    asyncio.run(check())


def test_state_is_immutable_duplicates_dropped_and_memory_cleared_on_end():
    async def check():
        live = Live(mode="desktop")
        await live.send(EventType.DWELL, app="chrome", target=Target(text="Sat · ₹5,000", context="Chandigarh → Goa"), event_id="e1")
        await live.send(EventType.DWELL, app="chrome", target=Target(text="Sat · ₹5,000", context="Chandigarh → Goa"), event_id="e1")
        await live.send(EventType.DWELL, app="chrome", target=Target(text="hunter2", role="password"))
        await live.until(lambda o: o.code == "context_rejected")
        with pytest.raises(FrozenInstanceError):
            live.rt.state.version = 9
        assert live.rt.metrics.duplicate_events_dropped == 1 and len(live.rt.trail.entries) == 1
        await live.close()
        assert live.rt.state.phase == "closed" and not live.rt.trail.entries and not live.rt.goals.goals
    asyncio.run(check())


def test_malformed_input_never_kills_the_session():
    async def check():
        live = Live()
        live.rt._debug = False
        await live.inq.put({"not": "an event"})
        await live.send(EventType.TOOL_RESULT, call_id="nope", status="success", result={})
        await live.send(EventType.SPEECH_FINAL, text="What can you help me with?", ts=5)
        assert "flights" in (await live.until(speak("final"))).text
        await live.close()
    asyncio.run(check())


def test_bounded_output_queue_is_rejected():
    async def check():
        with pytest.raises(ValueError, match="unbounded"):
            await Runtime().run(asyncio.Queue(), asyncio.Queue(maxsize=1))
    asyncio.run(check())


def test_irreversible_action_waits_for_confirmation_after_disruption():
    async def check():
        live = Live()
        await live.send(EventType.MANIFEST, tools=TOOL_REGISTRY)
        await live.send(EventType.SPEECH_FINAL, text="My QN90 LED keeps blinking red.", ts=10)
        lookup = await live.until(lambda o: o.type == "tool_call")
        await live.send(EventType.TOOL_RESULT, call_id=lookup.call_id, status="success",
                        result={"status": "success", "pages": [{"doc": "QN90-manual", "page": 57, "title": "LED Status Indicators"}]})
        offer = await live.until(speak("final"))
        assert "ticket" in offer.text and not any(o.api_name == "create_support_ticket" for o in live.seen if o.type == "tool_call")
        await live.send(EventType.SPEECH_FINAL, text="Yes please.", ts=4000)
        ticket = await live.until(lambda o: o.type == "tool_call")
        assert ticket.api_name == "create_support_ticket" and ticket.args["device"]["model"] == "QN90"
        await live.close()
    asyncio.run(check())
