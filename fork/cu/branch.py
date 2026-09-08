"""Branch lifecycle operations used by both the CLI and replay engine."""

import fnmatch
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from fork import locks
from fork.cu import db, docker, session
from fork.repl_client import ReplClient

EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def _client(b: db.Branch) -> ReplClient:
    return ReplClient(f"http://localhost:{b.ports['repl']}")


def _result(b: db.Branch, **extra) -> dict:
    return {**asdict(b), **extra}


def _resolve(source: str | None) -> tuple[str, str | None]:
    if source and source.startswith("ck_"):
        ck = db.get_checkpoint(source)
        return ck["image"], ck["id"]
    if source:
        return source, None
    return (
        "fork-main:latest"
        if docker.image_exists("fork-main:latest")
        else "fork-branch:latest"
    ), None


def _start(
    b: db.Branch, timeout: float = 120, desktop_session: dict | None = None
) -> dict:
    start = time.monotonic()
    try:
        cid = (
            docker.run(b.name, b.idx, b.image, b.proxy)
            if desktop_session is None
            else docker.run(b.name, b.idx, b.image, b.proxy, desktop_session)
        )
        db.update(b.name, container_id=cid, status="starting")
        docker.wait_healthy(b.name, timeout)
        report = docker.desktop_report(b.name)
        db.update(b.name, status="healthy")
    except (docker.DockerError, OSError) as exc:
        db.update(b.name, status="error")
        raise docker.DockerError(
            f"{b.name}: {exc}; lease retained, inspect or cu rm {b.name}"
        ) from exc
    extra = {}
    if report is not None:
        extra["desktop_session"] = report
    return _result(
        db.get_branch(b.name), boot_s=round(time.monotonic() - start, 3), **extra
    )


def create(
    name: str,
    source: str | None = None,
    effort: str = "low",
    proxy: bool = False,
    desktop_session: dict | None = None,
) -> dict:
    desktop_session = session.validate(desktop_session)
    locks.validate_name(name)
    if effort not in EFFORTS:
        raise ValueError(f"Invalid effort: {effort}")
    with locks.branch(name):
        image, parent = _resolve(source)
        if not docker.image_exists(image):
            raise ValueError(f"Image does not exist: {image}; run cu base build")
        if desktop_session is not None and not docker.supports_session(image):
            raise ValueError(
                "Image lacks desktop import support; rebuild the runtime image"
            )
        if docker.container(f"fork-{name}") is not None:
            raise ValueError(
                f"Container fork-{name} already exists; no container was modified"
            )
        b = db.reserve(name, image, parent, effort, proxy)
        return _start(b, desktop_session=desktop_session)


def _checkpoint(name: str, label: str | None) -> dict:
    b = db.get_branch(name)
    docker.owned(name, b.container_id)
    with _client(b) as repl:
        sidecar = repl.call("checkpoint", label=label)
    ck_id = "ck_" + secrets.token_hex(4)
    latest = db.latest_checkpoint(name)
    image, commit_ms, size = docker.commit(name, ck_id)
    tabs, variables = docker.read_state(name, "tabs"), docker.read_state(name, "repl")
    values = {
        "id": ck_id,
        "branch": name,
        "image": image,
        "label": label,
        "parent_checkpoint": latest["id"] if latest else b.parent_checkpoint,
        "tabs_json": json.dumps(tabs),
        "vars_json": json.dumps(variables),
        "commit_ms": commit_ms,
        "size_bytes": size,
        "created": db.now(),
    }
    db.insert_checkpoint(values)
    return {
        "id": ck_id,
        "branch": name,
        "image": image,
        "commit_ms": commit_ms,
        "size_bytes": size,
        "tabs": sidecar["tabs"],
    }


def checkpoint(name: str, label: str | None = None) -> dict:
    with locks.branch(name):
        return _checkpoint(name, label)


class ForkError(RuntimeError):
    def __init__(self, branches: list[dict], errors: dict[str, str]):
        self.branches, self.errors = branches, errors
        super().__init__(
            "Some forks failed; successful branches and failed leases are retained: "
            + json.dumps(errors)
        )


def fork(
    name: str,
    n: int = 2,
    source: str | None = None,
    names: list[str] | None = None,
    desktop_session: dict | None = None,
) -> dict:
    desktop_session = session.validate(desktop_session)
    if not 1 <= n <= 99:
        raise ValueError("Fork count must be between 1 and 99")
    names = names if names is not None else [f"{name}-{i}" for i in range(1, n + 1)]
    if len(names) != n or len(set(names)) != n or name in names:
        raise ValueError("Provide exactly n unique child names, excluding the parent")
    for child in names:
        locks.validate_name(child)
    start = time.monotonic()
    with locks.branch(name):
        b = db.get_branch(name)
        active = {branch.name for branch in db.list_branches()}
        if active.intersection(names):
            raise ValueError("A requested child branch already exists")
        ck = db.get_checkpoint(source) if source else db.latest_checkpoint(name)
        if ck is None:
            ck = _checkpoint(name, "auto-fork")
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=n) as pool:
        tasks = {
            pool.submit(
                create, child, ck["id"], b.effort, b.proxy, desktop_session
            ): child
            for child in names
        }
        for task in as_completed(tasks):
            child = tasks[task]
            try:
                results[child] = task.result()
            except (ValueError, RuntimeError, OSError) as exc:
                errors[child] = str(exc)
    branches = [results[child] for child in names if child in results]
    if errors:
        raise ForkError(branches, errors)
    return {
        "branches": branches,
        "checkpoint": ck["id"],
        "total_s": round(time.monotonic() - start, 3),
    }


