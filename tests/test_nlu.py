import pytest

from trail.core import entities as ent
from trail.core import nlu


@pytest.mark.parametrize("text, intent, slots", [
    ("Can you find flights to Chicago for Friday?", "flight_search", {"destination": "Chicago", "date": "Friday"}),
    ("Please book a flight to Boston for tomorrow.", "book_flight", {"destination": "Boston", "date": "tomorrow"}),
    ("Find a flight to Denver and book the 8 AM one for Alice.", "book_flight",
     {"destination": "Denver", "passenger_name": "Alice", "flight_pref": {"time": 480}}),
    ("I need to get to Denver", "flight_search", {"destination": "Denver"}),
    ("any seats to Denver Friday", "flight_search", {"destination": "Denver", "date": "Friday"}),
    ("Denver, Friday, book it", "book_flight", {"destination": "Denver", "date": "Friday"}),
    ("cancel my booking BK-0001", "cancel_booking", {"booking_id": "BK-0001"}),
    ("Please open a support ticket for my QN90, the screen has no picture.", "create_support_ticket", {}),
    ("Hey, what can you help me with?", "capabilities", {}),
])
def test_intents_and_slots(text, intent, slots):
    p = nlu.parse(text)
    assert p.intent == intent
    for k, v in slots.items():
        assert p.slots.get(k) == v


def test_self_repair_keeps_the_last_value_and_supersedes_the_first():
    p = nlu.parse("Uh, book a flight to Boston. Actually, make that New York.")
    assert p.slots["destination"] == "New York"
    assert "Boston" in p.superseded.get("place", [])
    assert "repair" in p.acts


def test_negation_rejects_a_value():
    p = nlu.parse("Oh wait, the ticket should be for Sam, not Nina.")
    assert p.slots["passenger_name"] == "Sam"
    assert p.intent is None          # "ticket" alone is neither a flight nor a support ticket


@pytest.mark.parametrize("text, act", [
    ("mm-hm", "backchannel"), ("Okay.", "backchannel"), ("Stop.", "stop"), ("Actually, never mind.", "stop"),
    ("Wait, don't open that ticket.", "stop"), ("what does that mean?", "clarify_q"), ("back to the flight", "back_to"),
    ("Yes, please open one.", "confirm"), ("No thanks, it's fine now.", "deny"), ("go on", "resume"),
    ("not now", "not_now"),
])
def test_dialog_acts(text, act):
    assert act in nlu.parse(text).acts


def test_backchannel_is_not_a_request():
    p = nlu.parse("mm-hm")
    assert p.intent is None and not p.slots


def test_place_roles():
    places = ent.find_places("Fly from Chicago to Seattle")
    assert [(p.name, p.role) for p in places] == [("Chicago", "origin"), ("Seattle", "destination")]
    assert ent.find_places("sea view")[:1] == []            # lowercase codes are words, not airports
    assert ent.find_places("Land at SEA")[0].name == "Seattle"


def test_dates_times_prices_counts():
    assert [d.value for d in ent.find_dates("next Friday or the day after tomorrow")] == ["next Friday", "day after tomorrow"]
    assert ent.find_times("the 8 AM one")[0].minutes == 480
    assert ent.find_times("around 2:30 pm")[0].minutes == 14 * 60 + 30
    assert ent.parse_clock("14:00") == 840
    prices = ent.find_prices("Sat · ₹5,000 or INR 4,500 or $99")
    assert [(p.amount, p.currency) for p in prices] == [(5000, "INR"), (4500, "INR"), (99, "USD")]
    assert ent.find_counts("for two people")[0].value == 2


def test_names_are_not_fillers_places_or_days():
    assert ent.bare_name("Okay.") is None
    assert ent.bare_name("Alice Johnson") == "Alice Johnson"
    assert nlu.parse("Book the cheapest flight to Denver for Priya please.").person == "Priya"
    assert nlu.parse("Please book a flight to Boston for tomorrow.").person is None


def test_confusable_places_for_clarification():
    assert "Austin" in ent.confusable_cities("Boston")
    assert ent.confusable_cities("New York") == []


def test_device_issue_summary_and_severity():
    p = nlu.parse("My WF45 washer is leaking water.")
    assert p.intent == "device_support"
    assert p.slots["issue_summary"] == "WF45 washer is leaking water"
    assert p.slots["severity"] == "high"
    assert "WF45" in p.model_tokens
