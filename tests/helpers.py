import asyncio
from contextlib import asynccontextmanager

from trail.core.bus import Event
from trail.core.runtime import Runtime
from trail.core.state import PreparedTurn


class ControlledProvider:
    """Synchronization points instead of timing-dependent test sleeps."""

    def __init__(self, *, block_prepare=False):
        self.prepare_calls = 0
        self.stream_calls = 0
        self.cancelled = False
        self.prepare_started = asyncio.Event()
        self.prepare_gate = asyncio.Event()
        if not block_prepare:
            self.prepare_gate.set()
        self.permits = asyncio.Queue()
        self.pulled = asyncio.Queue()
        self.requests = []

    async def prepare(self, request):
        self.prepare_calls += 1
        self.requests.append(request)
        self.prepare_started.set()
        try:
            await self.prepare_gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return PreparedTurn(chunks=("A ", "B ", "C"), evidence=request.evidence, plan=("retrieve", "answer"))

    async def stream(self, request, prepared, start):
        self.stream_calls += 1
        try:
            for index, chunk in enumerate(prepared.chunks[start:], start):
                self.pulled.put_nowait(index)
                await self.permits.get()
                yield chunk
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def release(self, count=1):
        for _ in range(count):
            self.permits.put_nowait(True)


class Session:
    def __init__(self, provider, **kwargs):
        self.runtime = Runtime(provider, **kwargs)
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.history = []
        self.task = asyncio.create_task(self.runtime.run(self.incoming, self.outgoing))

    async def send(self, kind, **kwargs):
        event = Event(type=kind, **kwargs)
        await self.incoming.put(event)
        return event

    async def until(self, kind, *, event_id=None):
        async with asyncio.timeout(2):
            while True:
                result = await self.outgoing.get()
                self.outgoing.task_done()
                self.history.append(result)
                if result.type == kind and (event_id is None or result.event_id == event_id):
                    return result

    async def ack(self, kind, **kwargs):
        event = await self.send(kind, **kwargs)
        await self.until("event_ack", event_id=event.event_id)
        return event


@asynccontextmanager
async def session(provider, **kwargs):
    live = Session(provider, **kwargs)
    await live.until("session_started")
    try:
        yield live
    finally:
        if not live.task.done():
            await live.send("session_end")
        await asyncio.wait_for(live.task, 2)
