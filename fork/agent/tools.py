"""Strict tool dispatch through the per-branch lock and optional approval gate."""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass

from fork import locks
from fork.actions import CheckpointFailure
from fork.agent.log import RunLogger
from fork.agent.policy import ObservationPolicy
from fork.repl_client import ReplError


def tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "strict": True,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS = [
    tool(
        "exec_js",
        "Execute JavaScript in a persistent Playwright REPL. Top-level await works. "
        "Globals: page (active tab), context, browser, console.log, display(base64Png), sleep(ms). "
        "Use globalThis or assignment without let/const for persistent variables. The output includes "
        "the post-action ARIA tree; use page.getByRole/getByLabel. Desktop is 1440x900.",
        {"code": {"type": "string"}},
    ),
    tool(
        "exec_py",
        "Execute persistent Python on the Linux desktop. Globals: pyautogui, time, subprocess, "
        "log, display(PIL_image), xdo, active_window, atspi_tree. Keep PyAutoGUI fail-safe enabled. "
        "Use for native apps; prefer exec_js for Chromium.",
        {"code": {"type": "string"}},
    ),
    tool(
        "observe",
        "Observe without acting. Prefer tree (ARIA for browser, AT-SPI for desktop); "
        "use screenshot or both when visual layout matters.",
        {"mode": {"type": "string", "enum": ["tree", "screenshot", "both"]}},
    ),
]


ACTION_TOOLS = [
    tool(
        "action",
        "Perform exactly one action, with mandatory before/after desktop screenshots and filesystem checkpoints. "
        "arguments is a JSON object encoded as a string. Browser selectors use Playwright CSS or text selectors. "
        "Operations and arguments: goto {url}, click {selector}, fill {selector,text}, press {selector,key}, "
        "select {selector,value}, check/uncheck {selector}, scroll {x,y}, new_tab/close_tab {}, activate_tab {index}; "
        "desktop_click {x,y}, desktop_type {text} (ASCII), desktop_press {key}, desktop_hotkey {keys:[...]}, desktop_scroll {amount}. "
        "No scripts or shell execution. One invocation per click; observe to inspect all results.",
        {"operation": {"type": "string"}, "arguments": {"type": "string"}},
    ),
    TOOLS[-1],
]


def tree_hash(observation: dict) -> str:
    return hashlib.sha256(observation.get("tree", "").encode()).hexdigest()


