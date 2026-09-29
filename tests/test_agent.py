import asyncio
from pathlib import Path

import pytest

from trail.core.agent import LocalProtocol, ParticipantAgent
from trail.core.llm.offline import OfflineProvider
from trail.core.state import Phase
from trail.replay import replay


def test_adapter_rejects_bad_message_then_processes_next_request():
    async def check():
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        agent = ParticipantAgent(incoming, outgoing, provider=OfflineProvider(chunk_delay=0))
        await agent.setup()
        runner = asyncio.create_task(agent.run())
        await incoming.put({"type": "unrecognized", "text": "SECRET_BODY"})
        await incoming.put({"type": "user_text", "text": "hello"})
        history = []
        async with asyncio.timeout(2):
            while not any(item["type"] == "done" for item in history):
                history.append(await outgoing.get())
        await incoming.put(None)
        await asyncio.wait_for(runner, 2)
        assert any(item.get("code") == "protocol_error" for item in history)
        assert not any("SECRET_BODY" in str(item) for item in history)
        assert agent.runtime.state.phase == Phase.CLOSED
    asyncio.run(check())


def test_encoding_failure_does_not_leave_runtime_or_feeder_running():
    class BrokenProtocol(LocalProtocol):
        def encode(self, output):
            raise ValueError("encoder failure")

    async def check():
        agent = ParticipantAgent(asyncio.Queue(), asyncio.Queue(), protocol=BrokenProtocol())
        with pytest.raises(ValueError, match="encoder failure"):
            await asyncio.wait_for(agent.run(), 2)
        assert agent.runtime.state.phase == Phase.CLOSED
        assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("trail-") and not task.done()]
    asyncio.run(check())


@pytest.mark.parametrize("path", sorted(Path("scenarios").glob("*.json")), ids=lambda p: p.stem)
def test_local_replay(path):
    result = asyncio.run(replay(path))
    assert result["passed"]
    assert result["metrics"]["errors"] == 0
