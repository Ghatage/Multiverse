"""Estimated Astra USD accounting: input $10/M, cached $1/M, output $50/M."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tokens:
    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    cache_write: int = 0

    @classmethod
    def from_usage(cls, usage: dict[str, Any] | None) -> "Tokens":
        if not usage:
            raise ValueError(
                "Response is missing token usage; cost cannot be accounted"
            )
        inp = usage.get("input_tokens_details") or {}
        out = usage.get("output_tokens_details") or {}
        t = cls(
            usage["input_tokens"],
            inp.get("cached_tokens", 0),
            usage["output_tokens"],
            out.get("reasoning_tokens", 0),
            inp.get("cache_write_tokens", 0),
        )
        if any(type(v) is not int or v < 0 for v in t.__dict__.values()):
            raise ValueError("Invalid token counters")
        if t.cached + t.cache_write > t.input or t.reasoning > t.output:
            raise ValueError("Token detail exceeds its total")
        return t


def estimate_usd(tokens: Tokens) -> float:
    input_scale, output_scale = (2, 1.5) if tokens.input > 272_000 else (1, 1)
    return (
        (tokens.input - tokens.cached - tokens.cache_write) * 10 * input_scale
        + tokens.cached * input_scale
        + tokens.cache_write * 12.5 * input_scale
        + tokens.output * 50 * output_scale
    ) / 1_000_000
