"""Immutable snapshots. Only Runtime's event loop replaces session state."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4


class Phase(str, Enum):
    LISTENING = "listening"
    PLANNING = "planning"
    SPEAKING = "speaking"
    PAUSED = "paused"
    DONE = "done"
    CLOSED = "closed"


@dataclass(frozen=True)
class Evidence:
    id: str
    text: str
    context: str = ""
    app: str = ""
    source: str = ""
    timestamp: float = 0.0
    untrusted: bool = True


@dataclass(frozen=True)
class PreparedTurn:
    """Provider result. No tool is executed by this core milestone."""

    chunks: tuple[str, ...]
    evidence: tuple[Evidence, ...] = ()
    plan: tuple[str, ...] = ()
    pending_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Checkpoint:
    prompt: str
    partial_answer: str = ""
    next_chunk: int = 0
    plan_position: int = 0
    prepared: PreparedTurn | None = None
    evidence: tuple[Evidence, ...] = ()
    pending_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionState:
    session_id: str = field(default_factory=lambda: uuid4().hex)
    # A semantic epoch, not a token counter. Progress cannot invalidate itself.
    version: int = 0
    turn_id: str | None = None
    phase: Phase = Phase.LISTENING
    ducked: bool = False
    speaking: bool = False
    typing: bool = False
    checkpoint: Checkpoint | None = None
    evidence: tuple[Evidence, ...] = ()
    active_app: str = ""


@dataclass
class Metrics:
    turns_started: int = 0
    turns_cancelled: int = 0
    turns_resumed: int = 0
    stale_proposals_dropped: int = 0
    duplicate_events_dropped: int = 0
    tokens_emitted: int = 0
    errors: int = 0
    max_yield_ms: float = 0.0
