"""Tests are deterministic: rules only, no model, no speech-to-text, no network."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

os.environ["TRAIL_LLM"] = "none"
os.environ["TRAIL_STT"] = "none"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(path: str | Path) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def run_kit(scenario: dict, agent_cls=None, *, time_scale: float = 4.0):
    """Run one scenario through the kit's own harness; return (trace, score, agent)."""
    from harness.runner import EvaluationHarness
    from harness.scorer import score_scenario
    from trail.core.agent import ParticipantAgent

    cls = agent_cls or ParticipantAgent
    agents = []

    def factory(i, o):
        a = cls(i, o)
        agents.append(a)
        return a

    async def go():
        h = EvaluationHarness(scenario, factory, time_scale=time_scale, verbose=False)
        return await h.run()

    trace = asyncio.run(go())
    return trace, score_scenario(scenario, trace), agents[0]


def actions(trace, kind=None):
    return [e for e in trace if e.get("kind") == "action" and (kind is None or e.get("action") == kind)]


@pytest.fixture
def kit():
    return run_kit
