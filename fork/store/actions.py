"""Durable action intent and atomic publication of verified filesystem evidence."""

import hashlib
import json
import re
from pathlib import Path

from fork.store.db import encoded, now

OUTCOMES = {"succeeded", "no_op", "failed_before_effect", "effect_unknown"}


def artifact(store, value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or not {"path", "sha256", "size_bytes"} <= value.keys()
    ):
        raise ValueError("Missing artifact metadata")
    path = Path(value["path"])
    path = (path if path.is_absolute() else store.artifact_root / path).resolve()
    if not path.is_relative_to(store.artifact_root) or not path.is_file():
        raise ValueError("Missing artifact or path outside the artifact root")
    if (
        type(value["size_bytes"]) is not int
        or path.stat().st_size != value["size_bytes"]
    ):
        raise ValueError("Artifact size mismatch")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != value["sha256"]:
        raise ValueError("Artifact hash mismatch")
    return {**value, "path": str(path)}


def evidence(store, value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or not {"desktop", "browser_tree", "native_tree", "inventory"} <= value.keys()
    ):
        raise ValueError("Incomplete action evidence")
    return {key: artifact(store, item) for key, item in value.items()}


def _checkpoint(store, data: dict, *, concrete=True) -> dict:
    if not re.fullmatch(r"ck_[0-9a-f]{8}", data["id"]):
        raise ValueError("Invalid checkpoint ID")
    fidelity = data.get("fidelity", data.get("fidelity_json", {}))
    if isinstance(fidelity, str):
        fidelity = json.loads(fidelity)
    row = {
        k: data.get(k)
        for k in (
            "id",
            "branch",
            "node_id",
            "image",
            "run_id",
            "step",
            "action_id",
            "parent_id",
            "image_digest",
            "platform",
            "manifest_path",
            "manifest_sha",
            "incarnation",
        )
    }
    row.update(
        created=data.get("created", now()),
        status=data.get("status", "committed"),
        fidelity_json=encoded(fidelity),
        evidence_json=encoded(data.get("evidence", {})),
    )
    if concrete:
        if (
            row["status"] != "committed"
            or row["platform"] not in {"linux/amd64", "linux/arm64"}
            or not row["incarnation"]
        ):
            raise ValueError("Incomplete checkpoint identity")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", row["image_digest"] or ""):
            raise ValueError("Immutable image digest required")
        if fidelity.get("overall") not in {
            "verified_reconstruction",
            "partial",
            "unsupported",
            "failed",
        }:
            raise ValueError(
                "Filesystem fidelity required; exact full-state recovery is unsupported"
            )
        manifest = artifact(
            store,
            {
                "path": row["manifest_path"],
                "sha256": row["manifest_sha"],
                "size_bytes": data.get("manifest_size"),
            },
        )
        contents = json.loads(Path(manifest["path"]).read_text())
        if any(
            contents.get(key) != row[key]
            for key in (
                "id",
                "branch",
                "run_id",
                "parent_id",
                "image_digest",
                "incarnation",
                "platform",
            )
        ):
            raise ValueError("Manifest checkpoint identity mismatch")
        row["manifest_path"] = manifest["path"]
        row["evidence_json"] = encoded(evidence(store, data["evidence"]))
        if store.image_verifier:
            valid = store.image_verifier(row)
        else:
            from fork.cu import docker

            got = docker.command(
                "image",
                "inspect",
                "--format",
                "{{.Id}} {{.Os}}/{{.Architecture}}",
                row["image"],
            )
            valid = got == row["image_digest"] + " " + row["platform"]
        if valid is not True:
            raise ValueError("Checkpoint image digest or platform mismatch")
    return row


def _put(con, row: dict):
    old = con.execute("SELECT * FROM checkpoints WHERE id=?", (row["id"],)).fetchone()
    if old:
        if any(old[k] != v for k, v in row.items() if k != "created"):
            raise ValueError("Conflicting checkpoint publication")
        return
    con.execute(
        "INSERT INTO checkpoints("
        + ",".join(row)
        + ") VALUES ("
        + ",".join("?" for _ in row)
        + ")",
        tuple(row.values()),
    )


def _candidate(store, data, action_id=None):
    with store.connection() as con:
        old = con.execute(
            "SELECT payload_json,action_id FROM checkpoint_candidates WHERE id=?",
            (data["id"],),
        ).fetchone()
        payload = encoded(data)
        if old and tuple(old) != (payload, action_id):
            raise ValueError("Conflicting checkpoint candidate")
        con.execute(
            "INSERT OR IGNORE INTO checkpoint_candidates(id,action_id,payload_json,created) VALUES (?,?,?,?)",
            (data["id"], action_id, payload, now()),
        )


def record_checkpoint(store, data):
    legacy = data.get("status") == "legacy"
    if not legacy:
        _candidate(store, data)
    row = _checkpoint(store, data, concrete=not legacy)
    with store.transaction() as con:
        _put(con, row)
        con.execute(
            "UPDATE checkpoint_candidates SET status='committed' WHERE id=?",
            (row["id"],),
        )
    return row["id"]


