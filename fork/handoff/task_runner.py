"""Detached, capped agent continuation on an already restored desktop."""

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

ACTIVE = {"queued", "running"}


def write_state(directory, state):
    temporary = directory / f"task-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(state))
    temporary.replace(directory / "task.json")


def read_state(directory):
    path = directory / "task.json"
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or state.get("status") not in ACTIVE | {
            "completed",
            "stopped",
            "failed",
        }:
            raise ValueError("Invalid task state")
    except (ValueError, OSError):
        state = {
            "status": "failed",
            "error": "Task status is unreadable; inspect the local worker log.",
        }
        write_state(directory, state)
    if state.get("status") in ACTIVE:
        with (directory / "task.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                state.update(
                    status="failed",
                    error="Task worker exited before recording completion.",
                )
                write_state(directory, state)
    if (directory / "task.cancel").exists() and state.get("status") in ACTIVE:
        state["cancellation_requested"] = True
    return {k: v for k, v in state.items() if k != "pid"}


def launch_task(directory: Path, branch: str, prompt: str):
    """Transfer an already-held lock to the child so queued jobs cannot duplicate."""
    lock = (directory / "task.lock").open("a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A task is already active on this handoff") from exc
        (directory / "task.cancel").unlink(missing_ok=True)
        state = {"status": "queued", "prompt": prompt}
        write_state(directory, state)
        env = dict(os.environ)
        env.pop("MULTIVERSE_HANDOFF_TOKEN", None)
        try:
            with (directory / "task-worker.log").open("ab") as output:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "fork.handoff.task_runner",
                        str(directory.resolve()),
                        branch,
                        "--lock-fd",
                        str(lock.fileno()),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                    pass_fds=(lock.fileno(),),
                    env=env,
                )
        except Exception:
            state.update(status="failed", error="Unable to launch task worker")
            write_state(directory, state)
            raise
        return state
    finally:
        lock.close()


class FileCancellation:
    def __init__(self, directory):
        self.path = directory / "task.cancel"

    def is_set(self):
        return self.path.exists()


def prepare_store(branch):
    """Bridge cu image ancestry into the action graph without claiming action proof."""
    from fork.cu import db
    from fork.store.actions import record_checkpoint
    from fork.store.db import Store

    store = Store(artifact_root=Path(os.environ.get("FORK_RUNS_DIR", "runs")))
    checkpoint = db.latest_checkpoint(branch)
    missing = []
    seen = set()
    while checkpoint:
        identifier = checkpoint["id"]
        if identifier in seen:
            raise ValueError("Checkpoint ancestry contains a cycle")
        seen.add(identifier)
        with store.connection() as con:
            if con.execute(
                "SELECT 1 FROM checkpoints WHERE id=?", (identifier,)
            ).fetchone():
                break
        missing.append(checkpoint)
        parent = checkpoint.get("parent_checkpoint")
        checkpoint = db.get_checkpoint(parent) if parent else None
    for checkpoint in reversed(missing):
        record_checkpoint(
            store,
            {
                "id": checkpoint["id"],
                "branch": checkpoint["branch"],
                "image": checkpoint["image"],
                "created": checkpoint["created"],
                "parent_id": checkpoint.get("parent_checkpoint"),
                "status": "legacy",
                "fidelity": {
                    "overall": "unsupported",
                    "reason": "Imported cu image checkpoint; action evidence was not certified in the trajectory graph",
                },
            },
        )
    return store


def run(directory: Path, branch: str, run_fn=None):
    from dotenv import load_dotenv

    from fork.agent.policy import Caps

    load_dotenv()
    state = json.loads((directory / "task.json").read_text())
    state.update(status="running", pid=os.getpid())
    write_state(directory, state)
    try:
        if run_fn is None:
            from fork.agent.loop import run_task

            run_fn = run_task
            agent_store = prepare_store(branch)
        else:
            agent_store = None

        def progress(cost):
            state["cost_usd"] = cost
            write_state(directory, state)

        if FileCancellation(directory).is_set():
            state.update(status="stopped", stop_reason="cancelled")
        else:
            result = run_fn(
                branch=branch,
                task={"id": "handoff-" + directory.name, "prompt": state["prompt"]},
                resume=True,
                store=agent_store,
                caps=Caps(max_turns=30, max_wall_s=600, max_cost_usd=5),
                cancel=FileCancellation(directory),
                progress=progress,
            )
            state.update(
                {
                    k: result[k]
                    for k in (
                        "run_id",
                        "final_text",
                        "error",
                        "stop_reason",
                        "cost_usd",
                        "steps",
                    )
                    if k in result
                }
            )
            state["status"] = (
                "failed"
                if result.get("error") or result.get("stop_reason") == "error"
                else (
                    "completed"
                    if result.get("stop_reason") in {"completed", "final_answer"}
                    else "stopped"
                )
            )
    except Exception as exc:
        # Keep provider exception details in local logs, never risk echoing credentials.
        logger.exception("Handoff task worker failed")
        state.update(
            status="failed",
            error=f"Task failed ({type(exc).__name__}); inspect the local task worker log.",
        )
    write_state(directory, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("branch")
    parser.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args()
    try:
        # Inherited descriptor owns the lock throughout this process, including setup.
        os.fstat(args.lock_fd)
        run(args.directory, args.branch)
    finally:
        os.close(args.lock_fd)


if __name__ == "__main__":
    main()
