"""Durable branch history and transactional port reservations."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from env.scripts.ports import port_block
from fork.locks import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
 name TEXT PRIMARY KEY, idx INTEGER NOT NULL, container_id TEXT, image TEXT NOT NULL,
 parent_checkpoint TEXT REFERENCES checkpoints(id), effort TEXT DEFAULT 'low', proxy INTEGER DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'starting', ports_json TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS active_branch_idx ON branches(idx) WHERE status != 'removed';
CREATE TABLE IF NOT EXISTS checkpoints (
 id TEXT PRIMARY KEY, branch TEXT NOT NULL REFERENCES branches(name), image TEXT NOT NULL,
 label TEXT, parent_checkpoint TEXT, tabs_json TEXT, vars_json TEXT,
 commit_ms INTEGER, size_bytes INTEGER, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS port_leases (
 idx INTEGER PRIMARY KEY, branch TEXT NOT NULL UNIQUE, container_id TEXT, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ck_branch ON checkpoints(branch, created);
CREATE TABLE IF NOT EXISTS merges (
 id INTEGER PRIMARY KEY, branch TEXT NOT NULL REFERENCES branches(name),
 checkpoint TEXT NOT NULL REFERENCES checkpoints(id), created TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(directory / "cu.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    try:
        with con:
            yield con
    finally:
        con.close()


@dataclass
class Branch:
    name: str
    idx: int
    container_id: str | None
    image: str
    parent_checkpoint: str | None
    effort: str
    proxy: bool
    status: str
    ports: dict[str, int]
    created: str
    updated: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Branch":
        values = dict(row)
        values["ports"] = json.loads(values.pop("ports_json"))
        values["proxy"] = bool(values["proxy"])
        return cls(**values)


def get_branch(name: str, *, include_removed: bool = False) -> Branch:
    with connect() as con:
        row = con.execute("SELECT * FROM branches WHERE name=?", (name,)).fetchone()
    if row is None or (row["status"] == "removed" and not include_removed):
        raise ValueError(f"Unknown branch: {name}")
    return Branch.from_row(row)


def list_branches() -> list[Branch]:
    with connect() as con:
        return [
            Branch.from_row(row)
            for row in con.execute(
                "SELECT * FROM branches WHERE status != 'removed' ORDER BY idx"
            )
        ]


def reserve(
    name: str, image: str, parent: str | None, effort: str, proxy: bool
) -> Branch:
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute(
            "SELECT status FROM branches WHERE name=?", (name,)
        ).fetchone()
        if existing and existing["status"] != "removed":
            raise ValueError(f"Branch name already exists in history: {name}")
        used = {row[0] for row in con.execute("SELECT idx FROM port_leases")}
        idx = next((i for i in range(1, 100) if i not in used), None)
        if idx is None:
            raise ValueError("No free branch indices (1–99)")
        ts = now()
        con.execute("INSERT INTO port_leases VALUES (?,?,NULL,?)", (idx, name, ts))
        con.execute(
            """INSERT INTO branches VALUES (?,?,NULL,?,?,?,?,?,?,?,?)
          ON CONFLICT(name) DO UPDATE SET idx=excluded.idx,container_id=NULL,image=excluded.image,
          parent_checkpoint=excluded.parent_checkpoint,effort=excluded.effort,proxy=excluded.proxy,
          status=excluded.status,ports_json=excluded.ports_json,created=excluded.created,updated=excluded.updated""",
            (
                name,
                idx,
                image,
                parent,
                effort,
                int(proxy),
                "starting",
                json.dumps(port_block(idx)),
                ts,
                ts,
            ),
        )
    return get_branch(name)


def update(name: str, **fields) -> None:
    allowed = {"status", "container_id", "image", "parent_checkpoint"}
    if not fields.keys() <= allowed:
        raise ValueError("Invalid branch update")
    fields["updated"] = now()
    with connect() as con:
        con.execute(
            "UPDATE branches SET "
            + ",".join(f"{k}=?" for k in fields)
            + " WHERE name=?",
            (*fields.values(), name),
        )
        if "container_id" in fields:
            con.execute(
                "UPDATE port_leases SET container_id=? WHERE branch=?",
                (fields["container_id"], name),
            )


def removed(name: str) -> None:
    with connect() as con:
        con.execute(
            "UPDATE branches SET status='removed', updated=? WHERE name=?",
            (now(), name),
        )
        con.execute("DELETE FROM port_leases WHERE branch=?", (name,))


def get_checkpoint(ck_id: str) -> dict:
    with connect() as con:
        row = con.execute("SELECT * FROM checkpoints WHERE id=?", (ck_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown checkpoint: {ck_id}")
    return dict(row)


def latest_checkpoint(name: str) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM checkpoints WHERE branch=? AND created >= (SELECT created FROM branches WHERE name=?) ORDER BY created DESC, rowid DESC LIMIT 1",
            (name, name),
        ).fetchone()
    return dict(row) if row else None


def insert_checkpoint(values: dict) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO checkpoints ("
            + ",".join(values)
            + ") VALUES ("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )


def record_merge(name: str, ck_id: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO merges(branch,checkpoint,created) VALUES (?,?,?)",
            (name, ck_id, now()),
        )
