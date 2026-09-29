from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from ..state import Evidence, PreparedTurn


@dataclass(frozen=True)
class TurnRequest:
    session_id: str
    turn_id: str
    version: int
    prompt: str
    evidence: tuple[Evidence, ...]
    image_ref: str | None = None


class Provider(Protocol):
    async def prepare(self, request: TurnRequest) -> PreparedTurn:
        """Retrieve and prepare an immutable result; never mutate session state."""
        ...

    def stream(
        self, request: TurnRequest, prepared: PreparedTurn, start: int
    ) -> AsyncIterator[str]:
        """Yield chunks starting at a checkpoint. Must propagate cancellation."""
        ...
