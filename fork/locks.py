"""Process and thread exclusion shared by lifecycle operations and agent tools."""

import fcntl
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def validate_name(name: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]{1,32}", name):
        raise ValueError("Branch names must match [a-z0-9-]{1,32}")


def data_dir() -> Path:
    return Path(os.environ.get("FORK_DATA_DIR", "data")).resolve()


@contextmanager
def branch(name: str) -> Iterator[None]:
    validate_name(name)
    directory = data_dir() / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with _guard:
        lock = _locks.setdefault(str(path), threading.Lock())
    with lock, path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
