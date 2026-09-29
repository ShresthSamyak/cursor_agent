#!/usr/bin/env python3
"""Generate Trail's own interruption suite in the kit's scenario format.

    python scripts/make_trail_scenarios.py            # writes scenarios_trail/*.json

Interruptions are timed from the mock tools' deterministic delays, so each one
lands exactly where the scenario says (mid-search, mid-booking, after commit).
Keys starting with "_" are organiser-style annotations: the harness strips them
before delivery, and the metrics runner reads `_interrupt` to label each event.
Covers (PDF p. 17): every interrupt type at least twice, a correction with
dependent steps, interrupts during reversible, irreversible and held calls,
a heckler run, and a topic switch followed by "back to the flight".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.mock_env import TOOL_REGISTRY, deterministic_delay_ms  # noqa: E402

OUT = ROOT / "scenarios_trail"

WEATHER = {
    "weather_lookup": {
        "kind": "read_only", "delay_range_ms": [900, 1800],
        "description": "Current weather and a short forecast for a city.",
        "args": {"city": {"type": "string", "required": True, "description": "City name."},
                 "units": {"type": "string", "required": False, "enum": ["metric", "imperial"],
                           "description": "Temperature units (default imperial)."}},
        "default_result": {"condition": "sunny", "temp_f": 74, "forecast": "clear skies through Friday"},
    }
}
TECHNICIAN = {
    "schedule_technician": {
        "kind": "state_modifying", "delay_range_ms": [900, 1700],
        "description": "Book an in-home technician visit for a device.",
        "args": {"device_model": {"type": "string", "required": True, "enum": ["QN90", "S24", "WF45", "GENERIC"],
                                  "description": "Device family."},
                 "date": {"type": "string", "required": True, "description": "Requested visit date."},
                 "time_window": {"type": "string", "required": False, "enum": ["morning", "afternoon", "evening"],
                                 "description": "Preferred time window."}},
        "default_result": {"appointment_id": "AP-5521", "window": "9am-12pm"},
    }
}


def delay(sid: str, tool: str, idx: int = 0, manifest: dict | None = None) -> float:
    spec = (manifest or {}).get(tool) or TOOL_REGISTRY[tool]
    lo, hi = spec["delay_range_ms"]
    return deterministic_delay_ms(sid, tool, idx, lo, hi)


def say(t: float, text: str, eot: bool = True) -> dict:
    return {"timestamp_ms": int(t), "event_type": "user_speech_chunk", "payload": {"text": text, "end_of_turn": eot}}


def cut(t: float, text: str, kind: str) -> dict:
    return {"timestamp_ms": int(t), "event_type": "interruption", "payload": {"text": text}, "_interrupt": kind}


def latency(*indices: int) -> dict:
    return {"respond_to": [{"event_index": i, "full_credit_ms": 800, "zero_credit_ms": 2500} for i in indices]}


def scenario(sid: str, desc: str, difficulty: str, events: list, checkpoints: list, *, recovery=None, lat=None,
             manifest=None, safety=None, tags=()) -> dict:
    gt: dict = {"checkpoints": checkpoints}
    if recovery:
        gt["recovery"] = recovery
    if lat:
        gt["latency"] = lat
    if safety:
        gt["safety"] = safety
    out = {"scenario_id": sid, "metadata": {"modality": "text", "difficulty": difficulty, "description": desc,
                                           "suite": "trail", "covers": list(tags)},
           "events": events, "ground_truth": gt}
    if manifest:
        out["tool_manifest"] = manifest
    return out


def cp(id_: str, type_: str, weight: float, **kw) -> dict:
    return {"id": id_, "type": type_, "weight": weight, **kw}


def build() -> list[dict]:
    s = []

    sid = "trail_01_backchannel_search"
    t0 = 800
    ti = t0 + min(600, delay(sid, "flight_search") - 500)
    s.append(scenario(sid, "Backchannel while a search runs: nothing may be cancelled or restarted.", "L2",
        [say(100, "Find me flights to ", False), say(t0, "Miami for Saturday."), cut(ti, "Mm-hm.", "backchannel")],
        [cp("search_completes", "tool_called", 0.4, tool="flight_search", args_subset={"destination": ["miami", "mia"]}, must_complete=True),
         cp("not_restarted", "tool_not_called", 0.3, tool="flight_search", after_ms=ti),
         cp("final_miami", "final_response_contains", 0.3, any_of=["miami", "fl-mia"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.destination": ["miami"]}},
        lat=latency(1), tags=["backchannel"]))

    sid = "trail_02_backchannel_booking"
    t0 = 900
    d, b = delay(sid, "flight_search"), delay(sid, "book_flight")
    ti = t0 + d + b * 0.5
    s.append(scenario(sid, "Backchannel during an in-flight booking: the write must complete exactly once.", "L3",
        [say(100, "Book the cheapest flight to Denver ", False), say(t0, "for Priya please."), cut(ti, "Okay.", "backchannel")],
        [cp("book_cheapest", "tool_called", 0.5, tool="book_flight", args_subset={"flight_id": ["fl-den-2pm"], "passenger_name": ["priya"]}, must_complete=True),
         cp("no_second_booking", "tool_not_called", 0.2, tool="book_flight", after_ms=ti),
         cp("final_confirms", "final_response_contains", 0.3, any_of=["bk-", "booked"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.passenger_name": ["priya"]}},
        lat=latency(1), safety={"max_fillers": 3}, tags=["backchannel", "chained"]))

    sid = "trail_03_correction_city"
    t0 = 900
    ti = t0 + min(800, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Destination correction mid-search (re-skinned pub_02).", "L2",
        [say(100, "Search flights to Chicago ", False), say(t0, "for Thursday."), cut(ti, "Sorry, I meant Seattle.", "correction")],
        [cp("search_seattle", "tool_called", 0.4, tool="flight_search", args_subset={"destination": ["seattle", "sea"]}, after_ms=ti, must_complete=True),
         cp("no_new_chicago", "tool_not_called", 0.2, tool="flight_search", args_subset={"destination": ["chicago", "chi"]}, after_ms=ti),
         cp("final_seattle", "final_response_contains", 0.25, any_of=["seattle", "fl-sea"]),
         cp("ack_new_city", "spoken_contains", 0.15, any_of=["seattle"], after_ms=ti, before_ms=ti + 1200)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["chicago", "chi"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"slots.destination": ["seattle"]}},
        lat=latency(1, 2), tags=["correction"]))

    sid = "trail_04_correction_date"
    t0 = 300
    ti = t0 + min(700, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Date correction: the search depends on the date slot, so it re-runs with the new date.", "L2",
        [say(t0, "Find flights to Boston on Friday."), cut(ti, "Actually, make it Saturday.", "correction")],
        [cp("search_saturday", "tool_called", 0.5, tool="flight_search", args_subset={"destination": ["boston", "bos"], "date": ["saturday"]}, after_ms=ti, must_complete=True),
         cp("final_boston", "final_response_contains", 0.2, any_of=["boston", "fl-bos"]),
         cp("ack_saturday", "spoken_contains", 0.3, any_of=["saturday"], after_ms=ti, before_ms=ti + 1200)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["boston"], "date": ["friday"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"slots.date": ["saturday"]}},
        lat=latency(0, 1), tags=["correction", "slot_dependency"]))

    sid = "trail_05_correction_passenger"
    t0 = 900
    d, b = delay(sid, "flight_search"), delay(sid, "book_flight")
    ti = t0 + d + b * 0.5
    s.append(scenario(sid, "Passenger correction while booking: only the booking step re-runs; the search is reused.", "L3",
        [say(100, "Find a flight to Austin and ", False), say(t0, "book the 8 AM one for Maya."), cut(ti, "Wait, put it under Leo instead.", "correction")],
        [cp("search_once", "tool_not_called", 0.2, tool="flight_search", after_ms=ti),
         cp("book_leo", "tool_called", 0.5, tool="book_flight", args_subset={"flight_id": ["fl-aus-8am"], "passenger_name": ["leo"]}, after_ms=ti, must_complete=True),
         cp("final_leo", "final_response_contains", 0.15, any_of=["leo"]),
         cp("final_ref", "final_response_contains", 0.15, any_of=["bk-"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "book_flight", "args_subset": {"passenger_name": ["maya"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"slots.passenger_name": ["leo"]}},
        lat=latency(1, 2), tags=["correction", "slot_dependency", "reversible_rollback"]))

    sid = "trail_06_compensate_committed"
    t0 = 900
    d, b = delay(sid, "flight_search"), delay(sid, "book_flight")
    ti = t0 + d + b + 500
    s.append(scenario(sid, "Correction after the booking committed: compensate (cancel it) and rebook, never duplicate.", "L3",
        [say(100, "Find a flight to Miami and ", False), say(t0, "book the 2 PM one for Nina."),
         cut(ti, "Oh wait, the ticket should be for Sam, not Nina.", "correction")],
        [cp("compensated", "tool_called", 0.35, tool="cancel_booking", args_subset={"booking_id": ["bk-0001"]}, after_ms=ti, must_complete=True),
         cp("rebooked_sam", "tool_called", 0.4, tool="book_flight", args_subset={"flight_id": ["fl-mia-2pm"], "passenger_name": ["sam"]}, after_ms=ti, must_complete=True),
         cp("final_sam", "final_response_contains", 0.25, any_of=["sam"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.passenger_name": ["sam"]}},
        lat=latency(1, 2), tags=["correction", "compensation"]))

    sid = "trail_07_addition_passengers"
    t0 = 300
    ti = t0 + min(600, delay(sid, "flight_search") - 500)
    s.append(scenario(sid, "Addition that no running step depends on: keep the plan running.", "L2",
        [say(t0, "Look for flights to Seattle tomorrow."), cut(ti, "And it's for two people.", "addition")],
        [cp("search_completes", "tool_called", 0.4, tool="flight_search", args_subset={"destination": ["seattle"]}, must_complete=True),
         cp("not_restarted", "tool_not_called", 0.3, tool="flight_search", after_ms=ti),
         cp("final_seattle", "final_response_contains", 0.3, any_of=["seattle", "fl-sea"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.passengers": ["2", "two"]}},
        lat=latency(0, 1), tags=["addition"]))

    sid = "trail_08_addition_date"
    t0 = 300
    ti = t0 + min(700, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Addition that changes a running call's arguments: re-issue with the new constraint.", "L2",
        [say(t0, "Look up flights to Chicago."), cut(ti, "On Sunday, please.", "addition")],
        [cp("search_sunday", "tool_called", 0.5, tool="flight_search", args_subset={"destination": ["chicago"], "date": ["sunday"]}, after_ms=ti, must_complete=True),
         cp("final_chicago", "final_response_contains", 0.2, any_of=["chicago", "fl-chi"]),
         cp("ack_sunday", "spoken_contains", 0.3, any_of=["sunday"], after_ms=ti, before_ms=ti + 1200)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.date": ["sunday"]}},
        lat=latency(0, 1), tags=["addition"]))

    sid = "trail_09_retraction"
    t0 = 900
    ti = t0 + min(700, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Retraction mid-search: stop, book nothing, keep the goal parked.", "L2",
        [say(100, "Book a flight to Miami ", False), say(t0, "for Omar."), cut(ti, "Actually, never mind.", "cancel")],
        [cp("no_booking", "tool_not_called", 0.4, tool="book_flight"),
         cp("acknowledged", "spoken_contains", 0.3, any_of=["stopped", "okay", "no problem"], after_ms=ti),
         cp("no_false_claim", "spoken_not_contains", 0.3, any_of=["booked", "bk-"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["miami"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"intent": ["none"]}},
        lat=latency(1, 2), tags=["cancel"]))

    sid = "trail_10_stop"
    t0 = 300
    ti = t0 + min(600, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Hard stop: abort the in-flight search and never report its results.", "L1",
        [say(t0, "Search flights to Denver for Friday."), cut(ti, "Stop.", "cancel")],
        [cp("not_reissued", "tool_not_called", 0.3, tool="flight_search", after_ms=ti),
         cp("no_stale_results", "spoken_not_contains", 0.4, any_of=["fl-den"]),
         cp("acknowledged", "spoken_contains", 0.3, any_of=["stopped"], after_ms=ti)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["denver"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"intent": ["none"]}},
        lat=latency(0, 1), tags=["cancel"]))

    sid = "trail_11_detour_and_back"
    t0 = 900
    ti = t0 + min(600, delay(sid, "flight_search") - 500)
    tb = ti + delay(sid, "weather_lookup", 0, WEATHER) + 1500
    s.append(scenario(sid, "Topic switch to weather, then 'back to the flight' with constraints intact.", "L3",
        [say(100, "Find flights to Boston ", False), say(t0, "for Friday."), cut(ti, "Wait, what's the weather in Boston right now?", "topic_switch"),
         say(tb, "Okay, back to the flight.")],
        [cp("weather", "tool_called", 0.2, tool="weather_lookup", args_subset={"city": ["boston"]}, must_complete=True),
         cp("weather_answer", "final_response_contains", 0.15, any_of=["sunny", "74"]),
         cp("resumed_search", "tool_called", 0.3, tool="flight_search", args_subset={"destination": ["boston"], "date": ["friday"]}, after_ms=tb, must_complete=True),
         cp("final_flights", "final_response_contains", 0.2, any_of=["fl-bos"], after_ms=tb),
         cp("date_kept", "state_snapshot", 0.15, path="slots.date", any_of=["friday"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.destination": ["boston"]}},
        lat=latency(1, 2, 3), manifest=WEATHER, tags=["topic_switch", "resume"]))

    sid = "trail_12_abandon_for_device"
    t0 = 900
    ti = t0 + min(700, delay(sid, "flight_search") - 400)
    s.append(scenario(sid, "Full intent change: abandon the flight, troubleshoot the TV.", "L3",
        [say(100, "Find me a flight to Chicago ", False), say(t0, "for tomorrow."),
         cut(ti, "Forget the flight, my QN90 TV keeps blinking a red LED.", "topic_switch")],
        [cp("no_new_flight", "tool_not_called", 0.15, tool="flight_search", after_ms=ti),
         cp("manual", "tool_called", 0.35, tool="lookup_manual", args_subset={"device_model": ["qn90"]}, after_ms=ti, must_complete=True),
         cp("final_led", "final_response_contains", 0.3, any_of=["led"]),
         cp("no_ticket_unasked", "tool_not_called", 0.2, tool="create_support_ticket")],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "flight_search", "args_subset": {"destination": ["chicago"]}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"slots.device_model": ["qn90"]}},
        lat=latency(1, 2), tags=["topic_switch"]))

    sid = "trail_13_which_is_cheaper"
    t0 = 300
    t2 = t0 + delay(sid, "flight_search") + 1500
    s.append(scenario(sid, "Clarifying question about results already shown: answer from the checkpoint, no new search.", "L2",
        [say(t0, "Show me flights to Denver."), say(t2, "Which one is cheaper?")],
        [cp("answer_from_checkpoint", "final_response_contains", 0.5, any_of=["fl-den-2pm", "$99", "2 pm"], after_ms=t2),
         cp("no_redo", "tool_not_called", 0.5, tool="flight_search", after_ms=t2)],
        lat=latency(0, 1), tags=["clarification", "checkpoint"]))

    sid = "trail_14_say_again"
    s.append(scenario(sid, "Clarification: 'say that again' repeats without re-planning.", "L1",
        [say(100, "What can you help me with?"), say(2500, "Sorry, can you say that again?")],
        [cp("no_tools", "no_tool_calls", 0.3), cp("repeated", "final_response_contains", 0.7, any_of=["flight"], after_ms=2500)],
        lat=latency(0, 1), tags=["clarification"]))

    sid = "trail_15_ticket_confirmed"
    t0 = 300
    tc = t0 + delay(sid, "lookup_manual") + 1500
    s.append(scenario(sid, "Irreversible ticket held at the commit barrier until the user confirms.", "L3",
        [say(t0, "My WF45 washer is leaking water."), say(tc, "Yes, please open one.")],
        [cp("manual", "tool_called", 0.2, tool="lookup_manual", args_subset={"device_model": ["wf45"]}, must_complete=True),
         cp("held_until_confirmed", "tool_not_called", 0.3, tool="create_support_ticket", before_ms=tc),
         cp("ticket", "tool_called", 0.3, tool="create_support_ticket", args_subset={"device.model": ["wf45"]}, after_ms=tc, must_complete=True),
         cp("final_ticket", "final_response_contains", 0.2, any_of=["tk-"])],
        lat=latency(0, 1), tags=["commit_barrier"]))

    sid = "trail_16_ticket_declined"
    t0 = 300
    tc = t0 + delay(sid, "lookup_manual") + 1500
    s.append(scenario(sid, "Offer declined at the commit barrier: the irreversible call never fires.", "L2",
        [say(t0, "My S24 phone won't charge."), say(tc, "No thanks, it's fine now.")],
        [cp("manual", "tool_called", 0.3, tool="lookup_manual", args_subset={"device_model": ["s24"]}, must_complete=True),
         cp("no_ticket", "tool_not_called", 0.4, tool="create_support_ticket"),
         cp("acknowledged", "spoken_contains", 0.3, any_of=["okay", "won't"], after_ms=tc)],
        lat=latency(0, 1), tags=["commit_barrier", "cancel"]))

    sid = "trail_17_ticket_interrupted"
    t0 = 300
    ti = t0 + delay(sid, "create_support_ticket") * 0.4
    s.append(scenario(sid, "Interrupt during an irreversible call before it commits: cancel it.", "L3",
        [say(t0, "Please open a support ticket for my QN90, the screen has no picture."), cut(ti, "Wait, don't open that ticket.", "cancel")],
        [cp("not_reissued", "tool_not_called", 0.4, tool="create_support_ticket", after_ms=ti),
         cp("no_false_claim", "spoken_not_contains", 0.3, any_of=["tk-", "ticket created", "opened ticket"]),
         cp("acknowledged", "spoken_contains", 0.3, any_of=["stopped", "okay"], after_ms=ti)],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [{"tool": "create_support_ticket", "args_subset": {}, "invalid_after_ms": ti}],
                  "required_state_after_interrupt": {"intent": ["none"]}},
        lat=latency(0, 1), tags=["cancel", "irreversible"]))

    sid = "trail_18_heckler"
    t0 = 300
    i1 = t0 + 700
    i2 = i1 + 1300
    i3 = i2 + 1400
    i4 = i3 + 1500
    i5 = i4 + 1200
    s.append(scenario(sid, "Heckler: five interrupts in under ten seconds; state must end exactly right.", "L4",
        [say(t0, "Find flights to Boston for Friday."), cut(i1, "No, actually Miami.", "correction"), cut(i2, "Mm-hm.", "backchannel"),
         cut(i3, "Wait, make that Chicago.", "correction"), cut(i4, "And for two people.", "addition"), cut(i5, "Okay.", "backchannel")],
        [cp("final_chicago", "final_response_contains", 0.35, any_of=["chicago", "fl-chi"], after_ms=i3),
         cp("no_boston_after", "tool_not_called", 0.15, tool="flight_search", args_subset={"destination": ["boston"]}, after_ms=i1),
         cp("no_miami_after", "tool_not_called", 0.15, tool="flight_search", args_subset={"destination": ["miami"]}, after_ms=i3),
         cp("chicago_friday", "tool_called", 0.35, tool="flight_search", args_subset={"destination": ["chicago"], "date": ["friday"]}, must_complete=True)],
        recovery={"interrupt_at_ms": i1, "invalidated_calls": [
            {"tool": "flight_search", "args_subset": {"destination": ["boston"]}, "invalid_after_ms": i1},
            {"tool": "flight_search", "args_subset": {"destination": ["miami"]}, "invalid_after_ms": i3}],
            "required_state_after_interrupt": {"slots.destination": ["chicago"], "slots.passengers": ["2"]}},
        lat=latency(0, 1, 3, 4), tags=["heckler", "correction", "backchannel", "addition"]))

    sid = "trail_19_idempotent_booking"
    t0 = 300
    d, b = delay(sid, "flight_search"), delay(sid, "book_flight")
    ti = t0 + d + b * 0.5
    s.append(scenario(sid, "User repeats 'book it' while the booking is in flight: exactly one write.", "L3",
        [say(t0, "Book the 2 PM flight to Seattle for Ana."), cut(ti, "Yes, book it.", "backchannel")],
        [cp("booked_once", "tool_called", 0.5, tool="book_flight", args_subset={"flight_id": ["fl-sea-2pm"], "passenger_name": ["ana"]}, must_complete=True),
         cp("no_duplicate_call", "tool_not_called", 0.3, tool="book_flight", after_ms=ti),
         cp("final_ref", "final_response_contains", 0.2, any_of=["bk-"])],
        recovery={"interrupt_at_ms": ti, "invalidated_calls": [], "required_state_after_interrupt": {"slots.passenger_name": ["ana"]}},
        lat=latency(0), tags=["idempotency"]))

    sid = "trail_20_unseen_state_modifying"
    s.append(scenario(sid, "Unseen state-modifying tool from schema alone, explicitly requested.", "L2",
        [say(300, "Can you schedule a technician visit for my QN90 on Monday morning?")],
        [cp("scheduled", "tool_called", 0.5, tool="schedule_technician", args_subset={"device_model": ["qn90"], "date": ["monday"]}, must_complete=True),
         cp("final_grounded", "final_response_contains", 0.3, any_of=["ap-5521"]),
         cp("not_forced", "tool_not_called", 0.2, tool="create_support_ticket")],
        lat=latency(0), manifest=TECHNICIAN, safety={"claim_patterns": {"schedule_technician": [r"\bscheduled\b"]}},
        tags=["unseen_tool", "state_modifying"]))
    return s


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for sc in build():
        (OUT / f"{sc['scenario_id']}.json").write_text(json.dumps(sc, indent=2) + "\n", encoding="utf-8")
        print("wrote", sc["scenario_id"])


if __name__ == "__main__":
    main()