def record_intent(store, data):
    if (
        not re.fullmatch(r"a_[0-9a-f]{12}", data["id"])
        or type(data["sequence"]) is not int
        or data["sequence"] < 0
    ):
        raise ValueError("Invalid action identity")
    pre = evidence(store, data["pre_evidence"])
    row = {
        k: data[k]
        for k in (
            "id",
            "run_id",
            "branch",
            "incarnation",
            "call_id",
            "sequence",
            "operation",
            "pre_checkpoint_id",
        )
    }
    row.update(
        arguments_json=encoded(data["arguments"]),
        pre_evidence_json=encoded(pre),
        outcome="effect_unknown",
        checkpoint_status="pending",
        created=data.get("created", now()),
    )
    with store.transaction() as con:
        old = con.execute("SELECT * FROM actions WHERE id=?", (row["id"],)).fetchone()
        if old:
            if any(
                old[k] != v
                for k, v in row.items()
                if k not in {"created", "outcome", "checkpoint_status"}
            ):
                raise ValueError("Conflicting action intent")
            return
        parent = con.execute(
            "SELECT * FROM checkpoints WHERE id=?", (row["pre_checkpoint_id"],)
        ).fetchone()
        run = con.execute(
            "SELECT branch FROM runs WHERE id=?", (row["run_id"],)
        ).fetchone()
        if (
            not parent
            or parent["status"] != "committed"
            or not run
            or run["branch"] != row["branch"]
            or parent["branch"] != row["branch"]
            or parent["incarnation"] != row["incarnation"]
        ):
            raise ValueError("Action baseline or incarnation mismatch")
        if con.execute(
            "SELECT 1 FROM actions WHERE branch=? AND incarnation=? AND checkpoint_status='pending'",
            (row["branch"], row["incarnation"]),
        ).fetchone():
            raise ValueError("Reconcile the pending action before another mutation")
        con.execute(
            "INSERT INTO actions("
            + ",".join(row)
            + ") VALUES ("
            + ",".join("?" for _ in row)
            + ")",
            tuple(row.values()),
        )


def publish(store, action_id: str, checkpoint: dict, post_evidence: dict, outcome: str):
    if outcome not in OUTCOMES:
        raise ValueError("Invalid action outcome")
    data = {**checkpoint, "action_id": action_id, "evidence": post_evidence}
    _candidate(store, data, action_id)
    row = _checkpoint(store, data)
    post = encoded(evidence(store, post_evidence))
    with store.transaction() as con:
        action = con.execute(
            "SELECT * FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        if not action:
            raise ValueError("Missing action intent")
        if (
            row["branch"] != action["branch"]
            or row["incarnation"] != action["incarnation"]
            or row["run_id"] != action["run_id"]
            or row["parent_id"] != action["pre_checkpoint_id"]
        ):
            raise ValueError("Post-checkpoint action lineage mismatch")
        if action["checkpoint_status"] == "committed":
            if (
                action["post_checkpoint_id"] != row["id"]
                or action["outcome"] != outcome
                or action["post_evidence_json"] != post
            ):
                raise ValueError("Conflicting action publication")
            _put(con, row)
            return row["id"]
        _put(con, row)
        con.execute(
            "UPDATE actions SET post_checkpoint_id=?,post_evidence_json=?,outcome=?,checkpoint_status='committed',completed=? WHERE id=?",
            (row["id"], post, outcome, now(), action_id),
        )
        con.execute(
            "UPDATE checkpoint_candidates SET status='committed' WHERE id=?",
            (row["id"],),
        )
    return row["id"]


def link_actions(con, edge_id: str, action_ids):
    if len(set(action_ids)) != len(action_ids):
        raise ValueError("Duplicate edge action")
    prior = None
    for action_id in action_ids:
        action = con.execute(
            "SELECT * FROM actions WHERE id=?", (action_id,)
        ).fetchone()
        if (
            not action
            or action["checkpoint_status"] != "committed"
            or action["outcome"] not in {"succeeded", "no_op"}
        ):
            raise ValueError("An edge requires committed, successful actions")
        for ck_id in (action["pre_checkpoint_id"], action["post_checkpoint_id"]):
            checkpoint = con.execute(
                "SELECT fidelity_json FROM checkpoints WHERE id=?", (ck_id,)
            ).fetchone()
            if json.loads(checkpoint["fidelity_json"]).get("overall") not in {
                "partial",
                "verified_reconstruction",
            }:
                raise ValueError("Checkpoint fidelity does not support recovery")
        if prior and (
            action["pre_checkpoint_id"] != prior["post_checkpoint_id"]
            or action["incarnation"] != prior["incarnation"]
            or action["run_id"] != prior["run_id"]
            or action["call_id"] != prior["call_id"]
            or action["sequence"] != prior["sequence"] + 1
        ):
            raise ValueError("Edge actions are not a contiguous committed sequence")
        prior = action
    # Keep the first concrete exemplar. Every subsequent execution retains its own
    # action IDs in step evidence, without replacing the exemplar's retained images.
    existing = con.execute(
        "SELECT 1 FROM edge_actions WHERE edge_id=?", (edge_id,)
    ).fetchone()
    if not existing:
        con.executemany(
            "INSERT INTO edge_actions VALUES (?,?,?)",
            [(edge_id, i, action_id) for i, action_id in enumerate(action_ids)],
        )
    con.execute(
        "UPDATE edges SET recovery_status='filesystem_checkpoints',verification_scope='actions' WHERE id=?",
        (edge_id,),
    )
