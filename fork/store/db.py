"""Transactional trajectory graph. Matching and concrete recovery proof stay separate."""

import hashlib
import json
import math
import os
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from fork.locks import data_dir
from fork.store.signature import fuzzy_match, normalise


def now() -> str:
    return datetime.now(UTC).isoformat()


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        artifact_root: str | Path | None = None,
        image_verifier: Callable[[dict], bool] | None = None,
    ):
        self._local = threading.local()
        self.path = Path(path) if path is not None else data_dir() / "traj.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root = Path(
            artifact_root or (self.path.parent if path else Path.cwd())
        ).resolve()
        self.image_verifier = image_verifier
        with self.connection() as con:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported trajectory schema version: {version}")
            if (
                version == 0
                and con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()
            ):
                raise ValueError(
                    "Unversioned nonempty trajectory database requires an explicit migration"
                )
            con.executescript(files("fork.store").joinpath("schema.sql").read_text())
            con.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            yield existing
            return
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        self._local.connection = con
        try:
            with con:
                yield con
        finally:
            del self._local.connection
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """An operation stays atomic even if its caller catches a nested failure."""
        with self.connection() as con:
            if not con.in_transaction:
                con.execute("BEGIN IMMEDIATE")
            name = "op_" + secrets.token_hex(8)
            con.execute("SAVEPOINT " + name)
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK TO " + name)
                raise
            finally:
                con.execute("RELEASE " + name)

    def node_for(self, tree: str, app: str | None = None) -> tuple[str, float, bool]:
        """Upsert only an exact node. Use find_node for fuzzy lookup without merging."""
        norm = normalise(tree)
        app = app or norm.app or "desktop"
        node_id = (
            "n_"
            + hashlib.sha256((app + "\0" + norm.signature).encode()).hexdigest()[:12]
        )
        with self.connection() as con:
            con.execute(
                """INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,1)
                           ON CONFLICT(app,signature) DO UPDATE SET hits=hits+1,last_seen=excluded.last_seen""",
                (
                    node_id,
                    norm.signature,
                    encoded(norm.tokens),
                    app,
                    norm.url_pattern,
                    norm.title,
                    tree,
                    now(),
                    now(),
                ),
            )
            row = con.execute(
                "SELECT id FROM nodes WHERE app=? AND signature=?",
                (app, norm.signature),
            ).fetchone()
        return row["id"], 1.0, True

    def find_node(
        self, tree: str, app: str | None = None, threshold: float | None = None
    ) -> tuple[str | None, float, bool]:
        norm = normalise(tree)
        app = app or norm.app or "desktop"
        threshold = (
            float(os.environ.get("FORK_SIG_THRESHOLD", "0.9"))
            if threshold is None
            else threshold
        )
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("Signature threshold must be between zero and one")
        with self.connection() as con:
            rows = con.execute(
                "SELECT * FROM nodes WHERE app=? AND url_pattern=? ORDER BY id",
                (app, norm.url_pattern),
            ).fetchall()
        for row in rows:
            if row["signature"] == norm.signature:
                return row["id"], 1.0, True
        candidates = [
            (fuzzy_match(norm.tokens, json.loads(row["tokens_json"])), row["id"])
            for row in rows
        ]
        score, node = max(candidates, default=(0.0, None))
        return (
            (node if node and score >= threshold and score > 0 else None),
            score,
            False,
        )

    def nodes(self, app: str | None = None) -> list[dict]:
        with self.connection() as con:
            query = (
                "SELECT * FROM nodes" + (" WHERE app=?" if app else "") + " ORDER BY id"
            )
            return [dict(row) for row in con.execute(query, (app,) if app else ())]

    def record_run(
        self,
        run_id: str,
        task_id: str,
        branch: str,
        mode: str = "cold",
        effort: str = "low",
    ) -> None:
        if mode not in {"cold", "warm", "repair"}:
            raise ValueError("Invalid run mode")
        with self.connection() as con:
            old = con.execute(
                "SELECT task_id,branch,mode,effort FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if old and tuple(old) != (task_id, branch, mode, effort):
                raise ValueError("Conflicting run identity")
            con.execute(
                "INSERT OR IGNORE INTO runs(id,task_id,branch,mode,effort,started) VALUES (?,?,?,?,?,?)",
                (run_id, task_id, branch, mode, effort, now()),
            )

    def finish_run(self, run_id: str, **fields) -> None:
        allowed = {
            "finished",
            "stop_reason",
            "model_calls",
            "cache_hits",
            "cache_misses",
            "repairs",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "wall_s",
            "checker_pass",
            "checkpoint_coverage",
            "ingest_sha",
        }
        if not fields.keys() <= allowed:
            raise ValueError("Invalid run fields")
        fields.setdefault("finished", now())
        with self.connection() as con:
            cursor = con.execute(
                "UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                (*fields.values(), run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown run")

    def record_step(
        self,
        run_id: str,
        idx: int,
        from_node: str,
        to_node: str,
        source: str,
        tool: str,
        code: str,
        verified: bool | int | None,
        exec_ms,
        model_ms,
        tokens_in,
        cost_usd,
        edge_id=None,
        *,
        evidence=None,
        ts=None,
    ):
        if source not in {"model", "replay", "repair"}:
            raise ValueError("Invalid step source")
        values = (
            run_id,
            idx,
            from_node,
            to_node,
            edge_id,
            source,
            tool,
            code,
            verified,
            exec_ms,
            model_ms,
            tokens_in,
            cost_usd,
            encoded(evidence or {}),
        )
        with self.connection() as con:
            old = con.execute(
                "SELECT run_id,idx,from_node,to_node,edge_id,source,tool,code,verified,exec_ms,model_ms,tokens_in,cost_usd,evidence_json FROM steps WHERE run_id=? AND idx=?",
                (run_id, idx),
            ).fetchone()
            if old:
                if tuple(old) != values:
                    raise ValueError("Conflicting step publication")
                return
            con.execute(
                "INSERT INTO steps(run_id,idx,from_node,to_node,edge_id,source,tool,code,verified,exec_ms,model_ms,tokens_in,cost_usd,evidence_json,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values, ts or now()),
            )

    def upsert_edge(
        self,
        from_node: str,
        to_node: str,
        tool: str,
        template: str,
        params: dict,
        post_signature: str,
        subgoal="",
        cost_ms=0,
        *,
        action_ids: Sequence[str] = (),
    ) -> str:
        if tool not in {"exec_js", "exec_py"}:
            raise ValueError("Only executable tools become edges")
        from fork.store.actions import link_actions

        with self.transaction() as con:
            nodes = {
                row["id"]: row
                for row in con.execute(
                    "SELECT id,app,signature FROM nodes WHERE id IN (?,?)",
                    (from_node, to_node),
                )
            }
            if (
                from_node not in nodes
                or to_node not in nodes
                or nodes[from_node]["app"] != nodes[to_node]["app"]
            ):
                raise ValueError("An edge requires two nodes in the same app")
            if nodes[to_node]["signature"] != post_signature:
                raise ValueError("Post-signature does not match the destination")
            row = con.execute(
                "SELECT * FROM edges WHERE from_node=? AND to_node=? AND tool=? AND code=?",
                (from_node, to_node, tool, template),
            ).fetchone()
            if row:
                if {
                    k: {field: v for field, v in item.items() if field != "example"}
                    for k, item in json.loads(row["params_json"]).items()
                } != {
                    k: {field: v for field, v in item.items() if field != "example"}
                    for k, item in params.items()
                }:
                    raise ValueError("Conflicting edge parameter metadata")
                edge_id = row["id"]
                con.execute(
                    "UPDATE edges SET hits=hits+1,last_verified=?,cost_ms=? WHERE id=?",
                    (now(), cost_ms, edge_id),
                )
            else:
                edge_id = "e_" + secrets.token_hex(6)
                con.execute(
                    "INSERT INTO edges(id,from_node,to_node,tool,code,params_json,post_signature,subgoal,cost_ms,created,last_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        edge_id,
                        from_node,
                        to_node,
                        tool,
                        template,
                        encoded(params),
                        post_signature,
                        subgoal,
                        cost_ms,
                        now(),
                        now(),
                    ),
                )
            if action_ids:
                link_actions(con, edge_id, action_ids)
            return edge_id

    def edges_from(self, node_id: str, status: str = "active") -> list[dict]:
        with self.connection() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM edges WHERE from_node=? AND status=? ORDER BY hits DESC,fails ASC,last_verified DESC,id",
                    (node_id, status),
                )
            ]

    def mark_edge(self, edge_id: str, ok: bool, exec_ms: int) -> None:
        with self.connection() as con:
            cursor = con.execute(
                "UPDATE edges SET hits=hits+?,fails=fails+?,cost_ms=?,last_verified=CASE WHEN ? THEN ? ELSE last_verified END WHERE id=?",
                (int(ok), int(not ok), exec_ms, ok, now(), edge_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown edge")

    def _status(self, edge_id, status):
        with self.connection() as con:
            if (
                con.execute(
                    "UPDATE edges SET status=? WHERE id=?", (status, edge_id)
                ).rowcount
                != 1
            ):
                raise ValueError("Unknown edge")

    def demote(self, edge_id: str) -> None:
        self._status(edge_id, "demoted")

    def retire(self, edge_id: str) -> None:
        self._status(edge_id, "retired")

    def metrics(self) -> dict:
        with self.connection() as con:
            counts = {
                table: con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in (
                    "nodes",
                    "edges",
                    "runs",
                    "steps",
                    "actions",
                    "checkpoints",
                )
            }
            edge = con.execute(
                "SELECT COALESCE(SUM(hits),0),COALESCE(SUM(fails),0) FROM edges"
            ).fetchone()
            modes = {
                row["mode"]: row["n"]
                for row in con.execute(
                    "SELECT mode,COUNT(*) AS n FROM runs GROUP BY mode"
                )
            }
            walls = {
                row["mode"]: row["wall"]
                for row in con.execute(
                    "SELECT mode,AVG(wall_s) AS wall FROM runs WHERE finished IS NOT NULL GROUP BY mode"
                )
            }
            saved = con.execute(
                "SELECT COUNT(*) FROM steps WHERE source='replay' AND verified=1"
            ).fetchone()[0]
            return {
                **counts,
                "hits": edge[0],
                "fails": edge[1],
                "runs_by_mode": modes,
                "model_calls_saved": saved,
                "avg_cold_wall_s": walls.get("cold"),
                "avg_warm_wall_s": walls.get("warm"),
            }

    def record_checkpoint(
        self, ck_id, branch, node_id, image, run_id=None, step=None, **metadata
    ):
        from fork.store.actions import record_checkpoint

        return record_checkpoint(
            self,
            {
                "id": ck_id,
                "branch": branch,
                "node_id": node_id,
                "image": image,
                "run_id": run_id,
                "step": step,
                **metadata,
            },
        )

    def record_action_intent(self, action: dict) -> None:
        from fork.store.actions import record_intent

        return record_intent(self, action)

    def publish_action_checkpoint(
        self, action_id: str, checkpoint: dict, evidence: dict, outcome: str
    ) -> str:
        from fork.store.actions import publish

        return publish(self, action_id, checkpoint, evidence, outcome)

    def actions_for(self, run_id: str) -> list[dict]:
        with self.connection() as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM actions WHERE run_id=? ORDER BY created,call_id,sequence",
                    (run_id,),
                )
            ]

    def retention_references(self) -> dict:
        with self.connection() as con:
            return {
                "checkpoints": [
                    dict(r)
                    for r in con.execute("SELECT * FROM checkpoints ORDER BY id")
                ],
                "actions": [
                    dict(r)
                    for r in con.execute(
                        "SELECT id,pre_checkpoint_id,post_checkpoint_id,checkpoint_status FROM actions ORDER BY id"
                    )
                ],
                "edge_actions": [
                    dict(r)
                    for r in con.execute(
                        "SELECT * FROM edge_actions ORDER BY edge_id,ordinal"
                    )
                ],
                "candidates": [
                    dict(r)
                    for r in con.execute(
                        "SELECT * FROM checkpoint_candidates ORDER BY id"
                    )
                ],
            }
