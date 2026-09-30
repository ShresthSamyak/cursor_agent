#!/usr/bin/env python3
"""Hidden-set-style stress scenarios (docs/kit/WALKTHROUGH.md section 4) -> scenarios_stress/.

Paraphrases, re-skins, two interrupts in one scenario, interrupts just before a tool returns,
retractions during chained calls, unseen tools with numbers/enums/nested objects/arrays,
injected failures, chit-chat. Ground truth follows the kit's conventions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from make_trail_scenarios import cp, cut, delay, latency, say, scenario  # noqa: E402

OUT = ROOT / "scenarios_stress"

HOTEL = {"hotel_search": {"kind": "read_only", "delay_range_ms": [1200, 2400], "description": "Search hotels in a city.",
                          "args": {"city": {"type": "string", "required": True, "description": "City name."},
                                   "nights": {"type": "number", "required": False, "description": "Length of stay."}},
                          "default_result": {"hotels": [{"hotel_id": "HT-0001", "name": "Harbor Inn", "price_usd": 189}]}}}
CAR = {"rental_car_quote": {"kind": "read_only", "delay_range_ms": [800, 1600], "description": "Quote a rental car at a city's airport.",
                            "args": {"pickup_city": {"type": "string", "required": True, "description": "Pickup city name."},
                                     "car_class": {"type": "string", "required": False, "enum": ["economy", "suv", "luxury"]}},
                            "default_result": {"quote_id": "RC-7781", "daily_usd": 58, "car_class": "economy"}}}
TABLE = {"reserve_table": {"kind": "state_modifying", "delay_range_ms": [900, 1600], "description": "Reserve a restaurant table.",
                           "args": {"restaurant": {"type": "string", "required": True, "description": "Restaurant name."},
                                    "party": {"type": "object", "required": True, "properties": {
                                        "size": {"type": "number", "required": True}, "name": {"type": "string", "required": True}}},
                                    "time": {"type": "string", "required": False}},
                           "default_result": {"reservation_id": "RS-2044"}}}
STATUS = {"flight_status": {"kind": "read_only", "delay_range_ms": [700, 1400], "description": "Live status (on time or delayed) of a flight by flight number.",
                            "args": {"flight_number": {"type": "string", "required": True, "description": "Flight number such as UA 212."}},
                            "default_result": {"status_detail": "delayed", "delay_min": 35, "gate": "B12"}}}
CURRENCY = {"currency_convert": {"kind": "read_only", "delay_range_ms": [500, 1100], "description": "Convert an amount between currencies.",
                                 "args": {"amount": {"type": "number", "required": True}, "from_currency": {"type": "string", "required": True, "enum": ["USD", "EUR", "INR", "GBP"]},
                                          "to_currency": {"type": "string", "required": True, "enum": ["USD", "EUR", "INR", "GBP"]}},
                                 "default_result": {"converted": 92.4, "rate": 0.924}}}


def build() -> list[dict]:
    s = []

    def search(sid, text, city, aliases, *, date=None, desc):
        checks = [cp("search", "tool_called", 0.5, tool="flight_search", args_subset={"destination": aliases, **({"date": [date]} if date else {})}, must_complete=True),
                  cp("final", "final_response_contains", 0.3, any_of=aliases + ["fl-" + aliases[0][:3]]),
                  cp("state", "state_snapshot", 0.2, path="slots.destination", any_of=aliases)]
        s.append(scenario(sid, desc, "L1", [say(300, text)], checks, lat=latency(0), tags=["paraphrase"]))

    search("st_01_get_to", "I need to get to Denver on Friday.", "Denver", ["denver", "den"], date="friday", desc="paraphrase: get to")
    search("st_02_any_seats", "Any seats to Miami tomorrow?", "Miami", ["miami", "mia"], date="tomorrow", desc="paraphrase: any seats")
    search("st_03_whats_flying", "What's flying out to Austin next Friday?", "Austin", ["austin", "aus"], date="next friday", desc="paraphrase: what's flying")
    search("st_04_head_to", "I'm trying to head to Seattle, what are my options?", "Seattle", ["seattle", "sea"], desc="paraphrase: head to")

    sid = "st_05_double_correction"
    t0 = 800
    i1 = t0 + 600
    i2 = i1 + 700
    s.append(scenario(sid, "Two corrections in one scenario", "L3",
        [say(100, "Find flights to ", False), say(t0, "Boston please."), cut(i1, "Actually, Miami.", "correction"), cut(i2, "No wait, make it Seattle.", "correction")],
        [cp("seattle", "tool_called", 0.5, tool="flight_search", args_subset={"destination": ["seattle"]}, after_ms=i2, must_complete=True),
         cp("final", "final_response_contains", 0.3, any_of=["seattle"], after_ms=i2),
         cp("no_miami_after", "tool_not_called", 0.2, tool="flight_search", args_subset={"destination": ["miami"]}, after_ms=i2)],
        recovery={"interrupt_at_ms": i1, "invalidated_calls": [
            {"tool": "flight_search", "args_subset": {"destination": ["boston"]}, "invalid_after_ms": i1},
            {"tool": "flight_search", "args_subset": {"destination": ["miami"]}, "invalid_after_ms": i2}],
            "required_state_after_interrupt": {"slots.destination": ["seattle"]}},
        lat=latency(1, 2, 3), tags=["correction", "double"]))

    sid = "st_06_interrupt_just_before_return"
    t0 = 300
    ti = t0 + delay(sid, "flight_search") - 120
    s.append(scenario(sid, "Correction lands ~120 ms before the stale search returns", "L3",
        [say(t0, "Search flights to Chicago."), cut(ti, "Sorry, I meant Denver.", "correction")],
        [cp("denver", "tool_called", 0.5, tool="flight_search", args_subset={"destination": ["denver"]}, after_ms=ti, must_complete=True),
         cp("final", "final_response_contains", 0.3, any_of=["denver"]),
         cp("no_chicago_final", "spoken_not_contains", 0.2, any_of=["fl-chi"], after_ms=ti)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["chicago"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"slots.destination": ["denver"]}},
        lat=latency(0, 1), tags=["correction", "race"]))

    sid = "st_07_retract_during_booking"
    t0 = 300
    ti = t0 + delay(sid, "flight_search") + delay(sid, "book_flight") * 0.4
    s.append(scenario(sid, "Retraction while the booking is in flight", "L3",
        [say(t0, "Book the 8 AM flight to Chicago for Dev."), cut(ti, "Actually, never mind, don't book it.", "cancel")],
        [cp("search", "tool_called", 0.3, tool="flight_search", args_subset={"destination": ["chicago"]}, must_complete=True),
         cp("no_rebook", "tool_not_called", 0.3, tool="book_flight", after_ms=ti),
         cp("no_claim", "spoken_not_contains", 0.4, any_of=["bk-", "is booked"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "book_flight", "args_subset": {"passenger_name": ["dev"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"intent": ["none"]}},
        lat=latency(0, 1), tags=["cancel", "chained"]))

    sid = "st_08_hotel_nights"
    s.append(scenario(sid, "Unseen tool with a number arg", "L2", [say(100, "Find me a hotel ", False), say(900, "in Chicago for three nights.")],
        [cp("hotel", "tool_called", 0.5, tool="hotel_search", args_subset={"city": ["chicago"], "nights": [3]}, must_complete=True),
         cp("not_flight", "tool_not_called", 0.15, tool="flight_search"),
         cp("final", "final_response_contains", 0.35, any_of=["harbor inn", "ht-0001", "189"])],
        lat=latency(1), manifest=HOTEL, tags=["unseen_tool"]))

    sid = "st_09_car_enum"
    s.append(scenario(sid, "Unseen tool with an enum arg from a synonym", "L2", [say(300, "How much would an SUV rental be in Denver?")],
        [cp("car", "tool_called", 0.5, tool="rental_car_quote", args_subset={"pickup_city": ["denver"], "car_class": ["suv"]}, must_complete=True),
         cp("final", "final_response_contains", 0.5, any_of=["rc-7781", "58"])],
        lat=latency(0), manifest=CAR, tags=["unseen_tool", "enum"]))

    sid = "st_10_table_nested"
    s.append(scenario(sid, "Unseen state-modifying tool with a nested object", "L3",
        [say(300, "Reserve a table at Luigi's for four people under the name Priya.")],
        [cp("reserve", "tool_called", 0.6, tool="reserve_table", args_subset={"restaurant": ["luigi's", "luigis"], "party.size": [4], "party.name": ["priya"]}, must_complete=True),
         cp("final", "final_response_contains", 0.4, any_of=["rs-2044"])],
        lat=latency(0), manifest=TABLE, tags=["unseen_tool", "nested"]))

    sid = "st_11_flight_status"
    s.append(scenario(sid, "Unseen read-only tool overlapping the flight domain", "L2", [say(300, "Is flight UA 212 on time?")],
        [cp("status", "tool_called", 0.5, tool="flight_status", args_subset={"flight_number": ["ua 212", "ua212"]}, must_complete=True),
         cp("not_search", "tool_not_called", 0.2, tool="flight_search"),
         cp("final", "final_response_contains", 0.3, any_of=["delayed", "35", "b12"])],
        lat=latency(0), manifest=STATUS, tags=["unseen_tool"]))

    sid = "st_12_currency"
    s.append(scenario(sid, "Unseen tool with number + two enums", "L2", [say(300, "Convert 100 dollars to euros.")],
        [cp("convert", "tool_called", 0.6, tool="currency_convert", args_subset={"amount": [100], "from_currency": ["usd"], "to_currency": ["eur"]}, must_complete=True),
         cp("final", "final_response_contains", 0.4, any_of=["92.4", "92"])],
        lat=latency(0), manifest=CURRENCY, tags=["unseen_tool", "enum"]))

    sid = "st_13_manual_timeout"
    s.append(scenario(sid, "Read-only manual lookup times out once", "L3", [say(300, "My QN90 LED keeps blinking red, what does that mean?")],
        [cp("retried", "tool_called", 0.4, tool="lookup_manual", min_count=2),
         cp("ok", "tool_called", 0.3, tool="lookup_manual", must_complete=True),
         cp("final", "final_response_contains", 0.3, any_of=["led", "page"])],
        lat=latency(0), tags=["failure"]))
    s[-1]["tool_overrides"] = {"lookup_manual": [{"call_index": 0, "delay_ms": 1800, "error": "timeout", "detail": "manual index timed out"}]}

    sid = "st_14_cancel_not_found"
    s.append(scenario(sid, "Cancel a booking that does not exist", "L2", [say(300, "Please cancel my booking BK-0042.")],
        [cp("cancel", "tool_called", 0.5, tool="cancel_booking", args_subset={"booking_id": ["bk-0042"]}),
         cp("final", "final_response_contains", 0.5, any_of=["couldn't", "could not", "sorry", "not find", "no booking"])],
        lat=latency(0), tags=["failure"]))

    sid = "st_15_ticket_high"
    s.append(scenario(sid, "Explicit support ticket with severity", "L2", [say(300, "Open a high priority support ticket for my S24, the screen is cracked.")],
        [cp("ticket", "tool_called", 0.6, tool="create_support_ticket", args_subset={"device.model": ["s24"], "issue.severity": ["high"]}, must_complete=True),
         cp("final", "final_response_contains", 0.4, any_of=["tk-"])],
        lat=latency(0), tags=["ticket"]))

    sid = "st_16_thanks"
    s.append(scenario(sid, "Chit-chat: no tools", "L1", [say(300, "Thanks, that's all for now.")],
        [cp("no_tools", "no_tool_calls", 0.6), cp("final", "final_response_contains", 0.4, any_of=["welcome", "anytime", "glad"])],
        lat=latency(0), tags=["no_tool"]))

    sid = "st_17_book_three_passengers"
    s.append(scenario(sid, "Booking with a named passenger and a time preference in a paraphrase", "L2",
        [say(300, "Get me on the 2 PM flight to Boston, it's for Marco.")],
        [cp("book", "tool_called", 0.6, tool="book_flight", args_subset={"flight_id": ["fl-bos-2pm"], "passenger_name": ["marco"]}, must_complete=True),
         cp("final", "final_response_contains", 0.4, any_of=["bk-"])],
        lat=latency(0), tags=["paraphrase", "chained"]))

    sid = "st_18_intent_change_to_weather"
    ti = 300 + 700
    s.append(scenario(sid, "Intent change mid-search with 'instead'", "L3",
        [say(300, "Find flights to Denver."), cut(ti, "Actually, just tell me the weather in Denver instead.", "topic_switch")],
        [cp("weather", "tool_called", 0.5, tool="weather_lookup", args_subset={"city": ["denver"]}, must_complete=True),
         cp("final", "final_response_contains", 0.5, any_of=["sunny", "74"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["denver"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"intent": ["weather_lookup"]}},
        lat=latency(0, 1), manifest={"weather_lookup": {"kind": "read_only", "delay_range_ms": [900, 1800], "description": "Current weather and a short forecast for a city.",
                                                        "args": {"city": {"type": "string", "required": True}}, "default_result": {"condition": "sunny", "temp_f": 74}}},
        tags=["topic_switch"]))
    return s


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for sc in build():
        sc["metadata"]["suite"] = "stress"
        (OUT / f"{sc['scenario_id']}.json").write_text(json.dumps(sc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(build())} scenarios to {OUT}")


if __name__ == "__main__":
    main()
