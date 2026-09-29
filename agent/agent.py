"""Submission entry point (submission.yaml: agent.agent:ParticipantAgent).

The agent lives in trail/core; this module only exposes it in the kit layout.
"""

from trail.core.agent import NaiveAgent, ParticipantAgent

from .baseline import BaselineAgent

__all__ = ["ParticipantAgent", "BaselineAgent", "NaiveAgent"]
