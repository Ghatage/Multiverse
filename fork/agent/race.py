"""Race independent desktops from one checkpoint; only verification can pick a winner."""

import copy
import math
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fork.agent.loop import check_task, load_task, run_task
from fork.agent.policy import Caps
from fork.cu import branch as branches
from fork.cu import db
from fork.locks import validate_name


def _task(value: dict, name: str) -> dict:
    def replace(item):
        if isinstance(item, str):
            return item.replace("{{tenant}}", name)
        if isinstance(item, list):
            return [replace(v) for v in item]
        if isinstance(item, dict):
            return {k: replace(v) for k, v in item.items()}
        return item

    result = replace(copy.deepcopy(value))
    if any(
        not isinstance(result.get(k), str) or not result[k]
        for k in ("id", "prompt", "start_url")
    ):
        raise ValueError("Task requires id, prompt, and start_url")
    return result


def race(
    checkpoint: str,
    variants: list[dict],
    task: dict | Path,
    verify=None,
    *,
    stagger_s: float = 5,
    max_wall_s: float = 300,
    max_cost_usd: float = 2,
    max_turns: int = 30,
    judge: bool = False,
) -> dict:
    Caps(max_turns, max_wall_s, max_cost_usd)
    if not math.isfinite(stagger_s) or stagger_s < 0:
        raise ValueError("Stagger must be finite and nonnegative")
    width = int(os.environ.get("FORK_MAX_BRANCHES", "2"))
    if not 1 <= width <= 8 or not 1 <= len(variants) <= 8:
        raise ValueError("Race width and variant count must be between 1 and 8")
    prefix = "race-" + secrets.token_hex(3)
    names = [f"{prefix}-{v['name']}" for v in variants]
    for name, variant in zip(names, variants):
        validate_name(name)
        if variant.get("effort") not in branches.EFFORTS:
            raise ValueError("Invalid variant effort")
        if not isinstance(variant.get("hint", ""), str):
            raise ValueError("Variant hint must be text")  # noqa: TRY004 - public validation contract
    if len(set(names)) != len(names):
        raise ValueError("Variant names must be unique")
    definitions = [
        load_task(task, name) if isinstance(task, Path) else _task(task, name)
        for name in names
    ]
    db.get_checkpoint(checkpoint)
    started = time.monotonic()
    deadline = started + max_wall_s
    cancel = threading.Event()
    winner_lock = threading.Lock()
    winner = None

    def worker(index: int) -> dict:
        nonlocal winner
        name, variant, definition = names[index], variants[index], definitions[index]
        base = {
            "branch": name,
            "cost_usd": 0.0,
            "checker": {"pass": None, "errors": []},
        }
        delay = min(
            max(0, started + index * stagger_s - time.monotonic()),
            max(0, deadline - time.monotonic()),
        )
        if cancel.wait(delay):
            return {**base, "stop_reason": "raced_out"}
        if time.monotonic() >= deadline:
            return {**base, "stop_reason": "wall_cap"}
        try:
            branches.create(name, source=checkpoint, effort=variant["effort"])
            remaining = deadline - time.monotonic()
            if cancel.is_set() or remaining <= 0:
                return {
                    **base,
                    "stop_reason": "raced_out" if cancel.is_set() else "wall_cap",
                }
            result = run_task(
                name,
                definition,
                effort=variant["effort"],
                extra_instructions=variant.get("hint", ""),
                judge=judge,
                caps=Caps(max_turns, remaining, max_cost_usd / len(variants)),
                cancel=cancel,
            )
            if result["stop_reason"] == "final_answer":
                try:
                    checked = verify(name) if verify else check_task(definition, name)
                    passed = (
                        checked.get("pass") is True
                        if isinstance(checked, dict)
                        else checked is True
                    )
                    result["race_verified"] = passed
                except Exception as exc:  # noqa: BLE001 - verifier failures cannot win
                    passed = False
                    result["verification_error"] = type(exc).__name__
                with winner_lock:
                    if passed and winner is None and time.monotonic() < deadline:
                        winner = name
                        cancel.set()
            return result
        except (ValueError, RuntimeError, OSError) as exc:
            # A model call could have failed before returning usage. Never claim zero cost for it.
            return {
                **base,
                "stop_reason": "error",
                "cost_usd": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    with ThreadPoolExecutor(
        max_workers=min(width, len(variants)), thread_name_prefix="fork-race"
    ) as pool:
        futures = [pool.submit(worker, i) for i in range(len(variants))]
        results = [future.result() for future in futures]
    return {
        "winner": winner,
        "checkpoint": checkpoint,
        "elapsed_s": round(time.monotonic() - started, 3),
        "runs": results,
        "cost_usd": sum(r["cost_usd"] or 0 for r in results),
        "usage_complete": all(
            r["cost_usd"] is not None and not r.get("error") for r in results
        ),
    }
