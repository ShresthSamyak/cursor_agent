"""The kit's minimal reference agent (~52-57/100 on the public set), kept for comparison.

Copied from the Theme 05 kit's agent/agent.py via the public repository
HarshRaj1607/interruptable-agent, which states it is unchanged kit code.
It is not Trail's submission; run_local.py uses it when --agent is omitted.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

CITY_CANON = {
    "boston": "Boston", "bos": "Boston",
    "new york": "New York", "nyc": "New York",
    "chicago": "Chicago", "denver": "Denver",
    "seattle": "Seattle", "miami": "Miami", "austin": "Austin",
}
_CITY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(CITY_CANON, key=len, reverse=True)) + r")\b", re.I)


class BaselineAgent:
    """Minimal reference agent from the hackathon kit — handles only
    pub_01/pub_02 (single-city flight search + one interruption). Kept
    here as the `run_local.py` default (`--agent agent.agent:BaselineAgent`)
    so we always have a ~57/100 baseline to beat. Not our submission —
    ParticipantAgent above is the real entry point.
    """

    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
        self.in_q = in_queue
        self.out_q = out_queue
        self.buffer: List[str] = []
        self.state: Dict[str, Any] = {"intent": None, "slots": {}}
        self.call_seq = 0
        self.pending: Dict[str, Dict] = {}
        self.tools: Dict[str, Any] = {}

    async def emit(self, action: str, payload: Dict[str, Any]):
        msg: Dict[str, Any] = {"action": action, "payload": payload}
        if action == "final_response":
            msg["state_snapshot"] = {"intent": self.state["intent"],
                                     "slots": dict(self.state["slots"])}
        await self.out_q.put(msg)

    async def call_tool(self, api_name: str, args: Dict[str, Any]) -> str:
        self.call_seq += 1
        call_id = f"c{self.call_seq}"
        self.pending[call_id] = {"api": api_name, "args": args}
        await self.emit("tool_call",
                        {"call_id": call_id, "api_name": api_name, "args": args})
        return call_id

    async def cancel_all_pending(self):
        for call_id in list(self.pending):
            await self.emit("cancel_tool", {"call_id": call_id})
            del self.pending[call_id]

    @staticmethod
    def find_city(text: str) -> Optional[str]:
        matches = _CITY_PATTERN.findall(text)
        return CITY_CANON[matches[-1].lower()] if matches else None

    async def run(self):
        while True:
            event = await self.in_q.get()
            etype = event.get("event_type")
            payload = event.get("payload", {})
            if etype == "tool_manifest":
                self.tools = payload.get("tools", {})
            elif etype == "user_speech_chunk":
                await self.on_user_text(payload.get("text", ""),
                                        payload.get("end_of_turn", False))
            elif etype == "interruption":
                await self.on_interruption(payload.get("text", ""))
            elif etype == "tool_result":
                await self.on_tool_result(payload)

    async def on_user_text(self, text: str, end_of_turn: bool):
        self.buffer.append(text)
        if not end_of_turn:
            return
        turn = " ".join(self.buffer).strip()
        self.buffer = []
        low = turn.lower()

        if any(w in low for w in ("flight", "fly", "flights")):
            city = self.find_city(low)
            if city is None:
                await self.emit("clarification_request",
                                {"text": "Sure — which city would you like to fly to?"})
                return
            self.state["intent"] = "book_flight"
            self.state["slots"]["destination"] = city
            await self.emit("filler_speech",
                            {"text": f"Looking up flights to {city} — one moment."})
            await self.call_tool("flight_search", {"destination": city})
            return

        self.state["intent"] = "chitchat"
        await self.emit("final_response",
                        {"text": "Hi! I can help you search for flights — "
                                 "just tell me where you want to fly."})

    async def on_interruption(self, text: str):
        new_city = self.find_city(text)
        await self.emit("filler_speech",
                        {"text": f"Got it — switching to {new_city}." if new_city
                                 else "Okay, one moment."})
        await self.cancel_all_pending()
        if new_city:
            self.state["intent"] = "book_flight"
            self.state["slots"]["destination"] = new_city
            await self.call_tool("flight_search", {"destination": new_city})

    async def on_tool_result(self, payload: Dict[str, Any]):
        call_id = payload.get("call_id", "")
        if self.pending.pop(call_id, None) is None:
            return
        result = payload.get("result", {})

        if payload.get("status") == "error":
            await self.emit("final_response",
                            {"text": "Sorry — I couldn't complete that right now."})
            return

        flights = result.get("flights", [])
        if not flights:
            await self.emit("final_response",
                            {"text": "I couldn't find any flights for that search."})
            return
        best = flights[0]
        self.state["slots"]["flight_id"] = best["flight_id"]
        await self.emit("final_response",
                        {"text": f"I found a flight to "
                                 f"{self.state['slots']['destination']}: "
                                 f"{best['flight_id']} departing {best['depart']} "
                                 f"for ${best['price_usd']}."})
