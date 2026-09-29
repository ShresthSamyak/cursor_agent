"""Runtime switches. Ablation variants flip these one at a time (PDF p. 17-18)."""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Features:
    """Each flag is one rung of the ablation ladder.

    naive: every user input cancels everything and restarts from that input
    alone. The other flags only matter when naive is False.
    """

    naive: bool = False
    goal_stack: bool = True          # checkpoints, park/resume, slot patching
    classifier: bool = True          # seven-way interrupt classifier, duck-then-decide
    forks: bool = True               # speculative work on partial input
    saga: bool = True                # tool tags, rollback/compensation, commit barrier

    @classmethod
    def ladder(cls) -> list[tuple[str, "Features"]]:
        return [
            ("1 naive restart", cls(naive=True, goal_stack=False, classifier=False, forks=False, saga=False)),
            ("2 +checkpoints/goals", cls(goal_stack=True, classifier=False, forks=False, saga=False)),
            ("3 +classifier", cls(goal_stack=True, classifier=True, forks=False, saga=False)),
            ("4 +forks", cls(goal_stack=True, classifier=True, forks=True, saga=False)),
            ("5 +saga", cls()),
        ]


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "harness"                    # "harness" or "desktop"
    features: Features = field(default_factory=Features)
    # Arbiter: the kit penalises fillers beyond 4 (3 in some scenarios) and
    # verbatim repeats, so the agent keeps its own budget below both.
    filler_budget: int = 3
    # Virtual ms after a user turn ends by which *something* real is said.
    speak_by_ms: float = 450.0
    # Read-only retries after an error result.
    read_only_retries: int = 1
    # Speculative forks may run read-only tools only in desktop mode: in the
    # harness an unrequested call can fail a tool_not_called checkpoint.
    speculative_tools: bool = False
    max_forks: int = 3
    fork_token_budget: int = 1500
    # How long to wait for vision/STT/LLM before degrading (virtual ms).
    media_timeout_ms: float = 6000.0
    llm_timeout_ms: float = 4500.0
    # Session trail.
    max_trail: int = 256
    trail_tau_s: float = 600.0
    dwell_threshold_ms: float = 350.0
    # Agent-initiated interruptions per 10 minutes (desktop).
    unsolicited_budget: int = 6
    stream_chunk_delay_s: float = 0.0        # desktop pacing for streamed answers

    def with_features(self, features: Features) -> "RuntimeConfig":
        return replace(self, features=features)
