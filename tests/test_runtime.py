import asyncio
from dataclasses import FrozenInstanceError

import pytest

from trail.core.bus import Event
from trail.core.runtime import Proposal, Runtime
from trail.core.state import Phase, PreparedTurn
from tests.helpers import ControlledProvider, session


def test_cancel_then_resume_does_not_repeat_retrieval_or_tokens():
    async def check():
        provider = ControlledProvider()
        async with session(provider) as live:
            await live.send("user_text", text="flight")
            provider.release()
            await live.until("token")
            await live.send("cancel")
            await live.until("cancelled")
            checkpoint = live.runtime.state.checkpoint
            assert checkpoint.partial_answer == "A "
            assert checkpoint.next_chunk == 1
            assert checkpoint.plan_position == 1
            assert live.runtime.metrics.turns_cancelled == 1
            await live.send("resume")
            provider.release(2)
            await live.until("done")
            assert provider.prepare_calls == 1
            assert "".join(o.text for o in live.history if o.type == "token") == "A B C"
            assert live.runtime.metrics.turns_resumed == 1
    asyncio.run(check())


def test_cancel_during_retrieval_is_prompt_and_resumable():
    async def check():
        provider = ControlledProvider(block_prepare=True)
        async with session(provider) as live:
            await live.send("user_text", text="flight")
            await asyncio.wait_for(provider.prepare_started.wait(), 1)
            await live.send("cancel")
            await live.until("cancelled")
            assert live.runtime.state.phase == Phase.PAUSED
            assert live.runtime.state.checkpoint.prepared is None
            provider.prepare_gate.set()
            provider.release(3)
            await live.send("resume")
            await live.until("done")
            assert provider.prepare_calls == 2
    asyncio.run(check())


def test_vad_ducks_without_cancelling_and_explicit_resume_continues():
    async def check():
        provider = ControlledProvider()
        async with session(provider) as live:
            await live.send("user_text", text="flight")
            provider.release()
            await live.until("token")
            await live.ack("vad_start")
            version = live.runtime.state.version
            provider.release(2)
            await live.ack("resume")
            assert live.runtime.state.ducked
            assert not provider.cancelled
            assert live.runtime.state.version == version
            await live.ack("vad_end")
            assert live.runtime.state.ducked
            await live.send("resume")
            await live.until("done")
            assert "".join(o.text for o in live.history if o.type == "token") == "A B C"
    asyncio.run(check())


def test_partial_speech_prefetch_is_silent_and_reused_after_final():
    async def check():
        provider = ControlledProvider()
        async with session(provider) as live:
            await live.ack("vad_start")
            await live.ack("speech_partial", text="which flight")
            await asyncio.wait_for(provider.prepare_started.wait(), 1)
            assert provider.prepare_calls == 1
            await live.ack("speech_partial", text="which flight")
            assert provider.stream_calls == 0
            assert not any(o.type == "token" for o in live.history)
            provider.release(3)
            await live.send("speech_final", text="which flight")
            await live.until("done")
            assert provider.prepare_calls == 1
            assert provider.stream_calls == 1
    asyncio.run(check())


def test_changed_final_replaces_partial_prefetch():
    async def check():
        provider = ControlledProvider()
        async with session(provider) as live:
            await live.ack("speech_partial", text="Friday")
            await asyncio.wait_for(provider.prepare_started.wait(), 1)
            provider.release(3)
            await live.send("speech_final", text="Saturday")
            await live.until("done")
            assert provider.prepare_calls == 2
            assert provider.requests[-1].prompt == "Saturday"
            assert len({o.version for o in live.history if o.type == "token"}) == 1
    asyncio.run(check())


def test_old_proposal_cannot_publish_after_cancel():
    async def check():
        provider = ControlledProvider(block_prepare=True)
        async with session(provider) as live:
            await live.ack("user_text", text="old")
            old = live.runtime.state
            await live.ack("cancel")
            accepted = asyncio.get_running_loop().create_future()
            await live.runtime._proposals.put(Proposal(old.version, old.turn_id, "token", "STALE", accepted))
            assert await asyncio.wait_for(accepted, 1) is False
            assert live.runtime.metrics.stale_proposals_dropped >= 1
            await live.ack("app_switch", app="excel")
            assert not any(o.text == "STALE" for o in live.history)
    asyncio.run(check())


