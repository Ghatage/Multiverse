"""Recover into a new branch, preserving the original trajectory and its desktop."""

import json
import secrets

from fork.cu import branch, docker
from fork.repl_client import ReplClient
from fork.store.actions import artifact, evidence


def recover(store, action_id, side, name):
    if side not in {"pre", "post"}:
        raise ValueError("Select before or after the action")
    with store.connection() as con:
        a = con.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not a:
            raise ValueError("Action not found")
        ck = con.execute(
            "SELECT * FROM checkpoints WHERE id=?", (a[f"{side}_checkpoint_id"],)
        ).fetchone()
        if not ck or ck["status"] != "committed":
            raise ValueError("This action has no committed recovery point")
        ck = dict(ck)
    # Hash validation precedes container creation. Do not trust a mutable image tag.
    proof = json.loads(ck["evidence_json"])
    evidence(store, proof)
    path = store.artifact_root / ck["manifest_path"]
    artifact(
        store,
        {
            "path": str(path),
            "sha256": ck["manifest_sha"],
            "size_bytes": path.stat().st_size,
        },
    )
    got = docker.command(
        "image",
        "inspect",
        "--format",
        "{{.Id}} {{.Os}}/{{.Architecture}}",
        ck["image_digest"],
    )
    if got != ck["image_digest"] + " " + ck["platform"]:
        raise ValueError("Recovery image identity changed")
    expected = json.loads(
        (store.artifact_root / proof["inventory"]["path"]).read_text()
    )
    restored = branch.create(name, ck["image_digest"])
    # Preserve ancestry when starting directly from an immutable digest.
    from fork.cu import db

    db.update(name, parent_checkpoint=ck["id"])
    restored["parent_checkpoint"] = ck["id"]
    with ReplClient(f"http://localhost:{restored['ports']['repl']}") as repl:
        owner = secrets.token_hex(24)
        repl.call("action_acquire", owner=owner)
        try:
            inventory = repl.call("action_evidence", owner=owner)["inventory"]
        finally:
            repl.call("action_release", owner=owner)
        # The immutable image is sealed; observations remain available.
        observed = repl.call(
            "observe", mode="both", target="browser", max_tree_chars=2000000
        )
    active = next((t["url"] for t in expected.get("tabs", []) if t.get("active")), None)
    matched = active is not None and observed.get("url") == active
    wanted_tabs = [t["url"] for t in expected.get("tabs", [])]
    actual_tabs = [t["url"] for t in inventory.get("tabs", [])]
    return {
        **restored,
        "checkpoint": ck["id"],
        "action_id": action_id,
        "side": side,
        "verification": {
            "overall": "partial"
            if matched and wanted_tabs == actual_tabs
            else "failed",
            "browser_tabs": {
                "expected": wanted_tabs,
                "actual": actual_tabs,
                "matched": wanted_tabs == actual_tabs,
            },
            "active_browser_url": {
                "expected": active,
                "actual": observed.get("url"),
                "matched": matched,
            },
            "filesystem_image": "verified_digest",
            "live_memory": "unsupported",
            "message": "Saved filesystem restored. Unsaved app memory and external services are not restored.",
        },
        "viewer_url": f"http://localhost:{restored['ports']['novnc']}/vnc.html?autoconnect=1&resize=scale&view_only=true",
    }
