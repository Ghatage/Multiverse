"""Docker/REPL boundary for the checkpoint coordinator's supported action API."""

import base64
import json
import secrets
import time
from pathlib import Path

from fork import locks
from fork.cu import db, docker
from fork.mutations.artifacts import write
from fork.repl_client import ReplClient


class Desktop:
    def __init__(
        self, branch: str, run_id: str, root: Path, *, deadline: float | None = None
    ):
        self.branch, self.run_id, self.root = branch, run_id, Path(root).resolve()
        state = db.get_branch(branch)
        self.incarnation = state.container_id
        if not self.incarnation:
            raise ValueError("Branch has no running incarnation")
        self.repl = ReplClient(f"http://localhost:{state.ports['repl']}")
        self.token = secrets.token_hex(32)
        self.deadline = deadline
        self.lease = None
        self.files: set[str] = set()
        self.keep_isolated = False
        self.recovery_from: str | None = None

    def assert_current(self) -> None:
        state = db.get_branch(self.branch)
        if state.container_id != self.incarnation or state.status != "healthy":
            raise RuntimeError("Branch incarnation changed during mutation ownership")
        docker.owned(self.branch, self.incarnation)

    def acquire(self) -> None:
        if self.lease is not None:
            return
        self.lease = locks.branch(self.branch)
        self.lease.__enter__()
        try:
            self.assert_current()
            result = self.repl.call(
                "lease_adopt" if self.recovery_from else "lease_begin",
                token=self.token,
                run_id=self.run_id,
                previous_run_id=self.recovery_from,
            )
            if (
                result.get("owned") is not True
                or result.get("input_mode") != "view_only"
            ):
                raise RuntimeError("Desktop did not establish mutation ownership")
        except BaseException:
            self.lease.__exit__(None, None, None)
            self.lease = None
            self.repl.close()
            raise

    def release(self) -> None:
        try:
            self.assert_current()
            self.repl.call(
                "lease_end", token=self.token, keep_isolated=self.keep_isolated
            )
        finally:
            self.repl.close()
            if self.lease:
                self.lease.__exit__(None, None, None)
                self.lease = None

    def evidence(self, label: str) -> dict:
        data = self.repl.call("evidence", paths=sorted(self.files))
        desktop, browser, inventory = (
            data["desktop"],
            data["browser"],
            data["inventory"],
        )
        if not browser.get("tree") or "(truncated " in browser["tree"]:
            raise RuntimeError("Complete browser evidence is unavailable")
        from fork.mutations.external import capture

        inventory["external"] = capture(browser.get("url", ""), self.branch)
        native = "\n\n".join(app.get("tree", "") for app in inventory["apps"])
        inventory["fidelity"] = {
            "overall": "partial",
            "excluded": ["process_memory", "unsaved_app_state"],
            "native_apps": "inventory_only",
            "filesystem": "docker_commit",
        }
        directory = self.root / "boundaries" / label
        png = base64.b64decode(desktop["screenshot"], validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Invalid desktop PNG evidence")
        return {
            "desktop": write(directory / "desktop.png", png),
            "browser_tree": write(directory / "browser.txt", browser["tree"].encode()),
            "native_tree": write(directory / "native.txt", native.encode()),
            "inventory": write(
                directory / "inventory.json", json.dumps(inventory).encode()
            ),
        }

    def checkpoint(
        self, checkpoint_id: str, parent: str | None, evidence: dict
    ) -> dict:
        self.assert_current()
        captured_at = time.time()
        self.repl.call("checkpoint", token=self.token, label=checkpoint_id)
        image, commit_ms, size = docker.commit(self.branch, checkpoint_id)
        identity = json.loads(docker.command("image", "inspect", image))[0]
        inventory = json.loads(Path(evidence["inventory"]["path"]).read_text())
        actual = self.repl.call(
            "recovery_state", token=self.token, paths=sorted(self.files)
        )
        from fork.mutations.external import capture

        active = next(
            (tab for tab in actual["browser_state"]["tabs"] if tab["active"]), None
        )
        actual["external"] = capture(active["url"] if active else "", self.branch)
        if any(
            actual[key] != inventory[key]
            for key in ("files", "browser_state", "external")
        ):
            raise RuntimeError(
                "Concrete recovery state drifted during checkpoint capture"
            )
        checkpoint = {
            "id": checkpoint_id,
            "branch": self.branch,
            "run_id": self.run_id,
            "node_id": None,
            "image": image,
            "image_digest": identity["Id"],
            "platform": identity["Os"] + "/" + identity["Architecture"],
            "incarnation": self.incarnation,
            "parent_id": parent,
            "evidence": evidence,
            "fidelity": {
                "overall": "partial",
                "filesystem": "docker_commit",
                "native_apps": "inventory_only",
                "excluded": [
                    "process_memory",
                    "unsaved_app_state",
                    "mounted_storage",
                    "external_services",
                ],
            },
            "commit_ms": commit_ms,
            "image_size_bytes": size,
            "layers": identity["RootFS"]["Layers"],
            "capture": {
                "started": captured_at,
                "finished": time.time(),
                "drift": "supported_fields_unchanged",
                "atomic_memory_snapshot": False,
            },
        }
        manifest = write(
            self.root / "manifests" / (checkpoint_id + ".json"),
            json.dumps(checkpoint, sort_keys=True).encode(),
        )
        return {
            **checkpoint,
            "manifest_path": manifest["path"],
            "manifest_sha": manifest["sha256"],
            "manifest_size": manifest["size_bytes"],
        }

    def prepare(self, action: dict) -> None:
        if action["operation"] == "write_file":
            self.files.add(action["path"])

    def admit(self, store) -> None:
        from fork.mutations.limits import admit

        admit(store, self, self.run_id)

    def continuous(self, previous: dict, current: dict) -> bool:
        before = json.loads(Path(previous["inventory"]["path"]).read_text())
        after = json.loads(Path(current["inventory"]["path"]).read_text())
        # Registering a new, absent write target changes the manifest vocabulary,
        # not the saved filesystem. Existing newly tracked files need a baseline.
        for name in after["files"].keys() - before["files"].keys():
            before["files"][name] = {"exists": False}
        return all(
            before[key] == after[key] for key in ("files", "browser_state", "external")
        )

    def execute(self, action: dict) -> dict:
        self.assert_current()
        remaining = (
            30 if self.deadline is None else min(30, self.deadline - time.monotonic())
        )
        if remaining <= 0:
            raise TimeoutError("Mutation deadline elapsed before execution")
        return self.repl.call(
            "action",
            token=self.token,
            action=action,
            timeout_ms=max(1, int(remaining * 1000)),
        )
