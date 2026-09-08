"""Portable startup state. The caller captures apps; guests receive copies, not paths."""

import base64
import binascii
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_BYTES = 8 * 1024 * 1024
EXTENSIONS = {
    "text": {".txt", ".md", ".log", ".json", ".yaml", ".yml", ".py", ".js"},
    "writer": {".rtf", ".doc", ".docx", ".odt"},
    "calc": {".csv", ".xls", ".xlsx", ".ods"},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Window(StrictModel):
    tabs: list[str] = Field(min_length=1, max_length=100)
    active_tab: int = 0

    @model_validator(mode="after")
    def check(self):
        if not 0 <= self.active_tab < len(self.tabs):
            raise ValueError("active_tab must index a tab in this window")
        for url in self.tabs:
            parsed = urlsplit(url)
            if len(url) > 8192 or any(ord(c) < 32 for c in url):
                raise ValueError("Invalid tab URL")
            if url != "about:blank" and (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Only HTTP(S) URLs and about:blank are portable")
        return self


class Document(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    app: Literal["text", "writer", "calc"]
    content_base64: str = Field(max_length=((MAX_BYTES + 2) // 3) * 4)

    @model_validator(mode="after")
    def check(self):
        if self.name in {".", ".."} or any(c in self.name for c in "/\\\0"):
            raise ValueError("Document name must be a filename, not a path")
        if any(ord(c) < 32 for c in self.name):
            raise ValueError("Invalid document filename")
        if Path(self.name).suffix.lower() not in EXTENSIONS[self.app]:
            raise ValueError("Document extension does not match the selected app")
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid document base64") from exc
        if len(content) > MAX_BYTES:
            raise ValueError("Document exceeds 8 MiB")
        if self.app == "text":
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Text documents must be UTF-8") from exc
        return self


class DesktopSession(StrictModel):
    version: Literal[1] = 1
    # None preserves the source image's browser; [] means a blank browser.
    windows: list[Window] | None = Field(default=None, max_length=20)
    active_window: int = 0
    documents: list[Document] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check(self):
        if self.active_window < 0 or self.active_window >= max(
            1, len(self.windows or [])
        ):
            raise ValueError("active_window must index a browser window")
        if sum(len(w.tabs) for w in self.windows or []) > 100:
            raise ValueError("At most 100 browser tabs per desktop")
        if (
            sum(len(base64.b64decode(d.content_base64)) for d in self.documents)
            > MAX_BYTES
        ):
            raise ValueError("Desktop documents exceed 8 MiB total")
        return self


def validate(value: dict | DesktopSession | None) -> dict | None:
    if value is None:
        return None
    # Revalidate models too: do not trust model_construct or later list mutations.
    if isinstance(value, DesktopSession):
        value = value.model_dump()
    try:
        return DesktopSession.model_validate(value).model_dump()
    except ValidationError as exc:
        errors = [
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False)
        ]
        raise ValueError("Invalid desktop session: " + "; ".join(errors)) from exc


def load(path: Path | None) -> dict | None:
    if path is None:
        return None
    if path.stat().st_size > 12 * 1024 * 1024:
        raise ValueError("Desktop session exceeds 12 MiB")
    return validate(json.loads(path.read_text()))


def pack(browser_state: Path | None, documents: list[Path], output: Path) -> dict:
    payload = load(browser_state) or DesktopSession().model_dump()
    for source in documents:
        app = next(
            (a for a, exts in EXTENSIONS.items() if source.suffix.lower() in exts), None
        )
        if not app:
            raise ValueError(f"Unsupported document format: {source.suffix}")
        with source.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError(f"Document exceeds 8 MiB: {source.name}")
        payload["documents"].append(
            {
                "name": source.name,
                "app": app,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    payload = validate(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids overwriting a prior capture or following a symlink.
    with output.open("x") as stream:
        output.chmod(0o600)
        json.dump(payload, stream)
    return {
        "path": str(output.resolve()),
        "windows": len(payload["windows"] or []),
        "documents": [d["name"] for d in payload["documents"]],
    }
