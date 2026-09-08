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
    lines, apps, complete = [], [], True
    count = 0

    def walk(node, depth):
        nonlocal count, complete
        count += 1
        if count > 50000 or depth > 80:
            complete = False
            return
        try:
            lines.append(" " * depth + f'{node.getRoleName()} "{node.name}"')
            for child in node:
                walk(child, depth + 1)
        except Exception as exc:  # noqa: BLE001 - AT-SPI nodes can disappear
            complete = False
            lines.append(f"unavailable: {type(exc).__name__}")

    for app in pyatspi.Registry.getDesktop(0):
        try:
            apps.append(
                {
                    "name": app.name,
                    "pid": app.get_process_id(),
                    "restore": "unsupported",
                }
            )
            walk(app, 0)
        except Exception:  # noqa: BLE001 - inventory records inaccessible apps
            complete = False
    try:
        windows = subprocess.check_output(["wmctrl", "-lpG"], text=True)
    except (OSError, subprocess.CalledProcessError):
        windows = None
        complete = False
    return {
        "tree": {"text": "\n".join(lines), "complete": complete},
        "inventory": {
            "apps": apps,
            "windows": windows,
            "focus": active_window(),
            "pointer": list(pyautogui.position()),
            "complete": complete,
        },
    }


def action(value):
    p, op = value["arguments"], value["operation"]
    if op == "desktop_click":
        pyautogui.click(p["x"], p["y"])
    elif op == "desktop_type":
        pyautogui.write(p["text"], interval=0.01)
    elif op == "desktop_press":
        pyautogui.press(p["key"])
    elif op == "desktop_hotkey":
        pyautogui.hotkey(*p["keys"])
    elif op == "desktop_scroll":
        pyautogui.scroll(p["amount"])
    else:
        raise ValueError("Unsupported desktop action")
    return {"operation": op}


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
            elif method == "active_window":
                result = active_window()
            elif method == "atspi_tree":
                result = atspi_tree(**params)
            elif method == "inventory":
                result = inventory()
            elif method == "action":
                result = action(params)
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
