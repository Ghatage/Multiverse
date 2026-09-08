"""Flush one JSONL record per model response and tool action; screenshots are files."""

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fork.agent.cost import Tokens, estimate_usd


def new_run_id(seed: int | None = None) -> str:
    return f"r_{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


class RunLogger:
    def __init__(
        self,
        branch: str,
        task_id: str,
        effort: str,
        *,
        root: Path | None = None,
        backend: str = "ws",
        seed: int | None = None,
    ):
        self.run_id = new_run_id(seed)
        self.path = (
            root or Path(os.environ.get("FORK_RUNS_DIR", "runs"))
        ) / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "shots").mkdir()
        self.branch, self.task_id, self.effort, self.backend, self.seed = (
            branch,
            task_id,
            effort,
            backend,
            seed,
        )
        self.turns = self.steps = self.model_calls = 0
        self.tokens = {
            "input": 0,
            "cached": 0,
            "output": 0,
            "reasoning": 0,
            "cache_write": 0,
        }
        self.cost_usd = 0.0
        self.started = time.monotonic()
        self.file = (self.path / "steps.jsonl").open("w")

    def write(self, **record) -> None:
        common = {
            "run_id": self.run_id,
            "branch": self.branch,
            "turn": self.turns,
            "ts": datetime.now(UTC).isoformat(),
        }
        self.file.write(json.dumps({**common, **record}, allow_nan=False) + "\n")
        self.file.flush()

    def model(
        self, response: dict, latency_ms: int, effort: str, *, purpose="task"
    ) -> None:
        self.turns += 1
        tokens = Tokens.from_usage(response.get("usage"))
        cost = estimate_usd(tokens)
        self.cost_usd += cost
        for k, v in asdict(tokens).items():
            self.tokens[k] += v
        self.write(
            kind="model",
            step=None,
            response_id=response["id"],
            latency_ms=latency_ms,
            tokens=asdict(tokens),
            cost_usd=cost,
            cum_cost_usd=self.cost_usd,
            stop=response.get("incomplete_details", {}).get("reason")
            if response.get("incomplete_details")
            else None,
            effort=effort,
            purpose=purpose,
            backend=self.backend,
        )

    def observation(self, value: dict, step: int, side: str) -> dict:
        relative = f"observations/{step:03d}-{side}.txt"
        path = self.path / relative
        path.parent.mkdir(exist_ok=True)
        content = value.get("tree", "").encode()
        with path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return {
            "path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "complete": bool(content)
            and value.get("target") != "desktop"
            and "(truncated " not in value.get("tree", ""),
            "scope": "tool_observation",
        }

    def screenshot(self, b64: str, step: int, index: int = 0) -> str:
        data = base64.b64decode(b64, validate=True)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("REPL returned a non-PNG screenshot")
        suffix = f"-{index}" if index else ""
        relative = f"shots/{step:03d}{suffix}.png"
        (self.path / relative).write_bytes(data)
        return relative

    def finish(
        self, reason: str, *, text="", checker=None, error=None, extra=None
    ) -> dict:
        summary = {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "branch": self.branch,
            "effort": self.effort,
            "stop_reason": reason,
            "turns": self.turns,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "wall_s": round(time.monotonic() - self.started, 3),
            "checker": checker or {"pass": None, "errors": []},
            "final_text": text,
            "cache": {"hits": 0, "misses": 0},
            "backend": self.backend,
            "usage_source": "scripted" if self.backend == "scripted" else "api",
            "seed": self.seed,
            "error": error,
        }
        summary.update(extra or {})
        (self.path / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n"
        )
        self.file.close()
        return summary
