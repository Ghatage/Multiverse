"""Model tools for individually checkpointed execution."""

import hashlib
import json
import time

from fork.agent.tools import TOOLS, tool
from fork.mutations.actions import OPERATIONS
from fork.mutations.coordinator import BoundaryError
from fork.repl_client import ReplError

COORDINATE = {"type": "number", "minimum": 0, "exclusiveMaximum": 16384}
FIELD_SCHEMAS = {
    "checked": {"type": "boolean"},
    "x": COORDINATE,
    "y": COORDINATE,
    "points": {
        "type": "array",
        "minItems": 2,
        "maxItems": 256,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y"],
            "properties": {"x": COORDINATE, "y": COORDINATE},
        },
    },
}

ACTION_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", *fields],
            "properties": {
                "operation": {"type": "string", "enum": [operation]},
                **{
                    field: FIELD_SCHEMAS.get(field, {"type": "string"})
                    for field in fields
                },
            },
        }
        for operation, fields in sorted(OPERATIONS.items())
    ]
}
TOOLS_CHECKPOINTED = [
    tool(
        "act",
        "Execute an ordered list of individual actions. Supported operations: navigate(url), new_tab(url), fill(selector,value), click(selector), select(selector,value), check(selector,checked), write_file(path,content), pointer_click(x,y), pointer_drag(points:[{x,y},...]), press_key(key). Pointer coordinates are CSS pixels relative to the browser viewport screenshot, NOT the full desktop. A drag holds the left mouse button along 2-256 points and releases it; use two points for a straight drag or shape bounding box. press_key accepts Playwright chords such as Control+z. new_tab preserves existing tabs and brings the new page forward. Selectors use Playwright CSS, text=, or role= syntax. Each action is checkpointed before the next starts. No arbitrary JavaScript/Python is available.",
        {
            "actions": {
                "type": "array",
                "items": ACTION_SCHEMA,
                "minItems": 1,
                "maxItems": 50,
            }
        },
    ),
    TOOLS[2],
]


class Executor:
    def __init__(self, coordinator, log, store, policy):
        self.coordinator, self.log, self.store, self.policy = (
            coordinator,
            log,
            store,
            policy,
        )
        self.history = []
        self.last_observation = {}
        self.source = "model"
        self.last_actions = []
        self.candidates = []

    @property
    def repl(self):
        return self.coordinator.backend.repl

    def execute(self, call: dict) -> dict:
        self.log.steps += 1
        step = self.log.steps
        started = time.monotonic()
        name = call.get("name")
        args = json.loads(call.get("arguments", "{}"))
        pre = self.repl.call(
            "observe", mode="tree", target="browser", max_tree_chars=2_000_000
        )
        code = json.dumps(args.get("actions", []), sort_keys=True)
        error = None
        fatal = None
        count = 0
        self.last_actions = []
        try:
            if name == "act" and set(args) == {"actions"}:
                count = len(args["actions"])
                self.last_actions = self.coordinator.execute(
                    call["call_id"], args["actions"]
                )
            elif (
                name == "observe"
                and set(args) == {"mode"}
                and args["mode"] in {"tree", "screenshot", "both"}
            ):
                pass
            else:
                raise ValueError(
                    "Use act with structured actions or observe; arbitrary code is unavailable"
                )
        except (ValueError, ReplError, BoundaryError) as exc:
            error = {"name": type(exc).__name__, "message": str(exc)}
            if self.coordinator.failed:
                fatal = exc
            self.last_actions = [
                {"action_id": action["id"]}
                for action in self.store.actions_for(self.log.run_id)
                if action["call_id"] == call["call_id"]
                and action["checkpoint_status"] == "committed"
                and action["outcome"] in {"succeeded", "no_op"}
            ]
        try:
            post = self.repl.call(
                "observe", mode="both", target="browser", max_tree_chars=2_000_000
            )
        except ReplError as exc:
            self.last_observation = {}
            self.coordinator.failed = True
            self.log.write(
                kind="tool",
                step=step,
                tool=name,
                call_id=call["call_id"],
                code=code,
                source=self.source,
                action_count=count,
                tree_before=self.log.observation(pre, step, "before"),
                tree_after=None,
                error={"name": "ObservationUnavailable", "message": str(exc)},
                screenshots=[],
                screenshot=None,
            )
            raise BoundaryError(
                "Post-action observation is unavailable; recovery is required"
            ) from exc
        self.last_observation = post
        before = self.log.observation(pre, step, "before")
        after = self.log.observation(post, step, "after")
        shot = self.log.screenshot(post["screenshot"], step)
        self.log.write(
            kind="tool",
            step=step,
            tool=name,
            call_id=call["call_id"],
            code=code,
            code_sha=hashlib.sha256(code.encode()).hexdigest(),
            source=self.source,
            action_count=count,
            error=error,
            tree_before=before,
            tree_after=after,
            tree_sha_before=hashlib.sha256(pre["tree"].encode()).hexdigest(),
            tree_sha_after=hashlib.sha256(post["tree"].encode()).hexdigest(),
            screenshot=shot,
            screenshots=[shot],
            url_before=pre.get("url"),
            url_after=post.get("url"),
            exec_ms=round((time.monotonic() - started) * 1000),
            tokens=None,
            cost_usd=None,
        )
        self.history.append(
            {"tool": name, "code": code, "error": error, "url": post.get("url")}
        )
        if name == "act" and not error and self.source == "model" and self.last_actions:
            self.candidates.append(
                {
                    "before": pre["tree"],
                    "after": post["tree"],
                    "code": code,
                    "action_ids": [action["action_id"] for action in self.last_actions],
                }
            )
        if fatal:
            raise BoundaryError(str(fatal)) from fatal
        content = [
            {
                "type": "input_text",
                "text": json.dumps(
                    {"error": error, "completed_actions": len(self.last_actions)}
                )
                + "\n"
                + post["tree"][: self.policy.max_tree_chars],
            }
        ]
        if self.policy.screenshot(
            step=step,
            error=bool(error),
            explicit=name == "observe" and args.get("mode") != "tree",
            unchanged=pre["tree"] == post["tree"],
        ):
            content.append(
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64," + post["screenshot"],
                    "detail": "original",
                }
            )
        return {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": content,
        }
