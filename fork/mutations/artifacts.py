"""Durable, hashed artifacts outside checkpoint images."""

import hashlib
import os
import secrets
from pathlib import Path


def write(path: Path, content: bytes) -> dict:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        descriptor = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