class ToolExecutor:
    def __init__(
        self,
        branch: str,
        repl,
        policy: ObservationPolicy,
        logger: RunLogger,
        gate=None,
        approval_waiter: Callable | None = None,
        task_prompt: str = "",
        deadline: float | None = None,
        store=None,
        checkpointed=False,
    ):
        self.branch, self.repl, self.policy, self.log = branch, repl, policy, logger
        self.gate, self.approval_waiter, self.task_prompt = (
            gate,
            approval_waiter,
            task_prompt,
        )
        self.deadline = deadline
        self.store = store
        self.coordinator = None
        if checkpointed:
            from fork.actions import ActionCoordinator

            self.coordinator = ActionCoordinator(branch, repl, store, logger)
        self.last_observation: dict = {}
        self.unchanged_count = 0
        self.history: list[dict] = []

    def _observe(self, mode="tree") -> dict:
        return self.repl.call(
            "observe",
            mode=mode,
            max_tree_chars=max(2_000_000, self.policy.max_tree_chars),
        )

    def execute(self, call: dict) -> dict:
        self.log.steps += 1
        step = self.log.steps
        name = call.get("name", "")
        code = ""
        pre = post = self.last_observation
        result: dict = {}
        error = None
        fatal_error = None
        screenshot = None
        local_before = local_after = None
        action_ids = []
        if self.coordinator:
            self.coordinator.ids = []
        verdict = {
            "decision": "allow",
            "category": "none",
            "rationale": "No gate configured",
        }
        start = time.monotonic()
        try:
            args = json.loads(call.get("arguments", "{}"))
            if name not in {
                "exec_js",
                "exec_py",
                "observe",
                "action",
            } or not isinstance(args, dict):
                raise ValueError("Unknown tool or invalid arguments")
            if self.coordinator and name.startswith("exec_"):
                raise ValueError(
                    "Checkpointed runs require individual action calls; arbitrary scripts are disabled"
                )
            if name == "action":
                if not self.coordinator or set(args) != {"operation", "arguments"}:
                    raise ValueError(
                        "Action checkpoint coordinator unavailable or invalid arguments"
                    )
                payload = json.loads(args["arguments"])
                if not isinstance(payload, dict):
                    raise ValueError("Action arguments must be an object")
            required = "mode" if name == "observe" else "code"
            if name != "action" and (
                set(args) != {required} or not isinstance(args[required], str)
            ):
                raise ValueError("Tool arguments do not match the strict schema")
            code = args.get("code", "")
            if len(code.encode()) > 65536:
                raise ValueError("Code exceeds 64 KiB")
            if name == "observe" and args["mode"] not in {"tree", "screenshot", "both"}:
                raise ValueError("Invalid observation mode")
            with locks.branch(self.branch):
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    raise TimeoutError("Run wall cap reached before tool execution")
                pre = self._observe("both")
                local_before = pre.get("screenshot")
                if self.gate is not None:
                    verdict = self.gate.check(
                        self.branch,
                        name,
                        code or json.dumps(args),
                        {
                            "url": pre.get("url"),
                            "title": pre.get("title"),
                            "history": self.history[-3:],
                            "task": self.task_prompt,
                        },
                    )
                    if is_dataclass(verdict):
                        verdict = asdict(verdict)
                    if not isinstance(verdict, dict) or verdict.get("decision") not in {
                        "allow",
                        "deny",
                        "review",
                    }:
                        verdict = {
                            "decision": "deny",
                            "rationale": "Invalid gate verdict",
                        }
                        raise ValueError("Invalid gate verdict")
                    if verdict["decision"] == "review":
                        timeout = (
                            min(120, max(0, self.deadline - time.monotonic()))
                            if self.deadline
                            else 120
                        )
                        approved = (
                            self.approval_waiter(verdict, timeout)
                            if self.approval_waiter
                            else False
                        )
                        if not approved:
                            raise PermissionError(
                                "Rejected by operator or approval timed out"
                            )
                    elif verdict["decision"] == "deny":
                        raise PermissionError(
                            "Denied by policy: " + verdict.get("rationale", "")
                        )
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    raise TimeoutError("Run wall cap reached before approved action")
                if name == "action":
                    self.coordinator.ids = []
                    result = self.coordinator.execute(
                        args["operation"], payload, call.get("call_id")
                    )
                    action_ids = list(self.coordinator.ids)
                    post = self._observe("both")
                elif name.startswith("exec_"):
                    remaining = (
                        max(0.001, self.deadline - time.monotonic())
                        if self.deadline
                        else 30
                    )
                    result = self.repl.call(
                        name,
                        **args,
                        timeout_ms=max(1, min(30000, int(remaining * 1000))),
                    )
                    post = self._observe("both")
                else:
                    # A tree accompanies every model-visible observation, even screenshot-only requests.
                    post = self._observe("both" if args["mode"] != "tree" else "tree")
                    result = {"stdout": "", "value": None}
                local_after = post.get("screenshot")
                unchanged = name.startswith("exec_") and tree_hash(pre) == tree_hash(
                    post
                )
                self.unchanged_count = self.unchanged_count + 1 if unchanged else 0
                explicit = name == "observe" and args["mode"] != "tree"
                if self.policy.screenshot(
                    step=step, error=False, explicit=explicit, unchanged=unchanged
                ):
                    screenshot = post.get("screenshot") or self._observe(
                        "screenshot"
                    ).get("screenshot")
        except (
            CheckpointFailure,
            ReplError,
            ValueError,
            PermissionError,
            TimeoutError,
            RuntimeError,
            OSError,
        ) as exc:
            if isinstance(exc, CheckpointFailure):
                fatal_error = exc
            error = {
                "name": type(exc).__name__,
                "message": str(exc),
                "code": getattr(exc, "code", None),
            }
            # A denied action must not run. Observation is still safe and useful to the model.
            with locks.branch(self.branch):
                try:
                    post = self._observe("both")
                    screenshot = post.get("screenshot")
                except ReplError:
                    post = pre
            if isinstance(exc, ReplError):
                screenshot = screenshot or exc.data.get("last_screenshot")
        if self.coordinator:
            action_ids = list(self.coordinator.ids)
        self.last_observation = post
        local_paths = []
        for index, value in enumerate(
            [local_before, local_after or post.get("screenshot")], start=100
        ):
            if value:
                local_paths.append(self.log.screenshot(value, step, index))
        images = ([screenshot] if screenshot else []) + result.get("images", [])
        paths = [
            self.log.screenshot(value, step, index)
            for index, value in enumerate(images)
        ]
        relative = paths[0] if paths else None
        before_evidence = self.log.observation(pre, step, "before")
        after_evidence = self.log.observation(post, step, "after")
        self.log.write(
            kind="tool",
            step=step,
            tool=name,
            call_id=call.get("call_id"),
            code=code,
            code_sha=hashlib.sha256(code.encode()).hexdigest(),
            gate=verdict.get("decision", "deny"),
            gate_category=verdict.get("category"),
            gate_rationale=verdict.get("rationale"),
            approval_id=verdict.get("approval_id"),
            exec_ms=round((time.monotonic() - start) * 1000),
            error=error,
            stdout_len=len(result.get("stdout", "")),
            url_before=pre.get("url"),
            url_after=post.get("url"),
            tree_sha_before=tree_hash(pre),
            tree_sha_after=tree_hash(post),
            screenshot=relative,
            screenshots=list(dict.fromkeys([*local_paths, *paths])),
            action_ids=action_ids,
            action_count=len(action_ids),
            tree_before=before_evidence,
            tree_after=after_evidence,
            tokens=None,
            cost_usd=None,
        )
        if self.store is not None:
            with self.store.transaction():
                before_node = (
                    self.store.node_for(pre["tree"])[0]
                    if before_evidence["complete"]
                    else None
                )
                after_node = (
                    self.store.node_for(post["tree"])[0]
                    if after_evidence["complete"]
                    else None
                )
                self.store.record_step(
                    self.log.run_id,
                    step,
                    before_node,
                    after_node,
                    "model",
                    name,
                    code,
                    None,
                    round((time.monotonic() - start) * 1000),
                    None,
                    None,
                    None,
                    evidence={
                        "before": before_evidence,
                        "after": after_evidence,
                        "coverage": "action_level" if action_ids else "tool_level",
                        "action_checkpoints": "filesystem_partial"
                        if action_ids
                        else "unavailable",
                        **({"action_ids": action_ids} if action_ids else {}),
                    },
                )
        text = (
            json.dumps(
                {
                    "stdout": result.get("stdout", ""),
                    "value": result.get("value"),
                    "error": error,
                    "state_lost": result.get("state_lost", False),
                },
                default=str,
            )
            + "\n---\n"
            + (
                post.get("tree", "")[: self.policy.max_tree_chars]
                + (
                    "\n(truncated for model output)"
                    if len(post.get("tree", "")) > self.policy.max_tree_chars
                    else ""
                )
            )
        )
        if self.unchanged_count >= 3:
            text += "\nThe page did not change after three consecutive actions. Inspect and adjust your approach."
        output = [{"type": "input_text", "text": text}]
        for screenshot in images:
            output.append(
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64," + screenshot,
                    "detail": "original",
                }
            )
        self.history.append({"tool": name, "url": post.get("url"), "error": error})
        if fatal_error is not None:
            raise fatal_error
        return {
            "type": "function_call_output",
            "call_id": call.get("call_id"),
            "output": output,
        }
