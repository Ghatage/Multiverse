"""Optional, budgeted model refinement of already verified trajectory edges."""

import hashlib
import json
import math
from pathlib import Path

from fork.store.db import Store, encoded, now
from fork.store.edges import parameterise, render


def refine_run(
    store: Store, run_id: str, root: Path, *, max_cost_usd: float = 0.5, runner=None
) -> dict:
    if not math.isfinite(max_cost_usd) or max_cost_usd < 0:
        raise ValueError("Invalid parameterisation cost limit")
    task_path = root / "task.json"
    task = json.loads(task_path.read_text()) if task_path.exists() else {}
    with store.connection() as con:
        edges = [
            dict(row)
            for row in con.execute(
                "SELECT DISTINCT e.* FROM edges e JOIN steps s ON s.edge_id=e.id WHERE s.run_id=? ORDER BY e.id",
                (run_id,),
            )
        ]
    total = {"model_calls": 0, "cost_usd": 0.0, "parameterised_edges": 0}
    for edge in edges:
        if (
            json.loads(edge["params_json"])
            or edge["recovery_status"] != "action_checkpoints_unavailable"
        ):
            continue
        with store.connection() as con:
            urls = {
                row["id"]: row["url_pattern"]
                for row in con.execute(
                    "SELECT id,url_pattern FROM nodes WHERE id IN (?,?)",
                    (edge["from_node"], edge["to_node"]),
                )
            }
        context = {
            "prompt": task.get("prompt", ""),
            "tool": edge["tool"],
            "url_before": urls[edge["from_node"]],
            "url_after": urls[edge["to_node"]],
        }
        code_sha = hashlib.sha256(edge["code"].encode()).hexdigest()
        context_sha = hashlib.sha256(encoded(context).encode()).hexdigest()
        with store.connection() as con:
            cached = con.execute(
                "SELECT result_json FROM template_cache WHERE code_sha=? AND context_sha=?",
                (code_sha, context_sha),
            ).fetchone()
        if cached:
            result = json.loads(cached["result_json"])
        else:
            if total["cost_usd"] + 0.25 > max_cost_usd:
                continue
            result = parameterise(edge["code"], context, runner=runner)
            total["model_calls"] += result["model_calls"]
            total["cost_usd"] += result["cost_usd"]
            if not result["model_calls"]:
                continue
            with store.connection() as con:
                con.execute(
                    "INSERT OR IGNORE INTO template_cache VALUES (?,?,?,?)",
                    (code_sha, context_sha, encoded(result), now()),
                )
        if not result["params"]:
            continue
        if (
            render(
                result["template"],
                {k: v["example"] for k, v in result["params"].items()},
            )
            != edge["code"]
        ):
            raise ValueError("Cached template changed the original code")
        with store.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM edges WHERE id=?", (edge["id"],)
            ).fetchone()
            if current["code"] != edge["code"] or json.loads(current["params_json"]):
                continue
            other = con.execute(
                "SELECT * FROM edges WHERE from_node=? AND to_node=? AND tool=? AND code=? AND id!=?",
                (
                    edge["from_node"],
                    edge["to_node"],
                    edge["tool"],
                    result["template"],
                    edge["id"],
                ),
            ).fetchone()
            if other:
                schema = lambda params: {
                    k: {field: v for field, v in item.items() if field != "example"}
                    for k, item in params.items()
                }
                if other[
                    "recovery_status"
                ] != "action_checkpoints_unavailable" or schema(
                    json.loads(other["params_json"])
                ) != schema(result["params"]):
                    continue
                # Preserve the first template's example; completed step records retain every original code.
                con.execute(
                    "UPDATE steps SET edge_id=? WHERE edge_id=?",
                    (other["id"], edge["id"]),
                )
                con.execute(
                    "UPDATE edges SET hits=hits+?,fails=fails+? WHERE id=?",
                    (current["hits"], current["fails"], other["id"]),
                )
                con.execute(
                    "UPDATE edges SET hits=0,fails=0,status='retired' WHERE id=?",
                    (edge["id"],),
                )
            else:
                con.execute(
                    "UPDATE edges SET code=?,params_json=? WHERE id=?",
                    (result["template"], encoded(result["params"]), edge["id"]),
                )
        total["parameterised_edges"] += 1
    return total
