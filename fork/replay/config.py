"""Replay limits; per-action checkpoints have no cadence bypass."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayConfig:
    max_replay_steps: int = 50
    repair_variants: tuple[str, ...] = ("medium",)
    repair_max_wall_s: float = 240
    fuzzy_threshold: float = 0.9
    identical_task_only: bool = True

    def __post_init__(self) -> None:
        if self.repair_variants != ("medium",) or self.identical_task_only is not True:
            raise ValueError(
                "Replay currently supports identical tasks and one medium repair variant"
            )
        if type(self.max_replay_steps) is not int or self.max_replay_steps < 1:
            raise ValueError("Replay step limit must be a positive integer")
        if not math.isfinite(self.repair_max_wall_s) or self.repair_max_wall_s <= 0:
            raise ValueError("Repair wall limit must be finite and positive")
        if (
            not math.isfinite(self.fuzzy_threshold)
            or not 0 <= self.fuzzy_threshold <= 1
        ):
            raise ValueError("Fuzzy threshold must be between zero and one")
        if (
            not self.repair_variants
            or len(set(self.repair_variants)) != len(self.repair_variants)
            or any(
                value not in {"low", "medium", "high", "xhigh", "max"}
                for value in self.repair_variants
            )
        ):
            raise ValueError("Repair variants must be unique supported efforts")
