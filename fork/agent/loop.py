"""Budgeted agent loop; model I/O is injected independently of desktop execution."""

import json
import os
import random
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from fork.agent.log import RunLogger
from fork.agent.policy import Caps, ObservationPolicy
from fork.agent.prompts import SYSTEM
from fork.agent.tools import TOOLS, ToolExecutor
from fork.agent.transport import TransportError, WallTimeout, WsTransport
from fork.cu import db
from fork.locks import validate_name
from fork.repl_client import ReplClient


class StopRun(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def load_task(path: Path, branch: str) -> dict:
    validate_name(branch)
    raw = json.loads(path.read_text())

    def replace(value):
        if isinstance(value, str):
            return value.replace("{{tenant}}", branch)
        if isinstance(value, dict):
            return {key: replace(v) for key, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    task = replace(raw)
    for field in ("id", "prompt", "start_url"):
        if not isinstance(task.get(field), str) or not task[field]:
            raise ValueError(f"Task requires {field}")
    return task


def check_task(task: dict, branch: str, base="http://localhost:3000") -> dict:
    if "expected" not in task:
        return {"pass": None, "errors": []}
    try:
        response = httpx.get(f"{base}/t/{branch}/state.json", timeout=5)
        response.raise_for_status()
        got = response.json()
        exp = task["expected"]
        errors = [
            f"{key}: want {value!r} got {got.get('fields', {}).get(key)!r}"
            for key, value in exp.get("fields", {}).items()
            if got.get("fields", {}).get(key) != value
        ]
        for key in ("rows", "submitted"):
            if key in exp and got.get(key) != exp[key]:
                errors.append(f"{key}: want {exp[key]!r} got {got.get(key)!r}")
        return {"pass": not errors, "errors": errors}
    except (httpx.HTTPError, ValueError) as exc:
        return {"pass": False, "errors": [f"Checker unavailable: {type(exc).__name__}"]}


def final_text(response: dict) -> str:
    return "\n".join(
        part.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def run_task(
    branch: str,
    task: dict,
    effort="low",
    caps: Caps | None = None,
    policy: ObservationPolicy | None = None,
    transport=None,
    gate=None,
    hooks=None,
    repl=None,
    repl_url: str | None = None,
    runs_dir: Path | None = None,
    checker: Callable | None = None,
    progress: Callable | None = None,
    seed: int | None = None,
    approval_waiter=None,
    sleep: Callable = time.sleep,
) -> dict:
    validate_name(branch)
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Invalid reasoning effort")
    if os.environ.get("FORK_MODEL", "gpt-6-astra") != "gpt-6-astra":
        raise ValueError("Phase 06 accounting supports gpt-6-astra only")
    caps, policy = caps or Caps(), policy or ObservationPolicy()
    tx = transport or WsTransport()
    log = RunLogger(
        branch, task["id"], effort, root=runs_dir, backend=tx.name, seed=seed
    )
    deadline = log.started + caps.max_wall_s
    container_id = None
    owns_repl = repl is None
    text, error = "", None
    reason = "error"
    executor = None

    def budget():
        if time.monotonic() >= deadline:
            raise StopRun("wall_cap")
        if log.cost_usd >= caps.max_cost_usd:
            raise StopRun("cost_cap")
        if log.turns >= caps.max_turns:
            raise StopRun("turn_cap")
        if container_id is not None:
            current = db.get_branch(branch, include_removed=True)
            if current.container_id != container_id or current.status != "healthy":
                raise StopRun("rewound")

    def request(body: dict, purpose="task") -> dict:
        for attempt in range(7):
            budget()
            start = time.monotonic()
            log.model_calls += 1
            try:
                response = tx.create(
                    timeout_s=max(0.001, deadline - time.monotonic()), **body
                )
                log.model(
                    response,
                    round((time.monotonic() - start) * 1000),
                    body["reasoning"]["effort"],
                    purpose=purpose,
                )
                if progress:
                    progress(log.cost_usd)
                return response
            except WallTimeout:
                raise StopRun("wall_cap") from None
            except TransportError as exc:
                if time.monotonic() >= deadline:
                    raise StopRun("wall_cap") from exc
                if exc.code == "misalignment_policy_violation":
                    raise StopRun("misalignment_block") from exc
                if exc.code in {"previous_response_not_found", "connection_lost"}:
                    raise
                if exc.status not in {429, 503} or attempt == 6:
                    raise
                delay = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else min(60, 2**attempt + random.random())
                )
                if delay >= deadline - time.monotonic():
                    raise StopRun("wall_cap") from exc
                log.write(
                    kind="tool",
                    step=None,
                    tool="retry",
                    code=exc.code,
                    attempt=attempt + 1,
                    delay_s=delay,
                )
                sleep(max(0, delay))
        raise AssertionError("Retry loop exhausted")

    def body(previous, inp, *, summarize=False) -> dict:
        result = {
            "model": "gpt-6-astra",
            "stream_id": branch,
            "store": False,
            "instructions": SYSTEM,
            "tools": [] if summarize else TOOLS,
            "tool_choice": "none" if summarize else "auto",
            "parallel_tool_calls": False,
            "reasoning": {"effort": "low" if summarize else effort, "summary": "auto"},
            "max_output_tokens": 1000 if summarize else 16000,
            "input": inp,
        }
        if previous:
            result["previous_response_id"] = previous
        return result

    try:
        # Entering an injected transport never requires a real key. Live clients fail here before UI mutation.
        with tx:
            if repl is None:
                if repl_url is None:
                    state = db.get_branch(branch)
                    container_id = state.container_id
                    repl_url = f"http://localhost:{state.ports['repl']}"
                repl = ReplClient(repl_url, timeout_s=min(60, caps.max_wall_s))
            budget()
            executor = ToolExecutor(
                branch,
                repl,
                policy,
                log,
                gate,
                approval_waiter,
                task["prompt"],
                deadline,
            )
            initial = executor.execute(
                {
                    "name": "exec_js",
                    "call_id": "bootstrap",
                    "arguments": json.dumps(
                        {"code": f"await page.goto({json.dumps(task['start_url'])})"}
                    ),
                }
            )
            if hooks:
                hooks.on_run_start(branch)
            inp = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": task["prompt"]},
                        *initial["output"],
                    ],
                }
            ]
            previous = None
            reset_count = 0
            while True:
                budget()
                try:
                    response = request(body(previous, inp))
                except TransportError as exc:
                    if (
                        exc.code
                        not in {"previous_response_not_found", "connection_lost"}
                        or reset_count >= 3
                    ):
                        raise
                    tx.reset()
                    previous = None
                    reset_count += 1
                    log.write(
                        kind="tool", step=None, tool="chain_reset", reason=exc.code
                    )
                    # No old call ids are reused after reconnect. The current UI is authoritative.
                    inp = [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": task["prompt"]
                                    + "\nConnection reset. Re-observe before acting. Recent completed tools: "
                                    + json.dumps(executor.history[-6:]),
                                }
                            ],
                        }
                    ]
                    observed = executor.execute(
                        {
                            "name": "observe",
                            "call_id": f"reconnect-{reset_count}",
                            "arguments": '{"mode":"both"}',
                        }
                    )
                    inp[0]["content"].extend(observed["output"])
                    continue
                previous = response["id"]
                output = response.get("output", [])
                if any(
                    part.get("type") == "refusal"
                    for item in output
                    if item.get("type") == "message"
                    for part in item.get("content", [])
                ):
                    raise StopRun("model_refusal")
                if response.get("status") == "failed":
                    raise TransportError("Model returned failed response")
                calls = [item for item in output if item.get("type") == "function_call"]
                messages = [item for item in output if item.get("type") == "message"]
                if not calls and any(
                    item.get("phase") in {None, "final_answer"} for item in messages
                ):
                    text = final_text(response)
                    if time.monotonic() >= deadline:
                        raise StopRun("wall_cap")
                    if log.cost_usd >= caps.max_cost_usd:
                        raise StopRun("cost_cap")
                    if response.get("status") == "incomplete":
                        raise TransportError(
                            "Incomplete response cannot count as task completion"
                        )
                    reason = "final_answer"
                    break
                # Stop before any emitted action if a cap was reached. Do not drop pending calls for a reset.
                if time.monotonic() >= deadline:
                    raise StopRun("wall_cap")
                if log.cost_usd >= caps.max_cost_usd:
                    raise StopRun("cost_cap")
                inp = []
                for call in calls:
                    if call.get("async"):
                        raise TransportError("Asynchronous tools are not supported")
                    if container_id is not None:
                        current = db.get_branch(branch, include_removed=True)
                        if (
                            current.container_id != container_id
                            or current.status != "healthy"
                        ):
                            raise StopRun("rewound")
                    if time.monotonic() >= deadline:
                        raise StopRun("wall_cap")
                    inp.append(executor.execute(call))
                if not calls:
                    if not messages:
                        raise TransportError(
                            "Response had neither a tool call nor a message"
                        )
                    inp = [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Continue the task or provide your verified final answer.",
                                }
                            ],
                        }
                    ]
                if (
                    response.get("usage", {}).get("input_tokens", 0)
                    > policy.context_soft_limit
                ):
                    summary_in = [
                        *inp,
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Summarize progress, constraints, completed actions and next steps in at most 300 words. Do not act.",
                                }
                            ],
                        },
                    ]
                    summarized = request(
                        body(previous, summary_in, summarize=True),
                        purpose="chain_summary",
                    )
                    budget()
                    observed = executor.execute(
                        {
                            "name": "observe",
                            "call_id": "chain-reset",
                            "arguments": '{"mode":"both"}',
                        }
                    )
                    inp = [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": task["prompt"]
                                    + "\nProgress summary:\n"
                                    + final_text(summarized),
                                },
                                *observed["output"],
                            ],
                        }
                    ]
                    previous = None
                    log.write(
                        kind="tool",
                        step=None,
                        tool="chain_reset",
                        reason="context_soft_limit",
                    )
    except StopRun as exc:
        reason = exc.reason
    except (
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        error = f"{type(exc).__name__}: {exc}"
        reason = "error"
    finally:
        if owns_repl and repl is not None:
            repl.close()
    try:
        result = checker(task, branch) if checker else check_task(task, branch)
    except Exception as exc:  # noqa: BLE001 - persist summary when an injected checker fails
        result = {"pass": False, "errors": [f"Checker failed: {type(exc).__name__}"]}
    return log.finish(reason, text=text, checker=result, error=error)
