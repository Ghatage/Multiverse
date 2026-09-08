"""Observation and run limits shared by live and offline transports."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Caps:
    max_turns: int = 30
    max_wall_s: float = 600
    max_cost_usd: float = 5

    def __post_init__(self):
        if (
            type(self.max_turns) is not int
            or self.max_turns < 0
            or not math.isfinite(self.max_wall_s)
            or self.max_wall_s <= 0
            or not math.isfinite(self.max_cost_usd)
            or self.max_cost_usd <= 0
        ):
            raise ValueError("Invalid run caps")


@dataclass(frozen=True)
class ObservationPolicy:
    shots_first_n: int = 2
    max_tree_chars: int = 20_000
    context_soft_limit: int = 200_000

    def __post_init__(self):
        if (
            self.shots_first_n < 0
            or self.max_tree_chars < 64
            or self.context_soft_limit <= 0
        ):
            raise ValueError("Invalid observation policy")

    def screenshot(
        self, *, step: int, error: bool, explicit: bool, unchanged: bool
    ) -> bool:
        return step <= self.shots_first_n or error or explicit or unchanged
