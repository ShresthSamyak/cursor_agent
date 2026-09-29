import pytest

from harness.mock_env import TOOL_REGISTRY, MockEnvironment
from trail.core import nlu
from trail.core.saga import CANCELLED, COMMITTED, IN_FLIGHT, UNKNOWN, Saga
from trail.core.tools import COMPENSABLE, IRREVERSIBLE, REVERSIBLE, ArgContext, Manifest, build_args, validate

WEATHER = {"kind": "read_only", "delay_range_ms": [900, 1800], "description": "Current weather and a short forecast for a city.",
           "args": {"city": {"type": "string", "required": True, "description": "City name."},
                    "units": {"type": "string", "required": False, "enum": ["metric", "imperial"]}},
           "default_result": {"condition": "sunny", "temp_f": 74}}
HOTEL = {"kind": "read_only", "delay_range_ms": [1200, 2400], "description": "Search hotels in a city.",
         "args": {"city": {"type": "string", "required": True}, "nights": {"type": "number", "required": False}},
         "default_result": {"hotels": [{"hotel_id": "HT-0001", "name": "Harbor Inn", "price_usd": 189}]}}
CAR = {"kind": "read_only", "delay_range_ms": [800, 1600], "description": "Quote a rental car at a city's airport.",
       "args": {"pickup_city": {"type": "string", "required": True},
                "car_class": {"type": "string", "required": False, "enum": ["economy", "suv", "luxury"]}},
       "default_result": {"quote_id": "RC-7781", "daily_usd": 58}}


def manifest(**extra):
    return Manifest({**TOOL_REGISTRY, **extra})


def args_for(m, tool, text, slots=None, **kw):
    p = nlu.parse(text)
    s = dict(p.slots)
    s.update(slots or {})
    return build_args(m.get(tool), ArgContext(slots=s, text=text, parse=p, **kw))


def test_saga_tags_come_from_kind_and_compensators():
    m = manifest(purchase_seat_upgrade={"kind": "state_modifying", "args": {"booking_id": {"type": "string", "required": True}}})
    assert m.tag("flight_search") == REVERSIBLE
    assert m.tag("book_flight") == COMPENSABLE and m.compensator("book_flight") == "cancel_booking"
    assert m.tag("create_support_ticket") == IRREVERSIBLE
    # cancel_booking takes a booking_id but does not undo an upgrade.
    assert m.tag("purchase_seat_upgrade") == IRREVERSIBLE


def test_nested_object_args_validate_against_the_kits_own_validator():
    m = manifest()
    args, missing = args_for(m, "create_support_ticket", "Open a ticket for my QN90, the LED keeps blinking red",
                             {"device_model": "QN90"})
    assert not missing
    assert args["device"] == {"model": "QN90"} and args["issue"]["severity"] in {"low", "medium", "high"}
    assert validate(m.get("create_support_ticket"), args) == []
    assert MockEnvironment("t")._validate_args(TOOL_REGISTRY["create_support_ticket"], args) == []


def test_enum_and_array_args():
    m = manifest()
    args, _ = args_for(m, "lookup_manual", "what is this port", device_hint="GENERIC", embedding=[0.1, 0.2])
    assert args["image_embedding"] == [0.1, 0.2] and args["device_model"] == "GENERIC"
    assert validate(m.get("lookup_manual"), {"query": "x", "device_model": "TV"}) != []


@pytest.mark.parametrize("tools, text, tool, expected", [
    ({"weather_lookup": WEATHER}, "What's the weather like in Denver right now?", "weather_lookup", {"city": "Denver"}),
    ({"hotel_search": HOTEL}, "Find me a hotel in Austin for two nights.", "hotel_search", {"city": "Austin", "nights": 2}),
    ({"rental_car_quote": CAR}, "How much is a rental car in Miami?", "rental_car_quote", {"pickup_city": "Miami"}),
    ({"weather_lookup": WEATHER}, "Is it raining in Seattle? In celsius please", "weather_lookup", {"city": "Seattle", "units": "metric"}),
])
def test_unseen_tools_routed_and_filled_from_schema_alone(tools, text, tool, expected):
    m = manifest(**tools)
    ranked = m.rank(nlu.parse(text))
    assert ranked[0][1] == tool
    args, missing = args_for(m, tool, text)
    assert not missing and {k: args[k] for k in expected} == expected
    assert validate(m.get(tool), args) == []


def test_idempotency_blocks_duplicate_writes_but_not_read_retries():
    s = Saga(manifest())
    book = s.open("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}, goal_id="g", step="book", version=1)
    assert s.blocked("book_flight", {"flight_id": "fl-a", "passenger_name": "al"}) is book     # normalised
    s.settle(book.call_id, "success", {"status": "success", "booking_id": "BK-1"})
    assert book.status == COMMITTED and s.blocked("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}) is book
    search = s.open("flight_search", {"destination": "X"}, goal_id="g", step="search", version=1)
    s.settle(search.call_id, "error", {"status": "error", "error": "timeout"})
    assert s.blocked("flight_search", {"destination": "X"}) is None                           # retry allowed


def test_ambiguous_write_failure_is_never_retried_blindly():
    s = Saga(manifest())
    c = s.open("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}, goal_id="g", step="book", version=1)
    s.settle(c.call_id, "error", {"status": "error", "error": "timeout"})
    assert c.status == UNKNOWN and s.blocked("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}) is c


def test_duplicate_booking_error_counts_as_committed():
    s = Saga(manifest())
    c = s.open("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}, goal_id="g", step="book", version=1)
    s.settle(c.call_id, "error", {"status": "error", "error": "duplicate_booking", "booking_id": "BK-9"})
    assert c.status == COMMITTED


def test_rollback_semantics():
    s = Saga(manifest())
    read = s.open("flight_search", {"destination": "X"}, goal_id="g", step="s", version=1)
    assert s.abort(read) == "cancel" and read.status == CANCELLED
    write = s.open("book_flight", {"flight_id": "FL-A", "passenger_name": "Al"}, goal_id="g", step="b", version=1)
    s.settle(write.call_id, "success", {"status": "success", "booking_id": "BK-7"})
    assert s.abort(write) == "compensate"
    assert s.compensation_args(write) == ("cancel_booking", {"booking_id": "BK-7"})
    ticket = s.open("create_support_ticket", {"device": {"model": "QN90"}, "issue": {"summary": "x", "severity": "low"}},
                    goal_id="g", step="t", version=1)
    assert ticket.status == IN_FLIGHT and s.abort(ticket) == "cancel"
