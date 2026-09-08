"""Durable operator input with one active consumer per desktop."""

import fcntl
import json
import secrets
from datetime import UTC, datetime

from fork.locks import data_dir, validate_name


class SteerChannel:
    def __init__(self, branch: str):
        validate_name(branch)
        self.directory = data_dir() / "steer"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{branch}.jsonl"
        self.lease_path = self.directory / f"{branch}.active"
        self.lease = None
        self.offset = 0

    def __enter__(self):
        self.lease = self.lease_path.open("a")
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            self.lease = None
            raise ValueError("An agent is already running on this branch") from None
        with self.path.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0, 2)
            self.offset = handle.tell()
        return self

    def __exit__(self, *args):
        if self.lease:
            self.lease.close()
            self.lease = None

    @classmethod
    def send(cls, branch: str, text: str) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16384:
            raise ValueError("Steering text must be nonempty and at most 16 KiB")
        channel = cls(branch)
        with channel.lease_path.open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ValueError("No active agent on this branch")
        item = {
            "id": secrets.token_hex(8),
            "ts": datetime.now(UTC).isoformat(),
            "text": text,
        }
        with channel.path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(item) + "\n")
            handle.flush()
        return item

    def read(self) -> list[dict]:
        items = []
        with self.path.open() as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            handle.seek(self.offset)
            while line := handle.readline():
                if not line.endswith("\n"):
                    break
                self.offset = handle.tell()
                try:
                    item = json.loads(line)
                    if isinstance(item.get("text"), str) and item["text"].strip():
                        items.append(item)
                except (ValueError, AttributeError):
                    continue
        return items
