"""Idempotent import of run evidence; historical hashes are never invented trees."""

import hashlib
import json
from pathlib import Path

from fork.store.db import Store, encoded
from fork.store.refine import refine_run
from fork.store.signature import normalise


def _tree(root: Path, metadata: dict | None) -> str | None:
    if not metadata:
        return None
    path = (root / metadata["path"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing observation artifact")
    data = path.read_bytes()
    if (
        len(data) != metadata["size_bytes"]
        or hashlib.sha256(data).hexdigest() != metadata["sha256"]
    ):
        raise ValueError("Observation artifact does not match its hash")
    return data.decode() if metadata.get("complete") is True else None


def ingest_run(
    store: Store,
    root: Path,
    *,
    template_model: bool = False,
    max_cost_usd: float = 0.5,
    runner=None,
) -> dict:
    root = Path(root).resolve()
    summary_bytes = (root / "summary.json").read_bytes()
    log_bytes = (root / "steps.jsonl").read_bytes()
    task_bytes = (
        (root / "task.json").read_bytes() if (root / "task.json").exists() else b""
    )
    digest = hashlib.sha256(
        summary_bytes + b"\0" + log_bytes + b"\0" + task_bytes
    ).hexdigest()
    summary = json.loads(summary_bytes)
    run_id = summary["run_id"]
    if root.name != run_id:
        raise ValueError("Run directory and identity disagree")
    records = [
        json.loads(line) for line in log_bytes.decode().splitlines() if line.strip()
    ]
    if any(
        record.get("run_id") != run_id or record.get("branch") != summary["branch"]
        for record in records
    ):
        raise ValueError("Mixed run identities in log")
    # Read and hash every referenced tree before publishing any part of this import.
    prepared = [
        (
            record,
            _tree(root, record.get("tree_before")),
            _tree(root, record.get("tree_after")),
        )
        for record in records
        if record.get("kind") == "tool" and record.get("step") is not None
    ]
    verified = (
        summary.get("checker", {}).get("pass") is True
        and summary.get("stop_reason") == "final_answer"
    )
    coverage = "action_checkpoints_unavailable"
    unavailable = sum(
        1 for _, before, after in prepared if before is None or after is None
    )
    with store.connection() as con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute(
            "SELECT ingest_sha FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if old and old["ingest_sha"] not in {None, digest}:
            raise ValueError("The run changed after ingestion")
        if not old or old["ingest_sha"] is None:
            store.record_run(
                run_id,
                summary["task_id"],
                summary["branch"],
                summary.get("mode", "cold"),
                summary.get("effort", "low"),
            )
            _import_actions(store, records, run_id, summary["branch"])
            covered = 0
            covered_ids = set()
            mutations = 0
            for record, before, after in prepared:
                proof = {
                    "before": record.get("tree_before"),
                    "after": record.get("tree_after"),
                    "coverage": "tool_level",
                    "action_checkpoints": "unavailable",
                }
                if record.get("action_ids"):
                    proof.update(
                        coverage="action_level",
                        action_checkpoints="filesystem_partial",
                        action_ids=record["action_ids"],
                    )
                old_step = con.execute(
                    "SELECT * FROM steps WHERE run_id=? AND idx=?",
                    (run_id, record["step"]),
                ).fetchone()
                if old_step:
                    if (
                        old_step["verified"] is not None
                        or old_step["edge_id"] is not None
                        or old_step["tool"] != record.get("tool")
                        or old_step["code"] != record.get("code", "")
                        or old_step["source"] != record.get("source", "model")
                        or old_step["evidence_json"] != encoded(proof)
                    ):
                        raise ValueError("Conflicting live step finalization")
                    a, b = old_step["from_node"], old_step["to_node"]
                    for node, tree in ((a, before), (b, after)):
                        stored = con.execute(
                            "SELECT signature FROM nodes WHERE id=?", (node,)
                        ).fetchone()
                        if (tree is None and node is not None) or (
                            tree is not None
                            and (
                                not stored
                                or stored["signature"] != normalise(tree).signature
                            )
                        ):
                            raise ValueError(
                                "Live step node disagrees with observation"
                            )
                else:
                    a = store.node_for(before)[0] if before else None
                    b = store.node_for(after)[0] if after else None
                actions = _call_actions(con, run_id, record)
                if record.get("tool") in {"exec_js", "exec_py", "action"}:
                    mutations += 1
                    covered += actions is not None
                if actions is not None:
                    covered_ids.update(actions)
                    proof.update(action_ids=actions, action_checkpoints="committed")
                edge = None
                # A changed UI is insufficient: the independent whole-task checker must pass.
                ok = (
                    verified
                    and not record.get("error")
                    and before is not None
                    and after is not None
                )
                if ok and record.get("tool") in {"exec_js", "exec_py"}:
                    norm_a, norm_b = normalise(before), normalise(after)
                    if norm_a.app == norm_b.app:
                        edge = store.upsert_edge(
                            a,
                            b,
                            record["tool"],
                            record.get("code", ""),
                            {},
                            norm_b.signature,
                            record["tool"],
                            record.get("exec_ms", 0),
                            action_ids=actions or (),
                        )
                if old_step:
                    con.execute(
                        "UPDATE steps SET edge_id=?,verified=?,exec_ms=?,ts=?,evidence_json=? WHERE run_id=? AND idx=?",
                        (
                            edge,
                            int(ok),
                            record.get("exec_ms"),
                            record["ts"],
                            encoded(proof),
                            run_id,
                            record["step"],
                        ),
                    )
                else:
                    store.record_step(
                        run_id,
                        record["step"],
                        a,
                        b,
                        record.get("source", "model"),
                        record.get("tool"),
                        record.get("code", ""),
                        int(ok),
                        record.get("exec_ms"),
                        None,
                        None,
                        None,
                        edge,
                        evidence=proof,
                        ts=record["ts"],
                    )
            journal = store.actions_for(run_id)
            if (
                mutations
                and covered == mutations
                and covered_ids == {action["id"] for action in journal}
            ):
                coverage = "committed_action_checkpoints"
            elif covered or journal:
                coverage = "partial_action_checkpoints"
            tokens = summary.get("tokens", {})
            store.finish_run(
                run_id,
                stop_reason=summary.get("stop_reason"),
                model_calls=summary.get("model_calls", 0),
                tokens_in=tokens.get("input", 0),
                tokens_out=tokens.get("output", 0),
                cost_usd=summary.get("cost_usd", 0),
                wall_s=summary.get("wall_s", 0),
                checker_pass=summary.get("checker", {}).get("pass"),
                ingest_sha=digest,
                checkpoint_coverage=coverage,
            )
        coverage = con.execute(
            "SELECT checkpoint_coverage FROM runs WHERE id=?", (run_id,)
        ).fetchone()[0]
    materialised = (
        refine_run(store, run_id, root, max_cost_usd=max_cost_usd, runner=runner)
        if template_model
        else {"model_calls": 0, "cost_usd": 0.0, "parameterised_edges": 0}
    )
    with store.connection() as con:
        counts = con.execute(
            "SELECT COUNT(DISTINCT from_node),COUNT(DISTINCT edge_id) FROM steps WHERE run_id=?",
            (run_id,),
        ).fetchone()
        nodes = con.execute(
            "SELECT COUNT(*) FROM (SELECT from_node FROM steps WHERE run_id=? AND from_node IS NOT NULL UNION SELECT to_node FROM steps WHERE run_id=? AND to_node IS NOT NULL)",
            (run_id, run_id),
        ).fetchone()[0]
    return {
        "run_id": run_id,
        "nodes": nodes,
        "edges": counts[1],
        "steps": len(prepared),
        "observations_unavailable": unavailable,
        "checkpoint_coverage": coverage,
        **materialised,
    }


def _import_actions(store, records, run_id, branch):
    from fork.store.actions import record_checkpoint

    for record in records:
        kind = record["kind"]
        if kind == "checkpoint":
            checkpoint = record["checkpoint"]
            if checkpoint.get("run_id") != run_id or checkpoint.get("branch") != branch:
                raise ValueError("Checkpoint journal identity mismatch")
            record_checkpoint(store, checkpoint)
        elif kind == "action_intent":
            action = record["action"]
            if action["run_id"] != run_id or action["branch"] != branch:
                raise ValueError("Action journal identity mismatch")
            store.record_action_intent(action)
        elif kind == "action_checkpoint":
            store.publish_action_checkpoint(
                record["action_id"],
                record["checkpoint"],
                record["evidence"],
                record["outcome"],
            )


def _call_actions(con, run_id, record):
    count = record.get("action_count", len(record.get("action_ids", [])))
    if type(count) is not int or count < 1:
        return None
    actions = con.execute(
        "SELECT * FROM actions WHERE run_id=? AND call_id=? ORDER BY sequence",
        (run_id, record.get("call_id")),
    ).fetchall()
    if (
        len(actions) != count
        or [a["sequence"] for a in actions] != list(range(count))
        or any(
            a["checkpoint_status"] != "committed"
            or a["outcome"] not in {"succeeded", "no_op"}
            for a in actions
        )
    ):
        return None
    return [a["id"] for a in actions]
