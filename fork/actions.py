"""One audited mutation per durable boundary. Callers hold the branch lock.

The RPC returns after each action, so observations and checkpoint control never
re-enter a busy code invocation. Unfinished intents block subsequent mutations.
Filesystem snapshots explicitly do not certify live application memory.
"""

import base64
import hashlib
import json
import os
import secrets
import shutil

from fork.cu import db, docker
from fork.store import actions as journal


class CheckpointFailure(Exception):
    """Fatal: stop the run rather than trying another mutation."""


class ActionCoordinator:
    def __init__(self, branch, repl, store, logger):
        self.branch, self.repl, self.store, self.log = branch, repl, store, logger
        self.owner = secrets.token_hex(24)
        self.ids = []

    def save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        with path.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def capture(self, key):
        value = self.repl.call("action_evidence", owner=self.owner)
        root = self.log.path / "actions" / key
        png = base64.b64decode(value.pop("desktop"), validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Missing full desktop PNG")
        proof = {"desktop": self.save(root / "desktop.png", png)}
        for field in ("browser_tree", "native_tree", "inventory"):
            proof[field] = self.save(root / f"{field}.json", value[field])
            proof[field]["complete"] = (
                all(t.get("complete", False) for t in value[field].get("tabs", []))
                if field == "browser_tree"
                else value[field].get("complete", False)
            )
        return proof, value

    def snapshot(self, proof, value, parent, action_id=None):
        # Control RPC runs outside user code while ownership remains exclusive.
        self.repl.call(
            "checkpoint", owner=self.owner, label=action_id or "action baseline"
        )
        ck_id = "ck_" + secrets.token_hex(4)
        self.log.write(
            kind="checkpoint_pending",
            step=self.log.steps,
            checkpoint_id=ck_id,
            image=f"fork-ckpt:{ck_id}",
            action_id=action_id,
        )
        os.fsync(self.log.file.fileno())
        image, elapsed, size = docker.commit(self.branch, ck_id)
        info = json.loads(docker.command("image", "inspect", image))[0]
        row = {
            "id": ck_id,
            "branch": self.branch,
            "run_id": self.log.run_id,
            "parent_id": parent,
            "image": image,
            "image_digest": info["Id"],
            "platform": info["Os"] + "/" + info["Architecture"],
            "incarnation": self.incarnation,
            "status": "committed",
            "evidence": proof,
            "fidelity": {
                "overall": "partial",
                "filesystem": "captured",
                "live_memory": "unsupported",
                "apps": value["inventory"]["apps"],
                "external_state": "not_captured",
                "atomic": False,
            },
            "action_id": action_id,
        }
        manifest = self.save(
            self.log.path / "actions" / ck_id / "manifest.json",
            {
                **row,
                "commit_ms": elapsed,
                "size_bytes": size,
                "layers": info.get("RootFS", {}).get("Layers", []),
                "capture_ts": value["ts"],
                "settle": value.get(
                    "settle", {"predicate": "baseline", "matched": False}
                ),
            },
        )
        row.update(
            manifest_path=manifest["path"],
            manifest_sha=manifest["sha256"],
            manifest_size=manifest["size_bytes"],
        )
        # Register the immutable image in cu for branch creation. An orphan stays
        # discoverable if trajectory publication fails after the Docker commit.
        db.insert_checkpoint(
            {
                "id": ck_id,
                "branch": self.branch,
                "image": info["Id"],
                "label": action_id or "action baseline",
                "parent_checkpoint": parent,
                "tabs_json": json.dumps({"tabs": value["inventory"]["tabs"]}),
                "vars_json": "{}",
                "commit_ms": elapsed,
                "size_bytes": size,
                "created": db.now(),
            }
        )
        return row

    def execute(self, operation, arguments, call_id, sequence=0):
        b = db.get_branch(self.branch)
        docker.owned(self.branch, b.container_id)
        self.incarnation = b.container_id
        with self.store.connection() as con:
            if con.execute(
                "SELECT 1 FROM actions WHERE branch=? AND incarnation=? AND (checkpoint_status!='committed' OR outcome='effect_unknown')",
                (self.branch, self.incarnation),
            ).fetchone():
                raise CheckpointFailure(
                    "An unfinished or uncertain action needs reconciliation before another mutation"
                )
        # Admission before acquiring input ownership or attempting any mutation.
        if shutil.disk_usage(self.log.path).free < 2 * 1024**3:
            raise CheckpointFailure(
                "Action checkpoint requires at least 2 GiB free host space"
            )
        self.repl.call("action_acquire", owner=self.owner)
        action_id = "a_" + secrets.token_hex(6)
        try:
            # A fresh baseline covers drift between model turns and acquisitions.
            pre, value = self.capture(action_id + "-before")
            latest = db.latest_checkpoint(self.branch)
            baseline = self.snapshot(
                pre, value, latest["id"] if latest else b.parent_checkpoint
            )
            journal.record_checkpoint(self.store, baseline)
            journal.record_intent(
                self.store,
                {
                    "id": action_id,
                    "run_id": self.log.run_id,
                    "branch": self.branch,
                    "incarnation": self.incarnation,
                    "call_id": call_id,
                    "sequence": sequence,
                    "operation": operation,
                    "arguments": arguments,
                    "pre_checkpoint_id": baseline["id"],
                    "pre_evidence": pre,
                },
            )
            result, failure = {}, None
            try:
                result = self.repl.call(
                    "action", owner=self.owner, operation=operation, arguments=arguments
                )
            except Exception as exc:  # noqa: BLE001 - checkpoint even when an action fails unexpectedly
                failure = exc
            # Even an effectful exception must get a post-boundary before returning.
            post, value = self.capture(action_id + "-after")
            value["settle"] = result.get(
                "settle", {"predicate": "none", "matched": False}
            )
            checkpoint = self.snapshot(post, value, baseline["id"], action_id)
            journal.publish(
                self.store,
                action_id,
                checkpoint,
                post,
                "effect_unknown" if failure else "succeeded",
            )
            self.ids.append(action_id)
            self.log.write(
                kind="action",
                step=self.log.steps,
                action_id=action_id,
                call_id=call_id,
                operation=operation,
                checkpoint=checkpoint["id"],
                outcome="effect_unknown" if failure else "succeeded",
            )
            if failure:
                raise failure
            return result
        except Exception as exc:
            raise CheckpointFailure(f"Action {action_id} stopped: {exc}") from exc
        finally:
            # A failed release leaves the guest sealed and fails closed.
            self.repl.call("action_release", owner=self.owner)
