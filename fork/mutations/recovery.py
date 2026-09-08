"""Verified filesystem and local-browser recovery without re-executing a batch prefix."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from fork import locks
from fork.cu import branch as branches
from fork.cu import db, docker
from fork.mutations.desktop import Desktop
from fork.mutations.external import restore as restore_external
from fork.repl_client import ReplClient
from fork.store.actions import _checkpoint
from fork.store.actions import evidence as validate_evidence
from fork.store.db import Store, encoded, now


def verify_current_external(state, required_tenant: str | None = None) -> None:
    from fork.mutations.external import capture

    with ReplClient(f"http://localhost:{state.ports['repl']}") as repl:
        current = repl.call("evidence", paths=[])
    urls = [tab["url"] for tab in current["inventory"]["browser_state"]["tabs"]]
    if required_tenant:
        urls.append(f"http://host.docker.internal:3000/t/{state.name}/")
    for url in urls:
        if capture(url, state.name).get("supported") is not True:
            raise ValueError(
                "Current external state cannot be reconciled; recovery did not replace the desktop"
            )


def recover(
    store: Store,
    branch: str,
    run_id: str,
    root: Path,
    checkpoint_id: str,
    *,
    expected_evidence: dict | None = None,
    deadline: float | None = None,
    expected_incarnation: str | None = None,
) -> Desktop:
    with store.connection() as con:
        candidate = con.execute(
            "SELECT payload_json FROM checkpoint_candidates WHERE id=? AND status='committed'",
            (checkpoint_id,),
        ).fetchone()
    if not candidate:
        raise ValueError("Checkpoint has no committed recovery evidence")
    saved = json.loads(candidate["payload_json"])
    _checkpoint(
        store, saved
    )  # Includes image ID/platform, manifest and all artifact hashes.
    proof = validate_evidence(store, expected_evidence or saved["evidence"])
    inventory = json.loads(Path(proof["inventory"]["path"]).read_text())
    if "browser_state" not in inventory or not inventory.get("external", {}).get(
        "supported"
    ):
        raise ValueError(
            "Checkpoint lacks supported browser/external recovery evidence"
        )
    # Keep input isolated across container replacement. The saved lease marker
    # also makes the new X server read-only before its REPL becomes reachable.
    for tab in inventory["browser_state"]["tabs"]:
        parsed = urlsplit(tab["url"])
        if tab["url"] != "about:blank" and (
            parsed.netloc != "host.docker.internal:3000"
            or not parsed.path.startswith("/t/" + branch + "/")
        ):
            raise ValueError(
                "Browser reconstruction requires this branch's local tenant"
            )
    with locks.branch(branch):
        state = db.get_branch(branch)
        if expected_incarnation and state.container_id != expected_incarnation:
            raise ValueError("Branch incarnation changed since this recovery request")
        if saved["branch"] != branch:
            raise ValueError("Recovery checkpoint belongs to another branch")
        docker.owned(branch, state.container_id)
        verify_current_external(state, inventory["external"].get("tenant"))
        # The trajectory journal and branch registry are separate databases.
        # Publish the verified checkpoint in the registry before referencing it,
        # and before replacing the running container.
        with db.connect() as con:
            existing = con.execute(
                "SELECT image,branch FROM checkpoints WHERE id=?", (checkpoint_id,)
            ).fetchone()
            if existing and (
                existing["image"] != saved["image_digest"]
                or existing["branch"] != branch
            ):
                raise ValueError(
                    "Checkpoint registry identity conflicts with recovery evidence"
                )
            if not existing:
                con.execute(
                    "INSERT INTO checkpoints(id,branch,image,label,created) VALUES(?,?,?,?,?)",
                    (
                        checkpoint_id,
                        branch,
                        saved["image_digest"],
                        "verified action boundary",
                        db.now(),
                    ),
                )
        db.update(branch, status="starting")
        docker.rm(branch, state.container_id)
        db.update(
            branch,
            image=saved["image_digest"],
            parent_checkpoint=checkpoint_id,
            container_id=None,
        )
        branches._start(db.get_branch(branch))
    backend = Desktop(branch, run_id, root, deadline=deadline)
    backend.recovery_from = saved["run_id"]
    backend.files = set(inventory.get("files", {}))
    try:
        backend.acquire()
        restore_external(inventory["external"], branch)
        backend.repl.call(
            "recover_browser", token=backend.token, snapshot=inventory["browser_state"]
        )
        actual = backend.repl.call("evidence", paths=sorted(backend.files))["inventory"]
        if actual["files"] != inventory.get("files", {}):
            raise RuntimeError(
                "Restored files do not match concrete checkpoint evidence"
            )
        if actual["browser_state"] != inventory["browser_state"]:
            raise RuntimeError(
                "Restored browser state does not match concrete evidence"
            )
        with store.connection() as con:
            con.execute(
                "INSERT INTO recoveries(run_id,checkpoint_id,old_incarnation,new_incarnation,evidence_json,created) VALUES(?,?,?,?,?,?)",
                (
                    run_id,
                    checkpoint_id,
                    state.container_id,
                    backend.incarnation,
                    encoded(proof),
                    now(),
                ),
            )
        return backend
    except BaseException:
        backend.keep_isolated = True
        if backend.lease:
            backend.release()
        raise


def recover_action(
    action_id: str, boundary: str = "pre", store: Store | None = None
) -> dict:
    """Restore a journaled boundary; execution of a suffix is a separate operation."""
    from fork.agent.log import new_run_id

    if boundary not in {"pre", "post"}:
        raise ValueError("Boundary must be pre or post")
    store = store or Store()
    with store.connection() as con:
        row = con.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown action")
    action = dict(row)
    checkpoint_id = action[boundary + "_checkpoint_id"]
    if not checkpoint_id:
        raise ValueError("Selected action has no committed boundary")
    proof = json.loads(action[boundary + "_evidence_json"])
    current_incarnation = db.get_branch(action["branch"]).container_id
    run_id = new_run_id()
    store.record_run(run_id, "recover:" + action_id, action["branch"], "repair")
    backend = recover(
        store,
        action["branch"],
        run_id,
        store.artifact_root / run_id,
        checkpoint_id,
        expected_evidence=proof,
        expected_incarnation=current_incarnation,
    )
    try:
        result = {
            "action_id": action_id,
            "boundary": boundary,
            "checkpoint_id": checkpoint_id,
            "branch": action["branch"],
            "incarnation": backend.incarnation,
            "verified": True,
            "fidelity": "partial",
            "supported": ["guest_files", "local_tenant", "browser_fields"],
            "excluded": [
                "process_memory",
                "unsaved_app_state",
                "native_app_reconstruction",
            ],
            "resumable_suffix": {
                "call_id": action["call_id"],
                "next_sequence": action["sequence"] + (boundary == "post"),
                "automatic_execution": False,
            },
        }
        store.finish_run(run_id, stop_reason="recovered", checker_pass=None)
        return result
    finally:
        backend.release()
