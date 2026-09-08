"""Budgeted agent loop; model I/O is injected independently of desktop execution."""

import json
import os
import random
import sqlite3
import time
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import httpx

from fork.actions import CheckpointFailure
from fork.agent.judge import JUDGE_TOOL, JudgePool
from fork.agent.log import RunLogger
from fork.agent.policy import Caps, ObservationPolicy
from fork.agent.prompts import ACTION_SYSTEM, SYSTEM
from fork.agent.steer import SteerChannel
from fork.agent.tools import ACTION_TOOLS, TOOLS, ToolExecutor
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
    if task.get("osworld_task") == "008":
        from fork.osworld008 import check

        return check(branch)
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
    cancel=None,
    extra_instructions: str = "",
    judge: bool = False,
    judge_runner=None,
    store=None,
    resume: bool = False,
) -> dict:
    validate_name(branch)
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Invalid reasoning effort")
    if os.environ.get("FORK_MODEL", "gpt-6-astra") != "gpt-6-astra":
        raise ValueError("Cost accounting supports gpt-6-astra only")
    caps, policy = caps or Caps(), policy or ObservationPolicy()
    tx = transport or WsTransport()
    log = RunLogger(
        branch, task["id"], effort, root=runs_dir, backend=tx.name, seed=seed
    )
    deadline = log.started + caps.max_wall_s
    container_id = None
    owns_repl = repl is None
    checkpointed = owns_repl and repl_url is None
    model_tools = ACTION_TOOLS if checkpointed else TOOLS
    text, error = "", None
    reason = "error"
    executor = None
    channel = SteerChannel(branch)
    steering_history: list[str] = []
    queued_steers: list[str] = []
    accounted: set[str] = set()
    judges = JudgePool(log, judge_runner) if judge else None

    def collect_steers(active=None):
        for item in channel.read():
            steering_history.append(item["text"])
            if isinstance(tx, WsTransport) and active:
                tx.steer(active, item["text"])
                ack = "sent"
            else:
                queued_steers.append(item["text"])
                ack = "queued"
            log.write(
                kind="steer",
                step=None,
                text=item["text"],
                ack=ack,
                previous_response_id=active,
            )
        if isinstance(tx, WsTransport):
            queued_steers.extend(tx.take_fallback())

    def steer_input() -> list[dict]:
        collect_steers()
        result = [{"role": "user", "content": text} for text in queued_steers]
        queued_steers.clear()
        return result

    if isinstance(tx, WsTransport):
        tx.poll = lambda active: collect_steers(active) if active else None
        tx.on_event = lambda event: log.write(
            kind="steer",
            step=None,
            ack=event["type"],
            steer_id=(event.get("steer") or {}).get("id"),
            code=(event.get("error") or {}).get("code"),
        )

    def budget():
        if cancel is not None and cancel.is_set():
            raise StopRun("raced_out")
        if time.monotonic() >= deadline:
            raise StopRun("wall_cap")
        if log.cost_usd + (judges.reserved_usd if judges else 0) >= caps.max_cost_usd:
            raise StopRun("cost_cap")
        if log.turns >= caps.max_turns:
            raise StopRun("turn_cap")
        if container_id is not None:
            current = db.get_branch(branch, include_removed=True)
            if current.container_id != container_id or current.status != "healthy":
                raise StopRun("rewound")

    if isinstance(tx, WsTransport):
        tx.before_successor = budget

    def steering_context() -> str:
        return (
            "\nOperator updates received (may already be applied; inspect the current UI "
            "and completed actions before doing anything again): "
            + json.dumps(steering_history)
        )

    def request(body: dict, purpose="task") -> dict:
        for attempt in range(7):
            budget()
            start = time.monotonic()
            log.model_calls += 1

            def account(response, started=start):
                if response["id"] in accounted:
                    return
                accounted.add(response["id"])
                log.model(
                    response,
                    round((time.monotonic() - started) * 1000),
                    body["reasoning"]["effort"],
                    purpose=purpose,
                )
                if progress:
                    progress(log.cost_usd)

            if isinstance(tx, WsTransport):
                tx.on_response = account
            before = len(accounted)
            delivery_turn = log.turns + 1
            try:
                response = tx.create(
                    timeout_s=max(0.001, deadline - time.monotonic()), **body
                )
                account(response)
                if judges:
                    judges.delivered(body["input"], delivery_turn)
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
            finally:
                log.model_calls += max(0, len(accounted) - before - 1)
        raise AssertionError("Retry loop exhausted")

    def body(previous, inp, *, summarize=False) -> dict:
        result = {
            "model": "gpt-6-astra",
            "stream_id": branch,
            "store": False,
            "instructions": (ACTION_SYSTEM if checkpointed else SYSTEM)
            + ("\n" + extra_instructions if extra_instructions else ""),
            "tools": []
            if summarize
            else [*model_tools, *([JUDGE_TOOL] if judge else [])],
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
        with channel, tx, judges if judges is not None else nullcontext():
            if repl is None:
                if repl_url is None:
                    state = db.get_branch(branch)
                    container_id = state.container_id
                    repl_url = f"http://localhost:{state.ports['repl']}"
                repl = ReplClient(repl_url, timeout_s=min(60, caps.max_wall_s))
            budget()
            if checkpointed and repl.health().get("action_protocol") != 1:
                raise RuntimeError(
                    "This desktop predates action checkpoints. Create a branch from an updated runtime image before running the agent."
                )
            (log.path / "task.json").write_text(json.dumps(task, indent=2) + "\n")
            if store is not False:
                from fork.store.db import Store

                store = store or Store(artifact_root=log.path.parent)
                store.record_run(log.run_id, task["id"], branch, "cold", effort)
            executor = ToolExecutor(
                branch,
                repl,
                policy,
                log,
                gate,
                approval_waiter,
                task["prompt"],
                deadline,
                store=store if store is not False else None,
                checkpointed=checkpointed,
            )
            initial = executor.execute(
                {
                    "name": "observe",
                    "call_id": "resume",
                    "arguments": '{"mode":"both"}',
                }
                if resume
                else {
                    "name": "action" if checkpointed else "exec_js",
                    "call_id": "bootstrap",
                    "arguments": json.dumps(
                        {
                            "operation": "goto",
                            "arguments": json.dumps({"url": task["start_url"]}),
                        }
                        if checkpointed
                        else {
                            "code": f"await page.goto({json.dumps(task['start_url'])})"
                        }
                    ),
                }
            )
            log.write(kind="lifecycle", step=None, mode="resume" if resume else "start")
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
                if judges:
                    inp.extend(judges.drain())
                budget()
                inp.extend(steer_input())
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
                    if judges:
                        judges.reset()
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
                                    + json.dumps(executor.history[-6:])
                                    + steering_context(),
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
                if cancel is not None and cancel.is_set():
                    raise StopRun("raced_out")
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
                    additions = steer_input()
                    if judges:
                        while judges.jobs:
                            if time.monotonic() >= deadline:
                                raise StopRun("wall_cap")
                            if cancel is not None and cancel.is_set():
                                raise StopRun("raced_out")
                            judges.wait(timeout=min(0.25, deadline - time.monotonic()))
                            additions.extend(judges.drain())
                            additions.extend(steer_input())
                    if additions:
                        inp = additions
                        continue
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
                    if cancel is not None and cancel.is_set():
                        raise StopRun("raced_out")
                    if container_id is not None:
                        current = db.get_branch(branch, include_removed=True)
                        if (
                            current.container_id != container_id
                            or current.status != "healthy"
                        ):
                            raise StopRun("rewound")
                    if time.monotonic() >= deadline:
                        raise StopRun("wall_cap")
                    if call.get("name") == "judge" and judges:
                        observed = executor.execute(
                            {
                                "name": "observe",
                                "call_id": "judge-snapshot-" + call["call_id"],
                                "arguments": '{"mode":"both"}',
                            }
                        )
                        if not judges.submit(
                            call,
                            task,
                            observed["output"],
                            max(0.001, deadline - time.monotonic()),
                            available_usd=caps.max_cost_usd - log.cost_usd,
                        ):
                            inp.append(
                                {
                                    "type": "function_call_output",
                                    "call_id": call["call_id"],
                                    "output": json.dumps(
                                        {
                                            "score": 0,
                                            "verdict": "fail",
                                            "missing": [
                                                "Judge capacity or budget exhausted"
                                            ],
                                        }
                                    ),
                                }
                            )
                        continue
                    if call.get("async"):
                        raise TransportError(
                            "Only the judge tool supports asynchronous execution"
                        )
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
                                    + final_text(summarized)
                                    + steering_context(),
                                },
                                *observed["output"],
                            ],
                        }
                    ]
                    previous = None
                    if judges:
                        judges.reset()
                    log.write(
                        kind="tool",
                        step=None,
                        tool="chain_reset",
                        reason="context_soft_limit",
                    )
    except StopRun as exc:
        reason = exc.reason
    except (
        CheckpointFailure,
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        sqlite3.Error,
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
    summary = log.finish(reason, text=text, checker=result, error=error)
    if store not in (None, False):
        from fork.store.ingest import ingest_run

        try:
            ingest_run(store, log.path)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            summary["store_error"] = f"{type(exc).__name__}: {exc}"
            (log.path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
