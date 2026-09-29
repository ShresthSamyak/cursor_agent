"""Validated local protocol; the official kit mapping belongs in agent.py."""

from enum import Enum
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(str, Enum):
    USER_TEXT = "user_text"
    SPEECH_PARTIAL = "speech_partial"
    SPEECH_FINAL = "speech_final"
    VAD_START = "vad_start"
    VAD_END = "vad_end"
    CANCEL = "cancel"
    RESUME = "resume"
    DWELL = "dwell"
    HOVER = "hover"
    SELECT = "select"
    APP_SWITCH = "app_switch"
    TYPING = "typing"
    SAVE = "save"
    TEST_RUN = "test_run"
    IMAGE = "image"
    SESSION_END = "session_end"


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=8000)
    context: str = Field(default="", max_length=2000)
    role: str = Field(default="text", max_length=100)
    sensitive: bool = False
    dwell_ms: int = Field(default=350, ge=0, le=600_000)
    bbox: tuple[float, float, float, float] | None = None


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    type: EventType
    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    ts: float = Field(default_factory=time, ge=0)
    source: str = Field(default="local", max_length=100)
    app: str = Field(default="", max_length=100)
    text: str = Field(default="", max_length=16_000)
    target: Target | None = None
    # Only an opaque reference: the portable core never opens files or URLs.
    image_ref: str | None = Field(default=None, max_length=2000)
    active: bool | None = None

    @model_validator(mode="after")
    def check_payload(self) -> "Event":
        if self.type in {EventType.USER_TEXT, EventType.SPEECH_FINAL, EventType.SPEECH_PARTIAL}:
            if not self.text.strip():
                raise ValueError("text events require non-empty text")
        if self.type in {EventType.DWELL, EventType.HOVER, EventType.SELECT} and self.target is None:
            raise ValueError("attention events require a target")
        if self.type == EventType.IMAGE and not self.image_ref:
            raise ValueError("image events require an opaque image_ref")
        if self.type == EventType.TYPING and self.active is None:
            raise ValueError("typing events require active=true/false")
        return self


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    session_id: str
    version: int
    turn_id: str | None = None
    text: str = ""
    code: str | None = None
    event_id: str | None = None
