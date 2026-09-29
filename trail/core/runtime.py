"""One state writer, cancellable workers, and version-checked output delivery."""

import asyncio
from collections import deque
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any
from uuid import uuid4

from .bus import Event, EventType, Output
from .llm.base import Provider, TurnRequest
from .privacy import redact, safe_target
from .state import Checkpoint, Evidence, Metrics, Phase, PreparedTurn, SessionState


@dataclass(frozen=True)
class Proposal:
    version: int
    turn_id: str
    kind: str
    value: Any = None
    accepted: asyncio.Future[bool] | None = None


class Runtime:
    """run() owns state. Providers receive snapshots and only submit proposals.

    A sink must be an unbounded asyncio.Queue. Emission never awaits a slow
    consumer, so checking the epoch and publishing an output are atomic within
    this event loop. Consumers must discard queued audio/text on `duck` and use
    the version and turn_id fields when rendering. No external tools run here.
    """

    def __init__(self, provider: Provider, *, max_evidence: int = 256) -> None:
        if max_evidence < 1:
            raise ValueError("max_evidence must be positive")
        self.provider = provider
        self.max_evidence = max_evidence
        self._state = SessionState()
        self.metrics = Metrics()
        self._proposals: asyncio.Queue[Proposal] = asyncio.Queue(maxsize=16)
        self._workers: set[asyncio.Task[None]] = set()
        self._worker: asyncio.Task[None] | None = None
        self._gate = asyncio.Event()
        self._gate.set()
        self._pending: deque[Proposal] = deque()
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._sink: asyncio.Queue[Output] | None = None
        self._owner: asyncio.Task | None = None
        self._prefetch = False
        self._image_ref: str | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    def _set(self, **changes: Any) -> None:
        if asyncio.current_task() is not self._owner:
            raise RuntimeError("session state can only be changed by the runtime loop")
        self._state = replace(self._state, **changes)

    def _emit(self, kind: str, *, text: str = "", code: str | None = None, event_id: str | None = None) -> None:
        assert self._sink is not None
        self._sink.put_nowait(Output(
            type=kind, session_id=self.state.session_id, version=self.state.version,
            turn_id=self.state.turn_id, text=text, code=code, event_id=event_id,
        ))

    def _duck(self) -> None:
        started = perf_counter()
        self._gate.clear()
        if not self.state.ducked:
            self._set(ducked=True)
            self._emit("duck")
        self.metrics.max_yield_ms = max(self.metrics.max_yield_ms, (perf_counter() - started) * 1000)

    def _unduck(self) -> None:
        self._set(ducked=False)
        self._gate.set()
        self._emit("unduck")
        while self._pending:
            self._apply(self._pending.popleft())

    def _invalidate(self) -> None:
        # The checkpoint is already updated synchronously on each accepted
        # proposal. Cancellation does not await network I/O or mutate state.
        self._set(version=self.state.version + 1)
        if self._worker and not self._worker.done():
            self._worker.cancel()
            self.metrics.turns_cancelled += 1
        self._worker = None
        while self._pending:
            self._reject(self._pending.popleft())

    def _request(self) -> TurnRequest:
        assert self.state.turn_id and self.state.checkpoint
        return TurnRequest(
            session_id=self.state.session_id, turn_id=self.state.turn_id,
            version=self.state.version, prompt=self.state.checkpoint.prompt,
            evidence=self.state.checkpoint.evidence, image_ref=self._image_ref,
        )

    def _spawn(self, *, prepare: bool) -> None:
        request = self._request()
        checkpoint = self.state.checkpoint
        assert checkpoint is not None
        task = asyncio.create_task(self._produce(request, checkpoint, prepare), name=f"trail-turn-{request.turn_id}")
        self._worker = task
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)

    async def _produce(self, request: TurnRequest, checkpoint: Checkpoint, prepare: bool) -> None:
        try:
            if prepare:
                result = await self.provider.prepare(request)
                await self._proposals.put(Proposal(request.version, request.turn_id, "prepared", result))
                return
            assert checkpoint.prepared is not None
            async for chunk in self.provider.stream(request, checkpoint.prepared, checkpoint.next_chunk):
                await self._gate.wait()
                if not isinstance(chunk, str) or not chunk or len(chunk) > 16_000:
                    raise ValueError("provider emitted an invalid chunk")
                accepted = asyncio.get_running_loop().create_future()
                await self._proposals.put(Proposal(request.version, request.turn_id, "token", chunk, accepted))
                # At most one outstanding token per producer. This bounds memory
                # and makes the checkpoint exactly match delivered output.
                if not await accepted:
                    return
            await self._proposals.put(Proposal(request.version, request.turn_id, "done"))
        except asyncio.CancelledError:
            # No I/O during unwind. The owner already holds the checkpoint.
            raise
        except Exception:
            # Provider exceptions can contain secrets or request bodies.
            await self._proposals.put(Proposal(request.version, request.turn_id, "error", "provider_failed"))

    def _reject(self, proposal: Proposal) -> None:
        self.metrics.stale_proposals_dropped += 1
        if proposal.accepted and not proposal.accepted.done():
            proposal.accepted.set_result(False)

    def _apply(self, proposal: Proposal) -> None:
        if (proposal.version, proposal.turn_id) != (self.state.version, self.state.turn_id):
            self._reject(proposal)
            return
        checkpoint = self.state.checkpoint
        assert checkpoint is not None
        if proposal.kind == "prepared":
            prepared = proposal.value
            if not isinstance(prepared, PreparedTurn):
                self._apply(Proposal(proposal.version, proposal.turn_id, "error", "invalid_provider_result"))
                return
            self._set(checkpoint=replace(checkpoint, prepared=prepared, evidence=prepared.evidence, pending_tools=prepared.pending_tools))
            if not self._prefetch:
                self._spawn(prepare=False)
            return
        if self.state.ducked and proposal.kind in {"token", "done"}:
            self._pending.append(proposal)
            return
        if proposal.kind == "token":
            if len(checkpoint.partial_answer) + len(proposal.value) > 128_000:
                if proposal.accepted and not proposal.accepted.done():
                    proposal.accepted.set_result(False)
                self._apply(Proposal(proposal.version, proposal.turn_id, "error", "answer_limit_exceeded"))
                return
            self._set(
                phase=Phase.SPEAKING,
                checkpoint=replace(checkpoint, partial_answer=checkpoint.partial_answer + proposal.value,
                                   next_chunk=checkpoint.next_chunk + 1,
                                   plan_position=max(0, len(checkpoint.prepared.plan) - 1) if checkpoint.prepared else 0),
            )
            self._emit("token", text=proposal.value)
            self.metrics.tokens_emitted += 1
            if proposal.accepted and not proposal.accepted.done():
                proposal.accepted.set_result(True)
        elif proposal.kind == "done":
            self._set(phase=Phase.DONE, checkpoint=replace(checkpoint, plan_position=len(checkpoint.prepared.plan) if checkpoint.prepared else 0))
            self._emit("done")
        elif proposal.kind == "error":
            self.metrics.errors += 1
            self._prefetch = False
            self._set(phase=Phase.PAUSED)
            self._emit("error", code=proposal.value, text="The provider failed. Resume to retry, or send a new request.")

    def _start(self, text: str, *, prefetch: bool = False, image_ref: str | None = None) -> None:
        self._invalidate()
        self._prefetch = prefetch
        self._image_ref = image_ref
        self._set(turn_id=uuid4().hex, phase=Phase.PLANNING,
                  checkpoint=Checkpoint(prompt=redact(text), evidence=self.state.evidence))
        self.metrics.turns_started += 1
        self._emit("turn_started", code="prefetch" if prefetch else None)
        if not prefetch and not self.state.speaking and not self.state.typing:
            self._unduck()
        self._spawn(prepare=True)

    def _remember(self, event: Event) -> None:
        assert event.target is not None
        target = safe_target(event.target)
        if target is None:
            self._emit("context_rejected", code="sensitive", event_id=event.event_id)
            return
        evidence = Evidence(id=event.event_id, text=target.text, context=target.context,
                            app=event.app, source=event.source, timestamp=event.ts)
        entries = tuple(e for e in self.state.evidence if (e.text, e.context, e.app) != (evidence.text, evidence.context, evidence.app))
        active = self.state.phase in {Phase.PLANNING, Phase.SPEAKING} and self.state.checkpoint is not None
        if active:
            self._duck()
        self._invalidate()
        self._set(evidence=(entries + (evidence,))[-self.max_evidence:])
        self._emit("context_added", event_id=event.event_id)
        if active:
            # Conservatively replan on new evidence. Targeted invalidation and
            # the seven-way classifier belong to the next gated milestone.
            checkpoint = self.state.checkpoint
            assert checkpoint
            self._start(checkpoint.prompt, prefetch=self._prefetch, image_ref=self._image_ref)
        elif self.state.phase == Phase.PAUSED and self.state.checkpoint:
            # A parked answer based on old evidence must not resume unchanged.
            self._set(checkpoint=Checkpoint(prompt=self.state.checkpoint.prompt, evidence=self.state.evidence))

    def _handle(self, event: Event) -> bool:
        if event.event_id in self._seen:
            self.metrics.duplicate_events_dropped += 1
            return True
        self._seen.add(event.event_id)
        self._seen_order.append(event.event_id)
        if len(self._seen_order) > 4096:
            self._seen.discard(self._seen_order.popleft())
        kind = event.type
        if kind == EventType.SESSION_END:
            return False
        if kind in {EventType.VAD_START, EventType.SPEECH_PARTIAL, EventType.SPEECH_FINAL,
                    EventType.USER_TEXT, EventType.CANCEL, EventType.IMAGE}:
            self._duck()
        if kind == EventType.VAD_START:
            self._set(speaking=True)
        elif kind == EventType.VAD_END:
            # Wait for final text or explicit resume; VAD silence is not intent.
            self._set(speaking=False)
        elif kind == EventType.TYPING:
            self._set(typing=bool(event.active))
            if event.active:
                self._duck()
        elif kind in {EventType.DWELL, EventType.SELECT}:
            self._remember(event)
        elif kind == EventType.SPEECH_PARTIAL:
            if not self.state.checkpoint or self.state.checkpoint.prompt != redact(event.text) or not self._prefetch:
                self._start(event.text, prefetch=True)
        elif kind in {EventType.USER_TEXT, EventType.SPEECH_FINAL, EventType.IMAGE}:
            if kind == EventType.SPEECH_FINAL:
                self._set(speaking=False)
            checkpoint = self.state.checkpoint
            if self._prefetch and checkpoint and checkpoint.prompt == redact(event.text) and kind != EventType.IMAGE:
                self._prefetch = False
                if not self.state.speaking and not self.state.typing:
                    self._unduck()
                if checkpoint.prepared:
                    self._spawn(prepare=False)
            else:
                self._start(event.text or "Describe this image.", image_ref=event.image_ref)
        elif kind == EventType.CANCEL:
            self._invalidate()
            self._prefetch = False
            self._set(phase=Phase.PAUSED)
            self._emit("cancelled")
        elif kind == EventType.RESUME:
            if self.state.speaking or self.state.typing:
                self._emit("status", code="user_active", text="Waiting for user activity to end.")
            elif self.state.phase == Phase.PAUSED and self.state.checkpoint:
                self._invalidate()
                self._set(turn_id=uuid4().hex, phase=Phase.PLANNING)
                self.metrics.turns_resumed += 1
                self._emit("turn_resumed")
                self._unduck()
                self._spawn(prepare=self.state.checkpoint.prepared is None)
            elif not self._prefetch:
                self._unduck()
        elif kind == EventType.APP_SWITCH:
            self._set(active_app=event.app)
        elif kind in {EventType.HOVER, EventType.SAVE, EventType.TEST_RUN}:
            self._emit("status", code="reserved_event", event_id=event.event_id)
        self._emit("event_ack", event_id=event.event_id)
        return True

    async def run(self, in_queue: asyncio.Queue[Event], out_queue: asyncio.Queue[Output]) -> None:
        if self._owner is not None or self.state.phase == Phase.CLOSED:
            raise RuntimeError("a Runtime runs exactly once per session")
        if out_queue.maxsize > 0:
            raise ValueError("out_queue must be unbounded; transport backpressure belongs outside the state writer")
        self._owner = asyncio.current_task()
        self._sink = out_queue
        incoming = asyncio.create_task(in_queue.get())
        proposed = asyncio.create_task(self._proposals.get())
        try:
            self._emit("session_started")
            running = True
            while running:
                await asyncio.wait((incoming, proposed), return_when=asyncio.FIRST_COMPLETED)
                # If input and output are ready together, user intent wins.
                if incoming.done():
                    event = incoming.result()
                    try:
                        if not isinstance(event, Event):
                            self.metrics.errors += 1
                            self._emit("error", code="invalid_event", text="Runtime input must be a validated Event.")
                        else:
                            running = self._handle(event)
                    finally:
                        in_queue.task_done()
                    if not running:
                        break
                    incoming = asyncio.create_task(in_queue.get())
                    # Let an already queued interruption reach the head before
                    # accepting another worker proposal.
                    if not in_queue.empty():
                        await asyncio.sleep(0)
                        continue
                if proposed.done():
                    self._apply(proposed.result())
                    self._proposals.task_done()
                    proposed = asyncio.create_task(self._proposals.get())
        finally:
            self._duck()
            self._invalidate()
            incoming.cancel()
            proposed.cancel()
            for worker in tuple(self._workers):
                worker.cancel()
            await asyncio.gather(incoming, proposed, *tuple(self._workers), return_exceptions=True)
            self._workers.clear()
            self._pending.clear()
            while not self._proposals.empty():
                self._proposals.get_nowait()
                self._proposals.task_done()
            self._seen.clear()
            self._seen_order.clear()
            self._image_ref = None
            self._set(phase=Phase.CLOSED, checkpoint=None, evidence=(), turn_id=None,
                      speaking=False, typing=False, active_app="")
            self._emit("session_ended")
