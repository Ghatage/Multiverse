"""Persist run-derived replay savings separately from edge learning counts."""

import json

from fork.mutations.artifacts import write
from fork.store.db import now


def refresh(store) -> dict:
    metrics = store.metrics()
    with store.connection() as con:
        groups = con.execute(
            "SELECT CASE WHEN repairs>0 THEN 'repair' ELSE mode END AS bucket,"
            "COUNT(*) AS n,SUM(model_calls) AS calls,AVG(wall_s) AS wall,SUM(cost_usd) AS cost "
            "FROM runs WHERE finished IS NOT NULL GROUP BY bucket"
        ).fetchall()
        hits, misses, repairs = con.execute(
            "SELECT COALESCE(SUM(cache_hits),0),COALESCE(SUM(cache_misses),0),COALESCE(SUM(repairs),0) FROM runs"
        ).fetchone()
    result = {
        "runs": {r["bucket"]: r["n"] for r in groups},
        "cache": {
            "nodes": metrics["nodes"],
            "edges": metrics["edges"],
            "hits": hits,
            "fails": misses,
            "hit_rate": hits / (hits + misses) if hits + misses else None,
        },
        "model_calls": {
            **{r["bucket"]: r["calls"] for r in groups},
            "saved": metrics["model_calls_saved"],
        },
        "wall_s": {r["bucket"]: r["wall"] for r in groups},
        "cost_usd": {r["bucket"]: r["cost"] for r in groups},
        "cost_basis": "run accounting; scripted transport usage is synthetic",
        "repairs": repairs,
        "updated": now(),
    }
    write(store.path.parent / "metrics.json", json.dumps(result, indent=2).encode())
    return result
