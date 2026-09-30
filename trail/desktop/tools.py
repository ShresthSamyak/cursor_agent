"""Desktop booking tools over the fictional Skylark Air corpus, saga-tagged (PDF p. 8, 11).

search / hold / policy are reversible (read_only), booking is compensable (cancel
exists), payment is irreversible and so waits at the commit barrier. The manifest
uses the kit's schema shape, so the same runtime plans and guards these calls.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).resolve().parent / "corpus" / "travel.json"

MANIFEST: dict[str, Any] = {
    "fare_search": {
        "kind": "read_only", "delay_range_ms": [400, 900],
        "description": "Search Skylark Air fares on a route for a day.",
        "args": {"route": {"type": "string", "required": True, "description": "Route, e.g. 'Chandigarh → Goa'."},
                 "day": {"type": "string", "required": False, "description": "Day of travel."}},
        "default_result": {"fares": []},
    },
    "hold_fare": {
        "kind": "read_only", "delay_range_ms": [300, 700],
        "description": "Hold a fare for 15 minutes without paying (released automatically).",
        "args": {"route": {"type": "string", "required": True}, "day": {"type": "string", "required": True},
                 "passengers": {"type": "number", "required": False}},
        "default_result": {"hold_id": "HD-0001"},
    },
    "book_fare": {
        "kind": "state_modifying", "delay_range_ms": [600, 1200],
        "description": "Book a held Skylark Air fare for the passengers.",
        "args": {"route": {"type": "string", "required": True}, "day": {"type": "string", "required": True},
                 "passengers": {"type": "number", "required": False}},
        "default_result": {"booking_id": "SK-BK-0001", "amount_inr": 0},
    },
    "cancel_booking": {
        "kind": "state_modifying", "delay_range_ms": [400, 800],
        "description": "Cancel an unpaid Skylark Air booking.",
        "args": {"booking_id": {"type": "string", "required": True}},
        "default_result": {"cancelled": ""},
    },
    "pay_booking": {
        "kind": "state_modifying", "delay_range_ms": [800, 1500],
        "description": "Pay for a booking. Final: payments cannot be undone.",
        "args": {"booking_id": {"type": "string", "required": True}, "amount_inr": {"type": "number", "required": True}},
        "default_result": {"payment_id": "PY-0001"},
    },
    "baggage_policy_lookup": {
        "kind": "read_only", "delay_range_ms": [300, 700],
        "description": "Answer baggage, refund, fare-rule and check-in questions from Skylark Air's policy.",
        "args": {"topic": {"type": "string", "required": True,
                           "enum": ["baggage", "refund", "fare_rules", "check_in", "infants"]}},
        "default_result": {"answer": ""},
    },
}


class DesktopTools:
    """Executes the manifest above against the corpus with realistic delays."""

    def __init__(self, corpus_path: Path | None = None, *, speed: float = 1.0, seed: int = 7) -> None:
        self.corpus = json.loads((corpus_path or CORPUS_PATH).read_text(encoding="utf-8"))
        self.speed = max(speed, 0.01)
        self.rng = random.Random(seed)
        self.bookings: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.holds = 0

    def _fare(self, route: str, day: str) -> dict[str, Any] | None:
        r = self._route(route)
        if r is None:
            return None
        d = (day or "").lower()[:3]
        return next((f for f in r["fares"] if f["day"].lower().startswith(d)), None)

    def _route(self, route: str) -> dict[str, Any] | None:
        routes = self.corpus["routes"]
        if route in routes:
            return routes[route]
        key = next((k for k in routes if route and route.split()[0].lower() in k.lower()), None)
        return routes.get(key) if key else next(iter(routes.values()))

    async def __call__(self, call_id: str, api: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = MANIFEST.get(api)
        if spec is None:
            return {"status": "error", "error": "unknown_tool", "detail": f"No tool named {api}"}
        lo, hi = spec["delay_range_ms"]
        await asyncio.sleep(self.rng.uniform(lo, hi) / 1000.0 / self.speed)
        pax = int(args.get("passengers") or 1)
        if api == "fare_search":
            r = self._route(str(args.get("route", "")))
            fares = r["fares"] if r else []
            if args.get("day"):
                fares = [f for f in fares if f["day"].lower().startswith(str(args["day"]).lower()[:3])]
            return {"status": "success", "currency": "INR", "fares": fares}
        if api == "hold_fare":
            f = self._fare(str(args.get("route", "")), str(args.get("day", "")))
            if f is None:
                return {"status": "error", "error": "not_found", "detail": "no such fare"}
            self.holds += 1
            return {"status": "success", "hold_id": f"HD-{self.holds:04d}", "flight_id": f["flight_id"],
                    "amount_inr": f["price"] * pax, "expires_in_min": 15}
        if api == "book_fare":
            f = self._fare(str(args.get("route", "")), str(args.get("day", "")))
            if f is None:
                return {"status": "error", "error": "not_found", "detail": "no such fare"}
            bid = f"SK-BK-{len(self.bookings) + 1:04d}"
            self.bookings[bid] = {"flight_id": f["flight_id"], "amount_inr": f["price"] * pax, "passengers": pax}
            return {"status": "success", "booking_id": bid, "flight_id": f["flight_id"], "amount_inr": f["price"] * pax}
        if api == "cancel_booking":
            bid = str(args.get("booking_id", ""))
            if bid in self.payments:
                return {"status": "error", "error": "not_found", "detail": "paid bookings cannot be cancelled here"}
            if self.bookings.pop(bid, None) is None:
                return {"status": "error", "error": "not_found", "detail": f"no booking {bid}"}
            return {"status": "success", "cancelled": bid}
        if api == "pay_booking":
            bid = str(args.get("booking_id", ""))
            if bid not in self.bookings:
                return {"status": "error", "error": "not_found", "detail": f"no booking {bid}"}
            pid = f"PY-{len(self.payments) + 1:04d}"
            self.payments[bid] = {"payment_id": pid, "amount_inr": args.get("amount_inr")}
            return {"status": "success", "payment_id": pid, "booking_id": bid}
        if api == "baggage_policy_lookup":
            topic = str(args.get("topic", "baggage"))
            c = self.corpus
            answer = {
                "baggage": f"{c['baggage']['cabin']} {c['baggage']['checked']}",
                "refund": c["policy"]["refund_policy"],
                "fare_rules": " ".join(f"{k.title()}: {v['refund']} {v['change']}" for k, v in c["fare_rules"].items()),
                "check_in": c["policy"]["check_in"],
                "infants": c["policy"]["infants"],
            }.get(topic, "")
            return {"status": "success", "topic": topic, "answer": answer}
        return {"status": "error", "error": "unknown_tool"}
