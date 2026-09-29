"""ParticipantAgent shape with an explicit, replaceable protocol boundary.

The supplied LocalProtocol is NOT a verified Theme 05 protocol implementation.
Map the official kit only here once its reference and fixtures are available.
"""

import asyncio
from typing import Any, Protocol

from pydantic import ValidationError

from .bus import Event, EventType, Output
from .llm.base import Provider
from .llm.offline import OfflineProvider
from .runtime import Runtime


class ProtocolAdapter(Protocol):
    def decode(self, message: Any) -> Event: ...
    def encode(self, output: Output) -> dict[str, Any]: ...


class LocalProtocol:
    def decode(self, message: Any) -> Event:
        if message is None:
            return Event(type=EventType.SESSION_END)
        return Event.model_validate(message)

    def encode(self, output: Output) -> dict[str, Any]:
        return output.model_dump(mode="json", exclude_none=True)


class ParticipantAgent:
    def __init__(
        self, in_queue: asyncio.Queue, out_queue: asyncio.Queue, *,
        provider: Provider | None = None, protocol: ProtocolAdapter | None = None,
    ) -> None:
        if out_queue.maxsize > 0:
            raise ValueError("out_queue must be unbounded")
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.protocol = protocol or LocalProtocol()
        self.runtime = Runtime(provider or OfflineProvider())

    async def setup(self) -> None:
        """Reserved for kit/model setup; no credentials are needed for replay."""

    async def run(self) -> None:
        events: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        outputs: asyncio.Queue[Output] = asyncio.Queue()

        async def ingest() -> None:
            while True:
                raw = await self.in_queue.get()
                try:
                    try:
                        event = self.protocol.decode(raw)
                    except (ValidationError, ValueError, TypeError):
                        state = self.runtime.state
                        self.out_queue.put_nowait(self.protocol.encode(Output(
                            type="error", session_id=state.session_id, version=state.version,
                            code="protocol_error", text="Invalid input message; see docs/protocol.md.",
                        )))
                        continue
                    await events.put(event)
                    if event.type == EventType.SESSION_END:
                        return
                finally:
                    self.in_queue.task_done()

        async def relay() -> None:
            while True:
                output = await outputs.get()
                try:
                    self.out_queue.put_nowait(self.protocol.encode(output))
                    if output.type == "session_ended":
                        return
                finally:
                    outputs.task_done()

        runtime = asyncio.create_task(self.runtime.run(events, outputs), name="trail-runtime")
        feeder = asyncio.create_task(ingest(), name="trail-input")
        forwarder = asyncio.create_task(relay(), name="trail-output")
        tasks = (runtime, feeder, forwarder)
        try:
            # Fail promptly if any boundary fails, including encoding errors.
            while not runtime.done():
                pending = [task for task in tasks if not task.done()]
                await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception():
                        raise task.exception()
            await runtime
            await forwarder
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
