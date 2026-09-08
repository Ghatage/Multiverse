"""Closed action vocabulary; arbitrary programs cannot masquerade as one action."""

import json
from pathlib import PurePosixPath
from urllib.parse import urlsplit

OPERATIONS = {
    "navigate": {"url"},
    "fill": {"selector", "value"},
    "click": {"selector"},
    "select": {"selector", "value"},
    "check": {"selector", "checked"},
    "write_file": {"path", "content"},
}


def validate(action: dict) -> dict:
    if not isinstance(action, dict) or action.get("operation") not in OPERATIONS:
        raise ValueError("Unsupported mutation operation")
    operation = action["operation"]
    if set(action) != OPERATIONS[operation] | {"operation"}:
        raise ValueError("Action arguments do not match the operation")
    for key, value in action.items():
        if key == "checked":
            if type(value) is not bool:
                raise ValueError("checked must be boolean")
        elif not isinstance(value, str):
            raise ValueError("Action arguments must be text")
    if len(json.dumps(action).encode()) > 65536:
        raise ValueError("Action exceeds 64 KiB")
    if "selector" in action and not action["selector"].strip():
        raise ValueError("Empty selector")
    if operation == "navigate":
        url = urlsplit(action["url"])
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("Navigation requires an HTTP(S) URL")
    if operation == "write_file":
        path = PurePosixPath(action["path"])
        if (
            not path.is_absolute()
            or ".." in path.parts
            or not path.is_relative_to("/home/user")
        ):
            raise ValueError("File writes must stay under /home/user")
    return dict(action)
