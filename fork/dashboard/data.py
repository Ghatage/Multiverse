"""Read-only views over runtime databases and incomplete/live JSONL recordings."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from fork.dashboard.artifacts import Artifacts
from fork.locks import data_dir
from fork.store.signature import normalise

IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def obj(value, default=None):
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value) if value else ({} if default is None else default)
        return (
            decoded
            if isinstance(decoded, dict)
            else ({} if default is None else default)
        )
    except (TypeError, ValueError):
        return {} if default is None else default


def number(value):
    return value if type(value) in (int, float) and abs(value) < 1e18 else None


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Settings:
    data: Path
    runs: Path
    inspect_docker: bool = True
    label: str = "Local workspace"
    synthetic: bool = False

    @classmethod
    def environment(cls):
        return cls(data_dir(), Path(os.environ.get("FORK_RUNS_DIR", "runs")).resolve())


def read_database(path, tables, warnings):
    result = {table: [] for table in tables}
    if not path.is_file():
        return result
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            available = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in tables:
                if table in available:
                    result[table] = [
                        dict(row) for row in con.execute(f'SELECT * FROM "{table}"')
                    ]
                else:
                    warnings.append(f"{path.name}: {table} table unavailable")
        finally:
            con.close()
    except sqlite3.Error:
        warnings.append(f"{path.name} is unavailable; retrying on the next update")
    return result


def agent_active(data: Path, branch: str) -> bool:
    if not re.fullmatch(r"[a-z0-9-]{1,32}", branch):
        return False
    lease = data / "steer" / f"{branch}.active"
    try:
        with lease.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


class Reader:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.payload = None
        self.details = {}
        self.node_details = {}
        self.artifacts = Artifacts((settings.runs, settings.data))
        self.last_checked = 0.0
        self.fingerprint = None
        self.log_cache = {}
        self.runtime_checked = -20.0
        self.runtime = {}
        self.runtime_error = None

    def _files(self):
        files = [
            self.settings.data / n
            for n in ("traj.db", "traj.db-wal", "cu.db", "cu.db-wal")
        ]
        if self.settings.runs.exists():
            for directory in sorted(self.settings.runs.iterdir()):
                if (
                    directory.is_dir()
                    and not directory.is_symlink()
                    and IDENTITY.fullmatch(directory.name)
                ):
                    files.extend(
                        directory / name
                        for name in ("summary.json", "steps.jsonl")
                        if not (directory / name).is_symlink()
                    )
        return files

    def _stamp(self, path):
        try:
            stat = path.stat()
            return (str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), None)

    def _logs(self, path, warnings):
        stamp = self._stamp(path)
        old = self.log_cache.get(str(path))
        if old and old[0] == stamp:
            warnings.extend(old[2])
            return old[1]
        records, problems = [], []
        try:
            with path.open() as handle:
                for index, line in enumerate(handle, 1):
                    if not line.endswith("\n"):
                        problems.append(
                            f"{path.parent.name}: incomplete log tail; waiting for writer"
                        )
                        break
                    try:
                        item = json.loads(line)
                        if (
                            not isinstance(item, dict)
                            or item.get("run_id") != path.parent.name
                        ):
                            raise ValueError("Mismatched log identity")
                        records.append((index, item))
                    except (ValueError, TypeError):
                        problems.append(
                            f"{path.parent.name}: unreadable log record {index}"
                        )
        except (OSError, UnicodeError):
            problems.append(f"{path.parent.name}: log unavailable or invalid text")
        self.log_cache[str(path)] = (stamp, records, problems)
        warnings.extend(problems)
        return records

    def _docker(self):
        if not self.settings.inspect_docker:
            return
        if time.monotonic() - self.runtime_checked < 10:
            return
        self.runtime_checked = time.monotonic()
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "label=fork.branch",
                    "--format",
                    "{{json .}}",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            self.runtime = {}
            for line in result.stdout.splitlines():
                row = json.loads(line)
                state = row.get("State", "unknown")
                status = (
                    "healthy"
                    if state == "running" and "(healthy)" in row.get("Status", "")
                    else state
                )
                if state == "running" and "(unhealthy)" in row.get("Status", ""):
                    status = "error"
                self.runtime[row.get("Names")] = {
                    "status": status,
                    "id": row.get("ID", ""),
                }
            self.runtime_error = None
        except (OSError, ValueError, subprocess.SubprocessError):
            self.runtime = {}
            self.runtime_error = "Docker status unavailable"

    def snapshot(self, *, force=False):
        with self.lock:
            now = time.monotonic()
            if not force and self.payload is not None and now - self.last_checked < 0.7:
                return self.payload
            self.last_checked = now
            self._docker()
            files = self._files()
            fingerprint = digest(
                [
                    *(self._stamp(p) for p in files),
                    self.runtime,
                    self.runtime_error,
                    # Refresh artifact availability and active-agent leases even without log writes.
                    int(now // 5),
                ]
            )
            if (
                not force
                and self.payload is not None
                and fingerprint == self.fingerprint
            ):
                return self.payload
            self.fingerprint = fingerprint
            self.payload = self._build(files)
            return self.payload

    def _build(self, files):
        warnings = []
        if self.runtime_error:
            warnings.append(self.runtime_error)
        traj = read_database(
            self.settings.data / "traj.db",
            ("nodes", "runs", "steps", "actions", "checkpoints"),
            warnings,
        )
        cu = read_database(
            self.settings.data / "cu.db", ("branches", "checkpoints"), warnings
        )
        artifacts = Artifacts(
            (self.settings.runs, self.settings.data), self.artifacts.file_cache
        )
        nodes = {
            n["id"]: {
                "id": n["id"],
                "title": n.get("title_pattern") or n.get("app") or "UI state",
                "url_pattern": n.get("url_pattern", ""),
                "app": n.get("app", ""),
                "signature": n.get("signature"),
                "origin": "store",
            }
            for n in traj["nodes"]
        }
        run_rows = {r["id"]: r for r in traj["runs"]}
        summaries, logs = {}, {}
        events = []
        for path in files:
            if path.name == "summary.json" and path.is_file():
                try:
                    summary = json.loads(path.read_text())
                    if (
                        not isinstance(summary, dict)
                        or summary.get("run_id") != path.parent.name
                    ):
                        raise ValueError("Summary identity mismatch")
                    summaries[path.parent.name] = summary
                except (OSError, ValueError):
                    warnings.append(
                        f"{path.parent.name}: summary is incomplete or invalid"
                    )
            elif path.name == "steps.jsonl" and path.is_file():
                logs[path.parent.name] = self._logs(path, warnings)
        run_ids = set(run_rows) | set(summaries) | set(logs)
        raw_steps = {}
        for run_id, records in logs.items():
            for line, record in records:
                kind = record.get("kind", "event")
                events.append(
                    {
                        "id": f"{run_id}:log:{line}",
                        "run_id": run_id,
                        "branch": record.get("branch"),
                        "kind": kind,
                        "ts": record.get("ts"),
                        "step": record.get("step"),
                        "message": record.get("text")
                        or record.get("ack")
                        or record.get("reason")
                        or record.get("tool")
                        or kind,
                    }
                )
                if kind in {"tool", "tool_start"} and type(record.get("step")) is int:
                    key = (run_id, record["step"])
                    if (
                        key in raw_steps
                        and kind != "tool_start"
                        and raw_steps[key].get("kind") != "tool_start"
                    ):
                        warnings.append(
                            f"{run_id}: repeated record for tool step {record['step']}"
                        )
                    raw_steps[key] = record
        stored_steps = {(s["run_id"], s["idx"]): s for s in traj["steps"]}
        run_ids |= {key[0] for key in stored_steps}
        runs = {}
        for run_id in sorted(run_ids):
            row, summary = run_rows.get(run_id, {}), summaries.get(run_id, {})
            records = [r for _, r in logs.get(run_id, [])]
            first = records[0] if records else {}
            backend = summary.get("backend") or next(
                (r.get("backend") for r in records if r.get("backend")), None
            )
            usage = summary.get("usage_source") or (
                "scripted"
                if backend == "scripted"
                else "api"
                if backend in {"ws", "http"}
                else "unknown"
            )
            model_records = [r for r in records if r.get("kind") == "model"]
            calls = number(summary.get("model_calls"))
            if calls is None:
                calls = (
                    len(model_records)
                    if model_records
                    else number(row.get("model_calls"))
                )
            cost = number(summary.get("cost_usd"))
            if cost is None:
                costs = [number(r.get("cost_usd")) for r in model_records]
                cost = (
                    sum(c for c in costs if c is not None)
                    if costs and all(c is not None for c in costs)
                    else number(row.get("cost_usd"))
                )
            branch = (
                summary.get("branch")
                or row.get("branch")
                or first.get("branch")
                or "unknown"
            )
            finished = bool(summary) or bool(row.get("finished"))
            active = agent_active(self.settings.data, branch)
            runs[run_id] = {
                "id": run_id,
                "branch": branch,
                "task_id": summary.get("task_id")
                or row.get("task_id")
                or "Unrecorded task",
                "mode": summary.get("mode") or row.get("mode") or "unknown",
                "effort": summary.get("effort") or row.get("effort"),
                "started": row.get("started") or first.get("ts"),
                "finished": row.get("finished"),
                "status": "finished"
                if finished
                else "running"
                if active
                else "incomplete",
                "stop_reason": summary.get("stop_reason") or row.get("stop_reason"),
                "checker_pass": obj(summary.get("checker")).get(
                    "pass", row.get("checker_pass")
                ),
                "backend": backend,
                "usage_source": usage,
                "model_calls": calls,
                "estimated_cost_usd": cost,
                "wall_s": number(summary.get("wall_s", row.get("wall_s"))),
                "checkpoint_coverage": row.get(
                    "checkpoint_coverage", "action_checkpoints_unavailable"
                ),
                "synthetic": self.settings.synthetic
                or summary.get("synthetic") is True,
                "step_count": 0,
                "gap_count": 0,
            }
        actions = {}
        for action in traj["actions"]:
            run_id = action["run_id"]
            base = self.settings.runs / run_id
            action = dict(action)
            action["arguments"] = obj(action.pop("arguments_json", None))
            for side in ("pre", "post"):
                proof = obj(action.pop(f"{side}_evidence_json", None))
                action[f"{side}_evidence"] = (
                    [
                        artifacts.register(meta, base=base, label=label)
                        for label, meta in proof.items()
                    ]
                    if isinstance(proof, dict)
                    else []
                )
            actions.setdefault(run_id, []).append(action)
        for items in actions.values():
            items.sort(
                key=lambda a: (a.get("created", ""), a.get("sequence", 0), a["id"])
            )
        details, occurrences = {}, []
        for key in sorted(set(stored_steps) | set(raw_steps)):
            run_id, index = key
            if run_id not in runs:
                continue
            row, raw = stored_steps.get(key, {}), raw_steps.get(key, {})
            base = self.settings.runs / run_id
            proof = obj(row.get("evidence_json"))
            before = artifacts.register(
                raw.get("tree_before") or proof.get("before"),
                base=base,
                label="Before observation",
            )
            after = artifacts.register(
                raw.get("tree_after") or proof.get("after"),
                base=base,
                label="After observation",
            )
            endpoints = []
            for side, descriptor in (("from_node", before), ("to_node", after)):
                node_id = row.get(side)
                if node_id and node_id not in nodes:
                    node_id = None
                if not node_id:
                    tree = artifacts.text(descriptor)
                    if tree:
                        norm = normalise(tree)
                        node_id = (
                            "n_"
                            + hashlib.sha256(
                                (
                                    (norm.app or "desktop") + "\0" + norm.signature
                                ).encode()
                            ).hexdigest()[:12]
                        )
                        nodes.setdefault(
                            node_id,
                            {
                                "id": node_id,
                                "title": norm.title or norm.app or "UI state",
                                "url_pattern": norm.url_pattern,
                                "app": norm.app,
                                "signature": norm.signature,
                                "origin": "recorded_observation",
                            },
                        )
                endpoints.append(node_id)
            error = raw.get("error")
            status = (
                "failed"
                if error
                else "succeeded"
                if row.get("verified") == 1
                else "pending"
                if raw.get("kind") == "tool_start"
                else "unverified"
            )
            # verified=0 means not task-verified, not proof that this individual call failed.
            identifier = f"{run_id}:{index}"
            step = {
                "id": identifier,
                "run_id": run_id,
                "index": index,
                "branch": runs[run_id]["branch"],
                "from": endpoints[0],
                "to": endpoints[1],
                "source": endpoints[0],
                "target": endpoints[1],
                "status": status,
                "verification": "task_checker"
                if row.get("verified") == 1
                else "not_verified",
                "tool": raw.get("tool") or row.get("tool") or "unknown",
                "execution_source": raw.get("source") or row.get("source") or "unknown",
                "ts": raw.get("ts") or row.get("ts"),
                "exec_ms": number(raw.get("exec_ms", row.get("exec_ms"))),
                "model_ms": number(row.get("model_ms")),
                "gap_before": endpoints[0] is None,
                "gap_after": endpoints[1] is None,
                "edge_id": row.get("edge_id"),
                "call_id": raw.get("call_id"),
                "granularity": "tool_call",
            }
            screenshots = raw.get("screenshots") or (
                [raw["screenshot"]] if raw.get("screenshot") else []
            )
            call_actions = [
                a
                for a in actions.get(run_id, [])
                if raw.get("call_id") and a.get("call_id") == raw["call_id"]
            ]
            explicit_actions = set(proof.get("action_ids", []))
            call_actions.extend(
                a
                for a in actions.get(run_id, [])
                if a["id"] in explicit_actions and a not in call_actions
            )
            call_actions.sort(key=lambda a: (a.get("sequence", 0), a["id"]))
            step["action_count"] = len(call_actions)
            details[identifier] = {
                **step,
                "code": raw.get("code", row.get("code", "")),
                "error": error,
                "evidence": [
                    before,
                    after,
                    *[
                        artifacts.register(p, base=base, label=f"Screenshot {i + 1}")
                        for i, p in enumerate(screenshots)
                    ],
                ],
                "actions": call_actions,
                "action_checkpoints": proof.get("action_checkpoints", "unavailable"),
            }
            occurrences.append(step)
            runs[run_id]["step_count"] += 1
            runs[run_id]["gap_count"] += int(any(p is None for p in endpoints))
        occurrences.sort(
            key=lambda s: (
                runs[s["run_id"]].get("started") or "",
                s["run_id"],
                s["index"],
            )
        )
        by_run, by_node = defaultdict(list), defaultdict(list)
        for occurrence in occurrences:
            by_run[occurrence["run_id"]].append(occurrence["id"])
            for node_id in {occurrence["from"], occurrence["to"]} - {None}:
                by_node[node_id].append(occurrence["id"])
        occurrence_lookup = {s["id"]: s for s in occurrences}
        paths = [
            {"run_id": r["id"], "branch": r["branch"], "occurrences": by_run[r["id"]]}
            for r in runs.values()
        ]
        # Display continuity gaps without adding a fabricated connecting edge.
        for path in paths:
            previous = None
            for sid in path["occurrences"]:
                step = details[sid]
                if previous and (
                    step["index"] != previous["index"] + 1
                    or previous["to"] != step["from"]
                ):
                    step["continuity_gap"] = True
                    occurrence_lookup[sid]["continuity_gap"] = True
                previous = step
        checkpoints = {
            c["id"]: {
                **c,
                "metadata_source": "branch_manager",
                "restore_available": False,
            }
            for c in cu["checkpoints"]
        }
        for c in traj["checkpoints"]:
            merged = {
                **checkpoints.get(c["id"], {}),
                **c,
                "metadata_source": "trajectory_store",
                "restore_available": False,
            }
            merged["fidelity"] = obj(merged.pop("fidelity_json", None))
            for key in ("manifest_path", "evidence_json", "tabs_json", "vars_json"):
                merged.pop(key, None)
            checkpoints[c["id"]] = merged
        for ck in checkpoints.values():
            for key in ("tabs_json", "vars_json"):
                ck.pop(key, None)
            ck["restore_reason"] = "Verified recovery API is not implemented"
        branches = []
        for row in cu["branches"]:
            branch = dict(row)
            branch["ports"] = obj(branch.pop("ports_json", None))
            branch["recorded_status"] = branch.get("status")
            if self.settings.inspect_docker and branch["status"] != "removed":
                live = self.runtime.get("fork-" + branch["name"])
                matched = live and (branch.get("container_id") or "").startswith(
                    live["id"]
                )
                branch["status"] = (
                    live["status"]
                    if matched
                    else "unknown"
                    if self.runtime_error
                    else "stopped"
                )
                branch["status_source"] = "docker"
            else:
                branch["status_source"] = "recorded"
            branch["agent_active"] = agent_active(self.settings.data, branch["name"])
            port = branch["ports"].get("novnc")
            branch["desktop_url"] = (
                f"http://localhost:{port}/vnc.html?autoconnect=1&resize=scale&view_only=1&quality=3&compression=6"
                if type(port) is int and 1024 <= port <= 65535
                else None
            )
            parent_ck = checkpoints.get(branch.get("parent_checkpoint"))
            branch["parent_branch"] = parent_ck.get("branch") if parent_ck else None
            branch["ancestry_source"] = (
                "checkpoint_metadata" if parent_ck else "unavailable"
            )
            branch["input_mode"] = "view_only"
            branch["run_ids"] = [
                r["id"] for r in runs.values() if r["branch"] == branch["name"]
            ]
            branches.append(branch)
        branches.sort(key=lambda b: (b.get("status") == "removed", b.get("idx", 0)))
        graph_nodes = [n for nid, n in nodes.items() if nid in by_node]
        for node in graph_nodes:
            node["run_ids"] = sorted(
                {details[sid]["run_id"] for sid in by_node[node["id"]]}
            )
            node["occurrence_count"] = len(by_node[node["id"]])
        run_list = sorted(
            runs.values(), key=lambda r: (r.get("started") or "", r["id"]), reverse=True
        )
        payload = {
            "label": self.settings.label,
            "synthetic": self.settings.synthetic,
            "graph": {
                "nodes": graph_nodes,
                "links": [
                    s for s in occurrences if not s["gap_before"] and not s["gap_after"]
                ],
                "occurrences": occurrences,
                "paths": paths,
                "gap_count": sum(
                    s["gap_before"] or s["gap_after"] for s in occurrences
                ),
            },
            "runs": run_list,
            "branches": branches,
            "checkpoints": list(checkpoints.values()),
            "metrics": self._metrics(run_list, occurrences),
            "events": sorted(events, key=lambda e: (e.get("ts") or "", e["id"])),
            "warnings": sorted(set(warnings)),
            "capabilities": {
                "recovery": False,
                "granularity": "tool_call",
                "steering": True,
            },
        }
        payload["revision"] = digest([payload, details])[:24]
        self.details = details
        self.node_details = {
            n["id"]: {**n, "occurrences": by_node[n["id"]]} for n in graph_nodes
        }
        self.actions = actions
        self.artifacts = artifacts
        return payload

    @staticmethod
    def _metrics(runs, steps):
        groups = {}
        for source in ("api", "scripted", "unknown"):
            selected = [r for r in runs if r["usage_source"] == source]
            calls = [r["model_calls"] for r in selected]
            costs = [r["estimated_cost_usd"] for r in selected]
            groups[source] = {
                "runs": len(selected),
                "model_calls": sum(c for c in calls if c is not None)
                if selected
                else None,
                "estimated_cost_usd": sum(c for c in costs if c is not None)
                if selected
                else None,
                "complete": bool(selected)
                and all(c is not None for c in calls + costs),
            }
        verified = sum(s["status"] == "succeeded" for s in steps)
        return {
            "runs": len(runs),
            "tool_calls": len(steps),
            "verified_calls": verified,
            "failed_calls": sum(s["status"] == "failed" for s in steps),
            "usage": groups,
            "model_calls_saved": sum(
                s["execution_source"] == "replay" and s["status"] == "succeeded"
                for s in steps
            ),
            "repairs": None,
            "cache_hit_rate": None,
        }
