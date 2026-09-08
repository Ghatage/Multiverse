"""Ordered, identical-task replay through the mandatory mutation coordinator."""

import json
import re
import time

from fork.mutations.coordinator import BoundaryError, Coordinator
from fork.mutations.desktop import Desktop
from fork.mutations.recovery import recover
from fork.replay.config import ReplayConfig
from fork.replay.tools import TOOLS_CHECKPOINTED, Executor
from fork.replay.verify import verify
from fork.store.db import encoded
from fork.store.signature import normalise


class ReplayHooks:
    tools = TOOLS_CHECKPOINTED
    instructions = "Use act for all mutations. Arbitrary JS/Python is unavailable. Every individual action gets a durable filesystem checkpoint. Use CSS selectors from observed fields, or Playwright text=/role= selectors. Do not submit orders unless the task explicitly requests it."

    def __init__(self, mode: str, config: ReplayConfig | None = None):
        if mode not in {"cold", "warm", "auto"}:
            raise ValueError("Mode must be cold, warm or auto")
        self.mode = mode
        self.config = config or ReplayConfig()
        self.path = []
        self.position = 0
        self.hits = self.misses = 0
        self.repairs = 0
        self.repairing = False
        self.failed_edge = None
        self.repair_started = None
        self.coordinator = None
        self.replay_steps = 0

    def bind(self, branch, task, log, store, policy, budget, checker, *, deadline):
        self.branch, self.task, self.log, self.store = branch, task, log, store
        self.policy, self.budget, self.checker = policy, budget, checker
        self.deadline = deadline
        self.path = self._path() if self.mode != "cold" else []
        backend = Desktop(branch, log.run_id, log.path, deadline=deadline)
        self.coordinator = Coordinator(
            store, backend, log.run_id, emit=log.write, before_action=budget
        )
        self.coordinator.__enter__()
        self.executor = Executor(self.coordinator, log, store, policy)
        return self.executor

    @property
    def incarnation(self):
        return self.coordinator.backend.incarnation if self.coordinator else None

    def _path(self):
        with self.store.connection() as con:
            runs = con.execute(
                "SELECT id FROM runs WHERE checker_pass=1 AND stop_reason='final_answer' AND checkpoint_coverage='committed_action_checkpoints' ORDER BY finished DESC"
            ).fetchall()
            for run in runs:
                task_path = self.log.path.parent / run["id"] / "task.json"
                if not task_path.is_file() or encoded(
                    json.loads(task_path.read_text())
                ) != encoded(self.task):
                    continue
                steps = con.execute(
                    "SELECT s.*,e.post_signature,e.status,e.recovery_status,e.to_node AS expected_node FROM steps s JOIN edges e ON e.id=s.edge_id WHERE s.run_id=? AND s.idx>1 AND s.tool='act' AND s.verified=1 ORDER BY s.idx",
                    (run["id"],),
                ).fetchall()
                if not steps or any(
                    step["status"] != "active"
                    or step["recovery_status"] != "filesystem_checkpoints"
                    for step in steps
                ):
                    continue
                return [dict(step) for step in steps]
        return []

    def bootstrap(self, *, resume: bool = False):
        if resume:
            self.path = []
            return self.executor.execute(
                {"name": "observe", "call_id": "resume", "arguments": '{"mode":"both"}'}
            )
        return self.executor.execute(
            {
                "name": "act",
                "call_id": "bootstrap",
                "arguments": json.dumps(
                    {
                        "actions": [
                            {"operation": "navigate", "url": self.task["start_url"]}
                        ]
                    }
                ),
            }
        )

    def on_run_start(self, branch):
        pass

    def _complete(self):
        url = self.executor.last_observation.get("url", "")
        pattern = self.task.get("final_url_pattern")
        if pattern and re.search(pattern, url):
            return self.checker().get("pass") is True
        return False

    def before_model_turn(self):
        self.check_budget()
        if self._complete():
            return {"complete": True}
        if self.repairing or self.mode == "cold" or self.position >= len(self.path):
            return None
        if self.replay_steps >= self.config.max_replay_steps:
            raise BoundaryError("Replay step cap reached")
        step = self.path[self.position]
        tree = self.executor.last_observation.get("tree", "")
        with self.store.connection() as con:
            before = dict(
                con.execute(
                    "SELECT * FROM nodes WHERE id=?", (step["from_node"],)
                ).fetchone()
            )
            expected = dict(
                con.execute(
                    "SELECT * FROM nodes WHERE id=?", (step["expected_node"],)
                ).fetchone()
            )
        edge = {"to_node": expected["id"], "post_signature": step["post_signature"]}
        # A precondition mismatch is a failed cached path, not permission to click
        # a stale locator on an unrelated state.
        if normalise(tree).signature != before["signature"]:
            self.misses += 1
            self._repair(step, self.coordinator.current["id"])
            return {"reset": True}
        self.executor.source = "replay"
        self.replay_steps += 1
        failed = None
        try:
            result = self.executor.execute(
                {
                    "name": "act",
                    "call_id": f"replay-{self.log.run_id}-{self.replay_steps}",
                    "arguments": json.dumps({"actions": json.loads(step["code"])}),
                }
            )
        except BoundaryError as exc:
            failed = exc
            result = None
        finally:
            self.executor.source = "model"
        post = self.executor.last_observation.get("tree", "")
        checked = verify(post, edge, expected, threshold=self.config.fuzzy_threshold)
        if failed is None and checked.ok:
            self.store.mark_edge(step["edge_id"], True, 0)
            self.position += 1
            self.hits += 1
            return {"replayed": True, "output": result["output"]}
        self.misses += 1
        actions = [
            a
            for a in self.store.actions_for(self.log.run_id)
            if a["call_id"] == f"replay-{self.log.run_id}-{self.replay_steps}"
        ]
        if not actions:
            raise BoundaryError("Failed replay has no recoverable action boundary")
        action = actions[-1]
        self._repair(
            step, action["pre_checkpoint_id"], json.loads(action["pre_evidence_json"])
        )
        return {"reset": True}

    def _repair(self, step, checkpoint, expected_evidence=None):
        self.store.mark_edge(step["edge_id"], False, 0)
        self.failed_edge = step["edge_id"]
        self.repairing = True
        self.repair_started = (time.monotonic(), self.log.cost_usd)
        self.repair_candidate_start = len(self.executor.candidates)
        prefix = []
        if expected_evidence is not None:
            from fork.replay.repair import retained_prefix

            call_id = f"replay-{self.log.run_id}-{self.replay_steps}"
            prefix = retained_prefix(
                [
                    action
                    for action in self.store.actions_for(self.log.run_id)
                    if action["call_id"] == call_id
                ]
            )
        self.coordinator.backend.keep_isolated = True
        self.coordinator.__exit__(None, None, None)
        backend = recover(
            self.store,
            self.branch,
            self.log.run_id,
            self.log.path,
            checkpoint,
            expected_evidence=expected_evidence,
            expected_incarnation=self.coordinator.backend.incarnation,
            deadline=min(
                self.deadline, self.repair_started[0] + self.config.repair_max_wall_s
            ),
        )
        self.coordinator = Coordinator(
            self.store,
            backend,
            self.log.run_id,
            emit=self.log.write,
            before_action=self.check_budget,
        )
        self.coordinator.forbidden_actions = [
            {"operation": "navigate", "url": self.task["start_url"]},
            *prefix,
        ]
        self.coordinator.__enter__()
        self.executor.coordinator = self.coordinator
        self.executor.last_observation = backend.repl.call(
            "observe", mode="both", target="browser", max_tree_chars=2_000_000
        )
        self.log.write(
            kind="recovery",
            step=None,
            checkpoint=checkpoint,
            verified=True,
            incarnation=backend.incarnation,
            fidelity="partial",
            supported=["guest_files", "local_tenant", "browser_fields"],
        )

    def repair_prompt(self):
        return "A cached transition failed. Recovery restored its pre-action boundary and verified saved files, local tenant state and browser values. Preserve the completed form fields; do not repeat already completed actions. Re-observe and complete the remaining task using the changed UI."

    def check_budget(self):
        self.budget()
        if (
            self.repair_started
            and time.monotonic()
            >= self.repair_started[0] + self.config.repair_max_wall_s
        ):
            from fork.agent.loop import StopRun

            raise StopRun("repair_wall_cap")

    def finish(self, summary):
        summary["cache"] = {"hits": self.hits, "misses": self.misses}
        summary["repairs"] = 0
        if self.repairing:
            passed = (
                summary["checker"].get("pass") is True
                and summary["stop_reason"] == "final_answer"
            )
            if passed:
                from fork.replay.repair import publish_replacement

                replacement = publish_replacement(
                    self.store, self.executor.candidates[self.repair_candidate_start :]
                )
                self.store.demote(self.failed_edge)
                self.repairs = 1
            summary["repairs"] = self.repairs
            summary["repair"] = {
                "edge_old": self.failed_edge,
                "edge_new": replacement if passed else None,
                "verified": passed,
                "variants": ["medium"],
                "cost_usd": self.log.cost_usd - self.repair_started[1],
                "wall_s": time.monotonic() - self.repair_started[0],
            }
            self.log.write(
                kind="repair",
                step=None,
                winner=self.branch,
                **summary["repair"],
            )
        if self.coordinator:
            self.coordinator.__exit__(None, None, None)
