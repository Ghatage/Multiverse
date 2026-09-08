"""Closed action vocabulary; arbitrary programs cannot masquerade as one action."""

import json
import math
from pathlib import PurePosixPath
from urllib.parse import urlsplit

OPERATIONS = {
    "navigate": {"url"},
    "new_tab": {"url"},
    "pointer_click": {"x", "y"},
    "pointer_drag": {"points"},
    "press_key": {"key"},
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
        if key in {"x", "y"}:
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not 0 <= value < 16384
            ):
                raise ValueError("Pointer coordinates must be finite and nonnegative")
        elif key == "points":
            if not isinstance(value, list) or not 2 <= len(value) <= 256:
                raise ValueError("A drag requires 2 to 256 points")
            for point in value:
                if not isinstance(point, dict) or set(point) != {"x", "y"}:
                    raise ValueError("Each drag point requires x and y")
                validate({"operation": "pointer_click", **point})
        elif key == "checked":
            if type(value) is not bool:
                raise ValueError("checked must be boolean")
        elif not isinstance(value, str):
            raise ValueError("Action arguments must be text")
    if len(json.dumps(action).encode()) > 65536:
        raise ValueError("Action exceeds 64 KiB")
    if "selector" in action and not action["selector"].strip():
        raise ValueError("Empty selector")
    if operation in {"navigate", "new_tab"}:
        url = urlsplit(action["url"])
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError("Navigation requires an HTTP(S) URL")
    if operation == "press_key" and (
        not action["key"].strip() or len(action["key"]) > 80
    ):
        raise ValueError("A key chord must contain 1 to 80 characters")
    if operation == "write_file":
        path = PurePosixPath(action["path"])
        if (
            not path.is_absolute()
            or ".." in path.parts
            or not path.is_relative_to("/home/user")
        ):
            raise ValueError("File writes must stay under /home/user")
    return dict(action)
