"""Small subprocess boundary; no shell strings or Docker SDK."""

import json
import subprocess
import tempfile
import time
from pathlib import Path

from env.scripts.ports import docker_publish_args, port_block


class DockerError(RuntimeError):
    pass


def command(*args: str, timeout: float = 60) -> str:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DockerError(f"Docker {args[0]} failed: {exc}") from exc
    if result.returncode:
        raise DockerError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def container(name: str) -> dict | None:
    try:
        return json.loads(command("container", "inspect", name))[0]
    except DockerError as exc:
        if "No such container" in str(exc) or "No such object" in str(exc):
            return None
        raise


def image_exists(image: str) -> bool:
    try:
        command("image", "inspect", image)
        return True
    except DockerError as exc:
        if "No such image" in str(exc) or "No such object" in str(exc):
            return False
        raise


def image_platform(image: str) -> str:
    platform = command(
        "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image
    )
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise DockerError(f"Unsupported desktop image platform: {platform}")
    return platform


def supports_session(image: str) -> bool:
    return (
        command(
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "fork.desktop-session"}}',
            image,
        )
        == "1"
    )


def run(
    name: str,
    idx: int,
    image: str,
    proxy: bool = False,
    desktop_session: dict | None = None,
) -> str:
    args = [
        "run",
        "-d",
        "--name",
        f"fork-{name}",
        "--platform",
        image_platform(image),
        "--shm-size=1g",
        "--label",
        f"fork.branch={name}",
        "-e",
        f"FORK_BRANCH={name}",
        *docker_publish_args(idx),
    ]
    if proxy:
        args += [
            "-e",
            f"FORK_PROXY=http://host.docker.internal:{port_block(idx)['proxy']}",
        ]
    if desktop_session is None:
        return command(*args, image)
    # Copy into a stopped container, never bind-mount host documents. Each child
    # receives identical captured bytes even if the Mac file subsequently changes.
    cid = command("create", *args[2:], image)
    with tempfile.TemporaryDirectory(prefix="fork-session-") as directory:
        seed = Path(directory) / "desktop-seed.json"
        seed.write_text(json.dumps(desktop_session))
        seed.chmod(0o644)  # Parent directory is private; guest user must read seed.
        command("cp", str(seed), f"{cid}:/state/desktop-seed.json")
    command("start", cid)
    return cid


def owned(name: str, expected_id: str | None = None) -> dict | None:
    info = container(f"fork-{name}")
    if info is not None and (
        info["Config"].get("Labels", {}).get("fork.branch") != name
        or (expected_id and info["Id"] != expected_id)
    ):
        raise DockerError(f"Refusing to modify unowned container fork-{name}")
    return info


def rm(name: str, expected_id: str | None = None) -> None:
    info = owned(name, expected_id)
    if info is not None:
        command("rm", "-f", info["Id"])


def inspect_health(name: str) -> str:
    info = container(f"fork-{name}")
    if info is None:
        return "error"
    state = info["State"]
    if not state["Running"]:
        return "stopped"
    health = state.get("Health", {}).get("Status")
    return {"healthy": "healthy", "starting": "starting", "unhealthy": "error"}.get(
        health, "error"
    )


def wait_healthy(name: str, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = inspect_health(name)
        if state == "healthy":
            return
        if state in {"stopped", "error"}:
            break
        time.sleep(0.1)
    logs = command("logs", "--tail", "20", f"fork-{name}")
    raise DockerError(f"fork-{name} not healthy within {timeout:g}s ({state}): {logs}")


def commit(name: str, ck_id: str) -> tuple[str, int, int]:
    image = f"fork-ckpt:{ck_id}"
    start = time.monotonic()
    command(
        "commit",
        "--pause=true",
        "--change",
        f"LABEL fork.checkpoint={ck_id}",
        f"fork-{name}",
        image,
        timeout=120,
    )
    elapsed = round((time.monotonic() - start) * 1000)
    size = int(command("image", "inspect", "-f", "{{.Size}}", image))
    return image, elapsed, size


def read_state(name: str, file: str) -> dict:
    return json.loads(command("exec", f"fork-{name}", "cat", f"/state/{file}.json"))


def desktop_report(name: str) -> dict | None:
    """Read startup fidelity for fresh imports and inherited checkpoint images."""
    script = (
        "const fs=require('fs');try {"
        "process.stdout.write(fs.readFileSync('/state/desktop-report.json','utf8'));"
        "} catch(e) {if(e.code!=='ENOENT')throw e;process.stdout.write('null');}"
    )
    return json.loads(command("exec", f"fork-{name}", "node", "-e", script))


def diff(name: str) -> list[tuple[str, str]]:
    return [
        tuple(line.split(" ", 1))
        for line in command("diff", f"fork-{name}").splitlines()
        if line
    ]
