"""Serve only recorded, contained evidence references, never caller-supplied paths."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".json": "text/plain",
}


@dataclass(frozen=True)
class Reference:
    path: Path
    root: Path
    sha256: str
    size: int
    mime: str


def read_reference(ref: Reference) -> bytes:
    resolved = ref.path.resolve(strict=True)
    if not resolved.is_relative_to(ref.root.resolve()) or not resolved.is_file():
        raise ValueError("Evidence is outside its permitted root")
    if resolved.stat().st_size != ref.size or ref.size > MAX_ARTIFACT_BYTES:
        raise ValueError("Evidence size changed or exceeds the display limit")
    with resolved.open("rb") as handle:
        data = handle.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Evidence no longer matches its recorded reference")
    if ref.mime == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Invalid PNG evidence")
    if ref.mime == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Invalid JPEG evidence")
    if ref.mime == "image/webp" and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise ValueError("Invalid WebP evidence")
    return data


class Artifacts:
    def __init__(self, allowed_roots: tuple[Path, ...], file_cache=None):
        self.roots = tuple(p.resolve() for p in allowed_roots)
        self.references: dict[str, Reference] = {}
        self.file_cache = file_cache if file_cache is not None else {}

    def register(self, metadata, *, base: Path, label: str) -> dict:
        result = {"label": label, "available": False}
        if not metadata:
            return {**result, "reason": "Not recorded"}
        if isinstance(metadata, str):
            metadata = {"path": metadata}
        if not isinstance(metadata, dict) or not isinstance(metadata.get("path"), str):
            return {**result, "reason": "Invalid evidence reference"}
        result.update(complete=metadata.get("complete"), scope=metadata.get("scope"))
        try:
            path = Path(metadata["path"])
            path = path if path.is_absolute() else base / path
            path = path.resolve(strict=True)
            # Relative log references cannot escape their run, even into another run.
            if not Path(metadata["path"]).is_absolute() and not path.is_relative_to(
                base.resolve()
            ):
                raise ValueError("Evidence reference escapes its run")
            roots = [root for root in self.roots if path.is_relative_to(root)]
            if not roots or not path.is_file() or path.suffix.lower() not in MIMES:
                raise ValueError("Evidence type or location is not allowed")
            stat = path.stat()
            size = stat.st_size
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, size)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError("Evidence exceeds the 32 MiB display limit")
            cached = self.file_cache.get(str(path))
            sha = (
                cached[1]
                if cached and cached[0] == stamp
                else hashlib.sha256(path.read_bytes()).hexdigest()
            )
            if metadata.get("sha256") is not None and metadata["sha256"] != sha:
                raise ValueError("Evidence hash mismatch")
            if (
                metadata.get("size_bytes") is not None
                and metadata["size_bytes"] != size
            ):
                raise ValueError("Evidence size mismatch")
            ref = Reference(
                path,
                max(roots, key=lambda p: len(p.parts)),
                sha,
                size,
                MIMES[path.suffix.lower()],
            )
            if not cached or cached[0] != stamp:
                read_reference(ref)
                self.file_cache[str(path)] = (stamp, sha)
            token = hashlib.sha256(
                json.dumps([str(path), sha, size]).encode()
            ).hexdigest()
            self.references[token] = ref
            return {
                **result,
                "available": True,
                "url": f"/api/evidence/{token}",
                "sha256": sha,
                "size_bytes": size,
                "mime": ref.mime,
                "integrity": "recorded_hash"
                if metadata.get("sha256")
                else "legacy_reference",
            }
        except (OSError, ValueError, RuntimeError):
            return {
                **result,
                "reason": "Missing, changed, oversized, or disallowed evidence",
            }

    def text(self, descriptor: dict) -> str | None:
        if not descriptor.get("available") or descriptor.get("complete") is not True:
            return None
        try:
            ref = self.references[descriptor["url"].rsplit("/", 1)[-1]]
            return read_reference(ref).decode("utf8")
        except (KeyError, OSError, ValueError, UnicodeError):
            return None
