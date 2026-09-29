"""Local functional replays, independent of the unavailable official evaluator."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .core.agent import ParticipantAgent
from .core.llm.offline import OfflineProvider


async def replay(path: Path, *, on_output: Callable[[dict], None] | None = None) -> dict[str, Any]:
    scenario = json.loads(path.read_text(encoding="utf-8"))
    incoming: asyncio.Queue = asyncio.Queue()
    outgoing: asyncio.Queue = asyncio.Queue()
    agent = ParticipantAgent(incoming, outgoing, provider=OfflineProvider(chunk_delay=0.005))
    await agent.setup()
    runner = asyncio.create_task(agent.run())
    history: list[dict] = []

    async def receive() -> dict:
        item = await asyncio.wait_for(outgoing.get(), timeout=3)
        outgoing.task_done()
        history.append(item)
        if on_output:
            on_output(item)
        return item

    try:
        for step in scenario["steps"]:
            if "send" in step:
                await incoming.put(step["send"])
            elif "wait_for" in step:
                while True:
                    item = await receive()
                    if item["type"] == "error":
                        raise AssertionError(f"Scenario produced {item.get('code')}")
                    if item["type"] == step["wait_for"]:
                        break
            else:
                raise ValueError("scenario steps must contain send or wait_for")
        await incoming.put(None)
        while (await receive())["type"] != "session_ended":
            pass
        await asyncio.wait_for(runner, timeout=3)
        answer = "".join(item["text"] for item in history if item["type"] == "token")
        for expected in scenario.get("expect_text", []):
            if expected not in answer:
                raise AssertionError(f"Missing expected answer text: {expected!r}")
        for kind in scenario.get("expect_events", []):
            if not any(item["type"] == kind for item in history):
                raise AssertionError(f"Missing expected event: {kind}")
        return {"scenario": scenario["name"], "passed": True, "metrics": asdict(agent.runtime.metrics)}
    finally:
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def run_replays(paths: list[Path], *, verbose: bool = False) -> list[dict]:
    def show(output: dict) -> None:
        if output["type"] == "token":
            print(output["text"], end="", flush=True)
        elif output["type"] in {"duck", "cancelled", "turn_resumed", "done", "session_ended"}:
            print(f"\n[{output['type']} v{output['version']}]")

    results = []
    for path in paths:
        results.append(await replay(path, on_output=show if verbose else None))
    return results
