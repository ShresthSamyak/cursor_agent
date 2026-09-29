"""Deterministic grounded demo, deliberately unable to book or invent policy."""

import asyncio
import re
from typing import AsyncIterator

from .base import TurnRequest
from ..state import PreparedTurn

_FARE = re.compile(r"(?:INR|Rs\.?|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_DAY = re.compile(r"\b(Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b", re.IGNORECASE)


class OfflineProvider:
    def __init__(self, *, chunk_delay: float = 0.025) -> None:
        self.chunk_delay = chunk_delay
        self.prepare_calls = 0

    async def prepare(self, request: TurnRequest) -> PreparedTurn:
        self.prepare_calls += 1
        # Always yield control, including when no network I/O is needed.
        await asyncio.sleep(0)
        prompt = request.prompt.lower()
        fares = []
        for entry in request.evidence:
            price, day = _FARE.search(entry.text), _DAY.search(entry.text)
            if price and day:
                fares.append((float(price[1].replace(",", "")), day[1], entry))
        if request.image_ref:
            answer = "The offline provider cannot interpret images. Connect a vision-capable provider to answer this."
        elif any(word in prompt for word in ("pay", "purchase", "book it")):
            answer = "No booking or payment was made. Action tools are not enabled in this core milestone."
        elif any(word in prompt for word in ("baggage", "refund", "policy")):
            answer = "I do not have the travel policy corpus in this session, so I cannot verify that policy."
        elif fares and any(word in prompt for word in ("cheap", "which", "fare", "flight")):
            # Never compare unrelated routes as though they were equivalent.
            route = fares[-1][2].context
            fares = [fare for fare in fares if fare[2].context == route]
            price, day, _ = min(fares, key=lambda fare: fare[0])
            dates = "date" if len(fares) == 1 else "dates"
            answer = (
                f"Of the {len(fares)} {dates} in your session for {route or 'the current route'}, "
                f"{day} is cheapest at INR {price:,.0f}. "
                "This uses the fares you supplied; live availability has not been checked."
            )
        else:
            answer = "The core is running in offline replay mode. Supply dated INR fares and ask which is cheapest to exercise a grounded answer."
        chunks = tuple(re.findall(r"\S+\s*", answer))
        return PreparedTurn(chunks=chunks, evidence=request.evidence, plan=("read session evidence", "answer from evidence"))

    async def stream(self, request: TurnRequest, prepared: PreparedTurn, start: int) -> AsyncIterator[str]:
        for chunk in prepared.chunks[start:]:
            await asyncio.sleep(self.chunk_delay)
            yield chunk
