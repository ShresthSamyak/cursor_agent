"""One event envelope for every source: kit harness, browser, editor, microphone.

The runtime never knows which source produced an event (PDF p. 9). Adapters
(agent.py for the kit, desktop/bridge.py for the demo layer) translate into
and out of these models.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(str, Enum):
    MANIFEST = "tool_manifest"
    SPEECH_PARTIAL = "speech_partial"     # user text, turn not finished
    SPEECH_FINAL = "speech_final"         # user text, turn finished (barge_in marks an interruption)
    AUDIO = "audio"                       # raw audio clip reference
    FRAME = "frame"                       # camera frame / screenshot reference
    TOOL_RESULT = "tool_result"
    INPUT_END = "input_end"               # no more scripted user input (kit scenario_end)
    SESSION_END = "session_end"
    # Desktop perception
    DWELL = "dwell"
    HOVER = "hover"
    SELECT = "select"
    APP_SWITCH = "app_switch"
    TYPING = "typing"
    SAVE = "save"
    TEST_RUN = "test_run"
    VAD_START = "vad_start"
    VAD_END = "vad_end"
    CANCEL = "cancel"                     # Esc / stop key
    RESUME = "resume"                     # "go on" button
    NOT_NOW = "not_now"
    DOC_CHANGE = "doc_change"             # editor text changed (code mentor)
    TERMINAL = "terminal"                 # terminal output (stack traces)
    TOOL_EXEC = "tool_exec"               # desktop: execute a tool call locally (internal)


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=8000)
    context: str = Field(default="", max_length=2000)
    role: str = Field(default="text", max_length=100)
    sensitive: bool = False
    dwell_ms: float = Field(default=350, ge=0, le=600_000)
    bbox: tuple[float, float, float, float] | None = None
    url: str | None = Field(default=None, max_length=2000)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    type: EventType
    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    ts: float | None = Field(default=None, ge=0)          # virtual ms (kit) or ms since epoch (desktop)
    source: str = Field(default="local", max_length=100)
    app: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=32_000)
    barge_in: bool = False
    target: Target | None = None
    media_ref: str | None = Field(default=None, max_length=2000)
    duration_ms: float | None = None
    final: bool = True
    frame_id: str | None = Field(default=None, max_length=200)
    device_hint: str | None = Field(default=None, max_length=100)
    call_id: str | None = Field(default=None, max_length=200)
    api_name: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=50)
    result: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    active: bool | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_payload(self) -> "Event":
        if self.type in {EventType.DWELL, EventType.HOVER, EventType.SELECT} and self.target is None:
            raise ValueError("attention events require a target")
        if self.type in {EventType.AUDIO, EventType.FRAME} and not self.media_ref:
            raise ValueError("media events require media_ref")
        if self.type == EventType.TOOL_RESULT and not self.call_id:
            raise ValueError("tool_result requires call_id")
        if self.type == EventType.TYPING and self.active is None:
            raise ValueError("typing events require active=true/false")
        return self


class Output(BaseModel):
    """What the runtime wants done. Adapters render it (kit action, bubble, voice)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str                                   # speak, token, tool_call, tool_cancel, duck, unduck, status, ...
    session_id: str
    version: int
    turn: int = 0
    kind: str | None = None                     # for speak: ack, clarify, final, notice
    text: str = ""
    snapshot: dict[str, Any] | None = None
    call_id: str | None = None
    api_name: str | None = None
    args: dict[str, Any] | None = None
    code: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
