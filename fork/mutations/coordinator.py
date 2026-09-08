"""Publish every action boundary before admitting another desktop mutation."""

import secrets
import time
from collections.abc import Callable
from typing import Protocol

from fork.mutations.actions import validate
from fork.store.db import Store


class BoundaryError(RuntimeError):
    """A mutation may have happened; recovery requires reconciliation."""


class Backend(Protocol):
    branch: str
    incarnation: str

    def acquire(self) -> None: ...
    def release(self) -> None: ...
    def assert_current(self) -> None: ...
    def evidence(self, label: str) -> dict: ...
    def checkpoint(
        self, checkpoint_id: str, parent: str | None, evidence: dict
    ) -> dict: ...
    def execute(self, action: dict) -> dict: ...
    def prepare(self, action: dict) -> None: ...


class Coordinator:
    def __init__(
        self,
        store: Store,
        backend: Backend,
        run_id: str,
        *,
        emit: Callable[..., None],
        before_action: Callable[[], None] | None = None,
    ):
        self.store, self.backend, self.run_id = store, backend, run_id
        self.emit, self.before_action = emit, before_action or (lambda: None)
        self.current: dict | None = None
        self.active = False
        self.failed = False
        self.forbidden_actions: list[dict] = []

    def _event(self, kind: str, **data) -> None:
        self.emit(kind=kind, step=None, **data)

    def __enter__(self):
        self.backend.acquire()
        self.active = True
        try:
            self.backend.assert_current()
            with self.store.connection() as con:
                reconciled = {
                    row[0]
                    for row in con.execute(
                        "SELECT old_incarnation FROM recoveries WHERE run_id=? AND new_incarnation=?",
                        (self.run_id, self.backend.incarnation),
                    )
                }
            pending = [
                a
                for a in self.store.actions_for(self.run_id)
                if (
                    a["checkpoint_status"] != "committed"
                    or a["outcome"] == "effect_unknown"
                )
                and a["incarnation"] not in reconciled
            ]
            if pending:
                raise BoundaryError(
                    "Reconcile unfinished actions before resuming the run"
                )
            self.before_action()
            if hasattr(self.backend, "admit"):
                self.backend.admit(self.store)
            baseline = self._capture(None, "baseline-" + secrets.token_hex(4))
            from fork.store.actions import record_checkpoint

            record_checkpoint(self.store, baseline)
            self.current = baseline
            self._event("checkpoint", checkpoint=baseline)
            return self
        except BaseException:
            self.failed = True
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.active:
            try:
                if self.failed:
                    self.backend.keep_isolated = True
                self.backend.release()
            finally:
                self.active = False

    def _capture(
        self, parent: str | None, label: str, action_id: str | None = None
    ) -> dict:
        self.backend.assert_current()
        proof = self.backend.evidence(label)
        checkpoint_id = "ck_" + secrets.token_hex(4)
        from fork.store.actions import reserve_checkpoint

        reserve_checkpoint(
            self.store,
            {
                "id": checkpoint_id,
                "branch": self.backend.branch,
                "run_id": self.run_id,
                "incarnation": self.backend.incarnation,
                "image": "fork-ckpt:" + checkpoint_id,
            },
            action_id,
        )
        ck = self.backend.checkpoint(checkpoint_id, parent, proof)
        return {**ck, "run_id": self.run_id, "evidence": proof}

    def execute(self, call_id: str, actions: list[dict]) -> list[dict]:
        if not self.active or self.failed or self.current is None:
            raise BoundaryError("No healthy mutation lease and baseline")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(actions, list)
            or not 1 <= len(actions) <= 50
        ):
            raise ValueError("A batch requires a call ID and one to fifty actions")
        actions = [
            validate(action) for action in actions
        ]  # Reject the entire batch before its first effect.
        if any(action in self.forbidden_actions for action in actions):
            raise ValueError(
                "Recovery refuses to repeat a completed prefix or restart the task; submit only the remaining suffix"
            )
        prior = [
            a for a in self.store.actions_for(self.run_id) if a["call_id"] == call_id
        ]
        if prior:
            raise BoundaryError(
                "Call already journaled; inspect its boundaries instead of rerunning a prefix"
            )
        results = []
        for sequence, action in enumerate(actions):
            self.before_action()
            self.backend.assert_current()
            action_id = "a_" + secrets.token_hex(6)
            started = time.monotonic()
            try:
                if hasattr(self.backend, "admit"):
                    self.backend.admit(self.store)
                if hasattr(self.backend, "prepare"):
                    self.backend.prepare(action)
                pre = self.backend.evidence(action_id + "-pre")
                if hasattr(self.backend, "continuous") and not self.backend.continuous(
                    self.current["evidence"], pre
                ):
                    from fork.store.actions import record_checkpoint

                    baseline = self._capture(self.current["id"], action_id + "-drift")
                    record_checkpoint(self.store, baseline)
                    self.current = baseline
                    pre = baseline["evidence"]
                    self._event("checkpoint", checkpoint=baseline, reason="state_drift")
                intent = {
                    "id": action_id,
                    "run_id": self.run_id,
                    "branch": self.backend.branch,
                    "incarnation": self.backend.incarnation,
                    "call_id": call_id,
                    "sequence": sequence,
                    "operation": action["operation"],
                    "arguments": {k: v for k, v in action.items() if k != "operation"},
                    "pre_checkpoint_id": self.current["id"],
                    "pre_evidence": pre,
                }
                self.store.record_action_intent(intent)
                self._event("action_intent", action=intent)
                error = None
                try:
                    result = self.backend.execute(action)
                    outcome = "no_op" if result.get("no_op") is True else "succeeded"
                except Exception as exc:  # noqa: BLE001 - any execution failure may have had an effect
                    # Exceptions do not prove that the effect did not happen.
                    result = {}
                    error = f"{type(exc).__name__}: {exc}"
                    outcome = "effect_unknown"
                post = self._capture(self.current["id"], action_id + "-post", action_id)
                self.store.publish_action_checkpoint(
                    action_id, post, post["evidence"], outcome
                )
                self.current = post
                record = {
                    "action_id": action_id,
                    "checkpoint": post,
                    "evidence": post["evidence"],
                    "outcome": outcome,
                    "error": error,
                    "wall_s": time.monotonic() - started,
                }
                self._event("action_checkpoint", **record)
                results.append(record)
                if error:
                    raise BoundaryError(
                        "Action effect is unknown; the remaining batch was not executed"
                    )
            except BaseException:
                self.failed = True
                raise
        return results