def rewind(name: str, ck_id: str) -> dict:
    with locks.branch(name):
        b, ck = db.get_branch(name), db.get_checkpoint(ck_id)
        if not docker.image_exists(ck["image"]):
            raise ValueError(f"Checkpoint image missing: {ck['image']}")
        docker.owned(name, b.container_id)
        db.update(name, status="starting")
        try:
            docker.rm(name, b.container_id)
            db.update(
                name, image=ck["image"], parent_checkpoint=ck_id, container_id=None
            )
            return {**_start(db.get_branch(name)), "checkpoint": ck_id}
        except (docker.DockerError, OSError):
            db.update(name, status="error")
            raise


def merge(name: str, into: str = "main") -> dict:
    if into != "main":
        raise ValueError("Only --into main is supported")
    with locks.branch(name):
        db.get_branch(name)
        ck = db.latest_checkpoint(name) or _checkpoint(name, "auto-merge")
        docker.command("tag", ck["image"], "fork-main:latest")
        db.record_merge(name, ck["id"])
        return {"branch": name, "checkpoint": ck["id"], "image": "fork-main:latest"}


def rm(name: str) -> dict:
    with locks.branch(name):
        b = db.get_branch(name, include_removed=True)
        if b.status != "removed":
            docker.rm(name, b.container_id)
            db.removed(name)
        return {"name": name, "status": "removed"}


def ls() -> list[dict]:
    out = []
    for b in db.list_branches():
        # Observe only: do not race lifecycle operations by overwriting their DB state.
        status = docker.inspect_health(b.name)
        latest = db.latest_checkpoint(b.name)
        out.append(
            _result(
                b, status=status, latest_checkpoint=latest["id"] if latest else None
            )
        )
    return out


def wait(name: str, timeout: float = 20) -> dict:
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    with locks.branch(name):
        b = db.get_branch(name)
        docker.owned(name, b.container_id)
        start = time.monotonic()
        try:
            docker.wait_healthy(name, timeout)
        except docker.DockerError:
            db.update(name, status="error")
            raise
        db.update(name, status="healthy")
        return _result(db.get_branch(name), wait_s=round(time.monotonic() - start, 3))


def grid() -> list[dict]:
    return [
        {
            "name": b.name,
            "url": f"http://localhost:{b.ports['novnc']}/vnc.html?autoconnect=1&resize=scale",
        }
        for b in db.list_branches()
    ]


def _ignored(path: str) -> bool:
    if any(
        path == p or path.startswith(p + "/")
        for p in ["/tmp", "/proc", "/dev", "/home/user/.cache"]
    ):
        return True
    return (
        path == "/state/meta.json"
        or fnmatch.fnmatch(path, "/home/user/chrome/**/*.log")
        or (
            path.startswith("/home/user/chrome/")
            and any(
                part.startswith("Cache") or part == "Code Cache"
                for part in path.split("/")
            )
        )
    )


def _snapshot(name: str) -> tuple[set[tuple[str, str]], set[str]]:
    with locks.branch(name):
        b = db.get_branch(name)
        docker.owned(name, b.container_id)
        files = {(kind, path) for kind, path in docker.diff(name) if not _ignored(path)}
        with _client(b) as repl:
            tabs = repl.call(
                "exec_js",
                code="return context.pages().map(p=>p.url()).filter(u=>u!=='about:blank')",
            )["value"]
        return files, set(tabs)


def diff(name: str, other: str | None = None) -> dict:
    files, tabs = _snapshot(name)
    other_files = None
    if other and not other.startswith("ck_"):
        other_files, other_tabs = _snapshot(other)
    else:
        b = db.get_branch(name)
        ck_id = other or b.parent_checkpoint
        other_tabs = (
            {
                t["url"]
                for t in json.loads(db.get_checkpoint(ck_id)["tabs_json"])["tabs"]
            }
            if ck_id
            else set()
        )
    fs = {
        label: sorted(path for kind, path in files if kind == flag)
        for flag, label in [("A", "added"), ("C", "changed"), ("D", "deleted")]
    }
    if other_files is not None:
        fs["only_in_a"] = [
            f"{kind} {path}" for kind, path in sorted(files - other_files)
        ]
        fs["only_in_b"] = [
            f"{kind} {path}" for kind, path in sorted(other_files - files)
        ]
    return {
        "fs": fs,
        "tabs": {
            "added": sorted(tabs - other_tabs),
            "removed": sorted(other_tabs - tabs),
        },
    }