def test_cancellation_resistant_provider_result_is_discarded():
    class ResistantProvider(ControlledProvider):
        async def prepare(self, request):
            self.prepare_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return PreparedTurn(chunks=("STALE",))

    async def check():
        provider = ResistantProvider()
        async with session(provider) as live:
            await live.ack("user_text", text="old")
            await asyncio.wait_for(provider.prepare_started.wait(), 1)
            await live.ack("cancel")
            # A marker after the old producer's result synchronizes processing.
            rejected = asyncio.get_running_loop().create_future()
            await live.runtime._proposals.put(Proposal(-1, "old", "token", "marker", rejected))
            await asyncio.wait_for(rejected, 1)
            assert live.runtime.state.checkpoint.prepared is None
            assert provider.stream_calls == 0
    asyncio.run(check())


def test_queued_cancel_wins_over_ready_output():
    async def check():
        provider = ControlledProvider(block_prepare=True)
        async with session(provider) as live:
            await live.ack("user_text", text="old")
            state = live.runtime.state
            accepted = asyncio.get_running_loop().create_future()
            live.incoming.put_nowait(Event(type="cancel"))
            live.runtime._proposals.put_nowait(Proposal(state.version, state.turn_id, "token", "STALE", accepted))
            await live.until("cancelled")
            assert await asyncio.wait_for(accepted, 1) is False
            assert not any(o.text == "STALE" for o in live.history)
    asyncio.run(check())


def test_duplicate_event_id_does_not_restart_a_turn():
    async def check():
        provider = ControlledProvider(block_prepare=True)
        async with session(provider) as live:
            event = await live.ack("user_text", text="flight", event_id="one")
            await live.incoming.put(event)
            await live.ack("app_switch", app="chrome")
            assert live.runtime.metrics.turns_started == 1
            assert live.runtime.metrics.duplicate_events_dropped == 1
    asyncio.run(check())


def test_session_end_clears_context_checkpoint_and_workers():
    async def check():
        provider = ControlledProvider(block_prepare=True)
        async with session(provider) as live:
            await live.ack("dwell", target={"text": "private context"})
            await live.ack("user_text", text="question")
        state = live.runtime.state
        assert state.phase == Phase.CLOSED
        assert state.evidence == ()
        assert state.checkpoint is None
        assert not live.runtime._workers
        assert not live.runtime._seen
        assert live.runtime._proposals.empty()
    asyncio.run(check())


def test_provider_failure_is_sanitized_and_session_remains_usable():
    class FailingProvider(ControlledProvider):
        async def prepare(self, request):
            raise ValueError("secret: SUPER_PRIVATE_TOKEN")

    async def check():
        async with session(FailingProvider()) as live:
            await live.send("user_text", text="question")
            error = await live.until("error")
            assert error.code == "provider_failed"
            assert "SUPER_PRIVATE" not in error.model_dump_json()
            assert live.runtime.state.phase == Phase.PAUSED
            await live.ack("cancel")
    asyncio.run(check())


def test_session_snapshot_is_immutable_and_state_writer_is_enforced():
    async def check():
        async with session(ControlledProvider()) as live:
            with pytest.raises(FrozenInstanceError):
                live.runtime.state.version = 99
            with pytest.raises(RuntimeError, match="runtime loop"):
                live.runtime._set(version=99)
    asyncio.run(check())


def test_bounded_output_queue_is_rejected_instead_of_blocking_interrupts():
    async def check():
        runtime = Runtime(ControlledProvider())
        with pytest.raises(ValueError, match="unbounded"):
            await runtime.run(asyncio.Queue(), asyncio.Queue(maxsize=1))
    asyncio.run(check())


def test_external_task_cancellation_cleans_up_session():
    async def check():
        runtime = Runtime(ControlledProvider(block_prepare=True))
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        task = asyncio.create_task(runtime.run(incoming, outgoing))
        await asyncio.wait_for(outgoing.get(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.state.phase == Phase.CLOSED
        assert runtime.state.checkpoint is None
        assert not runtime._workers
    asyncio.run(check())


def test_typing_keeps_output_paused_until_idle_and_resume():
    async def check():
        provider = ControlledProvider()
        async with session(provider) as live:
            await live.ack("typing", active=True)
            await live.ack("user_text", text="flight")
            provider.release(3)
            await live.ack("resume")
            assert live.runtime.state.ducked
            await live.ack("typing", active=False)
            await live.send("resume")
            await live.until("done")
    asyncio.run(check())
