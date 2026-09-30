"""Spoken requests against the desktop tools: routing, list answers, follow-ups, overheard speech."""

import asyncio

from trail.core.bus import EventType
from trail.core.goals import Goal
from trail.core.nlg import describe_result
from trail.core.tools import ArgSpec, arg_class
from trail.desktop.tools import MANIFEST, DesktopTools

from .test_runtime import Live


def test_list_items_are_named_and_priced_by_their_meaning_not_their_first_number():
    goal = Goal(id="g1", intent="fare_search", domain="tool", text="")
    goal.slots["passengers"] = 2
    result = {"status": "success", "currency": "INR", "fares": [
        {"flight_id": "SK-1", "day": "Friday", "stops": 0, "price": 6400},
        {"flight_id": "SK-2", "day": "Saturday", "stops": 0, "price": 5000},
    ]}
    text = describe_result(None, goal, result)
    assert "Friday at ₹6,400" in text and "Saturday at ₹5,000" in text
    assert "cheapest is Saturday" in text and "₹10,000" in text
    assert " at 0" not in text and "SK-1" not in text and "currency" not in text


def test_route_and_numeric_passenger_args_are_classed_correctly():
    assert arg_class(ArgSpec(name="route", type="string")) == "route"
    assert arg_class(ArgSpec(name="passengers", type="number")) == "count"
    assert arg_class(ArgSpec(name="passenger_name", type="string")) == "person"


def _outputs(live):
    out = []
    while not live.outq.empty():
        out.append(live.outq.get_nowait())
    return out


def test_spoken_fare_flow_routes_carries_context_and_ignores_overheard_questions():
    async def check():
        live = Live(mode="desktop", stream_chunk_delay_s=0.0)
        live.rt.tool_executor = DesktopTools(speed=50)
        await live.send(EventType.MANIFEST, tools=MANIFEST)

        async def say(text):
            await live.send(EventType.SPEECH_FINAL, text=text, source="speech")
            await asyncio.sleep(0.4)
            return _outputs(live)

        out = await say("What's the cheapest flight from Chandigarh to Goa?")
        calls = [o for o in out if o.type == "tool_call"]
        assert calls and calls[0].api_name == "fare_search" and calls[0].args["route"] == "Chandigarh → Goa"
        assert any(o.text and "₹4,500" in o.text for o in out)

        out = await say("Hold the Saturday fare.")
        calls = [o for o in out if o.type == "tool_call"]
        assert calls and calls[0].api_name == "hold_fare" and "Goa" in calls[0].args["route"]

        out = await say("Did you watch the match last night?")
        assert not [o for o in out if o.type == "tool_call"]
        assert not [o for o in out if o.type in {"speak", "speak_end"} and o.kind == "final"]
        await live.close()
    asyncio.run(check())


def test_a_sentence_is_not_accepted_as_a_structured_answer():
    async def check():
        live = Live(mode="desktop", stream_chunk_delay_s=0.0)
        live.rt.tool_executor = DesktopTools(speed=50)
        await live.send(EventType.MANIFEST, tools=MANIFEST)
        await live.send(EventType.SPEECH_FINAL, text="Hold the Saturday fare.", source="cli")
        await live.until(lambda o: o.type in {"speak", "speak_end"} and o.kind == "clarify" or
                         (o.text or "").endswith("should I use?"))
        await live.send(EventType.SPEECH_FINAL, text="Did you watch the match last night?", source="speech")
        await asyncio.sleep(0.4)
        assert not [o for o in _outputs(live) if o.type == "tool_call"]
        await live.close()
    asyncio.run(check())
