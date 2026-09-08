"""Persistent desktop worker. Stdout is reserved for JSON-lines replies."""

import base64
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

os.environ["DISPLAY"] = ":1"
os.environ["DBUS_SESSION_BUS_ADDRESS"] = Path("/tmp/dbus.addr").read_text().strip()
import pyatspi
import pyautogui

pyautogui.FAILSAFE = True
images = []


def display(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    images.append(base64.b64encode(buf.getvalue()).decode())


def xdo(*args):
    return subprocess.check_output(["xdotool", *map(str, args)], text=True).strip()


def active_window():
    try:
        return xdo("getactivewindow", "getwindowname")
    except subprocess.CalledProcessError:
        return ""


def atspi_tree(max_depth=12, max_chars=20000):
    try:
        pid = int(xdo("getactivewindow", "getwindowpid"))
    except (ValueError, subprocess.CalledProcessError):
        return ""
    lines = []

    def walk(node, depth):
        if depth > max_depth or sum(map(len, lines)) >= max_chars:
            return
        try:
            states = ",".join(
                str(s).removeprefix("STATE_") for s in node.getState().getStates()
            )
            lines.append(" " * depth + f'{node.getRoleName()} "{node.name}" [{states}]')
            for child in node:
                walk(child, depth + 1)
        except Exception as exc:  # noqa: BLE001 - AT-SPI nodes can disappear mid-walk
            lines.append(" " * depth + f"unavailable node: {type(exc).__name__}")

    for app in pyatspi.Registry.getDesktop(0):
        if app.get_process_id() == pid:
            walk(app, 0)
    text = "\n".join(lines)
    if len(text) > max_chars:
        suffix = "\n…(truncated nodes)"
        text = text[: max(0, max_chars - len(suffix))] + suffix
    return text


def inventory():
    """Read every accessible application without changing focus or window state."""
    applications = []
    for app in pyatspi.Registry.getDesktop(0):
        lines = []
        unavailable = []

        def walk(node, depth, lines=lines, unavailable=unavailable):
            if depth > 40 or len(lines) >= 100000:
                unavailable.append("tree_limit")
                return
            try:
                lines.append(" " * depth + f'{node.getRoleName()} "{node.name}"')
                for child in node:
                    walk(child, depth + 1)
            except Exception as exc:  # noqa: BLE001 - disappearing AT-SPI nodes are explicit evidence gaps
                unavailable.append(type(exc).__name__)

        try:
            walk(app, 0)
            applications.append(
                {
                    "name": app.name,
                    "pid": app.get_process_id(),
                    "tree": "\n".join(lines),
                    "complete": not unavailable,
                    "unavailable": unavailable,
                }
            )
        except Exception as exc:  # noqa: BLE001 - disappearing AT-SPI nodes are explicit evidence gaps
            applications.append(
                {"complete": False, "unavailable": [type(exc).__name__]}
            )
    try:
        windows = subprocess.check_output(["wmctrl", "-lpGx"], text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        windows = None
    return {
        "apps": applications,
        "windows": windows,
        "active_window": active_window(),
        "pointer": list(pyautogui.position()),
        "scope": "discoverable_atspi_apps",
        "excluded": ["process_memory", "unsaved_app_state"],
        "captured_at": time.time(),
    }


globals_ = {
    "pyautogui": pyautogui,
    "time": time,
    "subprocess": subprocess,
    "log": print,
    "display": display,
    "xdo": xdo,
    "atspi_tree": atspi_tree,
    "active_window": active_window,
}
reserved = set(globals_) | {"__builtins__"}


def dump_vars():
    out = {}
    for key, value in globals_.items():
        if key in reserved or key.startswith("__"):
            continue
        try:
            json.dumps(value, allow_nan=False)
            out[key] = value
        except (TypeError, ValueError):
            pass
    return out


class Output(io.StringIO):
    def write(self, text):
        sys.__stdout__.write(json.dumps({"id": msg["id"], "stream": text}) + "\n")
        sys.__stdout__.flush()
        return super().write(text)


for line in sys.stdin:
    out = Output()
    images.clear()
    msg = json.loads(line)
    result, error = None, None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            method, params = msg.get("method", "exec"), msg.get("params", {})
            if method == "exec":
                exec(msg["code"], globals_)  # noqa: S102 - this worker is the code-execution sandbox
            elif method == "dump_vars":
                result = dump_vars()
            elif method == "load_vars":
                globals_.update(
                    {
                        k: v
                        for k, v in params.items()
                        if k not in reserved and not k.startswith("__")
                    }
                )
            elif method == "inventory":
                result = inventory()
            elif method == "active_window":
                result = active_window()
            elif method == "atspi_tree":
                result = atspi_tree(**params)
            elif method == "screenshot":
                display(pyautogui.screenshot())
                result = images[-1]
            else:
                raise ValueError("Unknown worker method")
    except Exception as exc:  # noqa: BLE001 - serialize user-code exceptions
        error = {
            "name": type(exc).__name__,
            "message": str(exc),
            "stack": traceback.format_exc(),
        }
    print(
        json.dumps(
            {
                "id": msg["id"],
                "stdout": out.getvalue(),
                "images": images,
                "value": result,
                "error": error,
            }
        ),
        flush=True,
    )
