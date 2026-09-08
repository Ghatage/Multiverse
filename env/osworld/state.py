"""Task 008 local service state; snapshots stay inside the forked filesystem."""

import json
import os
from pathlib import Path
import shutil
import sys
import time
from urllib.request import Request, urlopen

APPS = {"mailhub": 8101, "vaultbank": 8102, "expenseflow": 8103}
COOKIE = "osworld-task008"
STATE = Path("/state/osworld")


def request(app, method="GET", payload=None):
    req = Request(
        f"http://127.0.0.1:{APPS[app]}/api/state",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Cookie": f"user_id={COOKIE}", "Content-Type": "application/json"},
        method=method,
    )
    with urlopen(req, timeout=10) as response:
        return json.load(response)["state"]


def save(payload):
    STATE.mkdir(parents=True, exist_ok=True)
    pending = STATE / "web.json.tmp"
    with pending.open("w") as out:
        json.dump(payload, out)
        out.flush()
        os.fsync(out.fileno())
    pending.replace(STATE / "web.json")


def snapshot():
    # Called by the serialized REPL while the host holds its branch lock.
    payload = {app: request(app) for app in APPS}
    save(payload)
    return {"apps": list(payload), "path": str(STATE / "web.json")}


def restore():
    for app in APPS:
        deadline = time.monotonic() + 45
        while True:
            try:
                request(app)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
    saved = STATE / "web.json"
    first_boot = not saved.exists()
    if first_boot:
        payload = {app: json.loads(Path(f"/opt/osworld/seed/{app}.json").read_text()) for app in APPS}
    else:
        payload = json.loads(saved.read_text())
    for app, state in payload.items():
        restored = request(app, "PUT", state)
        if restored.get("data") != state.get("data", {}):
            raise RuntimeError(f"{app} state restore verification failed")
    if first_boot:
        desktop = Path("/home/user/Desktop")
        desktop.mkdir(parents=True, exist_ok=True)
        shutil.copytree("/opt/osworld/seed/guideline", desktop / "guideline", dirs_exist_ok=True)
        shutil.copy2("/opt/osworld/public/task_008/SF-HK-boarding-pass.png", desktop)
        tabs = [{"url": f"http://{app}.localhost:8080/?cookie={COOKIE}", "active": app == "expenseflow"} for app in APPS]
        Path("/state/tabs.json").write_text(json.dumps({"tabs": tabs}))
    snapshot()


if __name__ == "__main__":
    if sys.argv[1] == "restore":
        restore()
    elif sys.argv[1] == "snapshot":
        print(json.dumps(snapshot()))
    else:
        raise SystemExit("Expected restore or snapshot")
