"""Environment startup: use the window manager's title bar for Chromium."""

import json
from pathlib import Path

path = Path("/home/user/chrome/Default/Preferences")
path.parent.mkdir(parents=True, exist_ok=True)
try:
    settings = json.loads(path.read_text())
except FileNotFoundError:
    settings = {}
settings.setdefault("browser", {})["custom_chrome_frame"] = False
# Fork restores tabs from its checkpoint sidecar after every container boot.
settings.setdefault("profile", {}).update(exit_type="Normal", exited_cleanly=True)
path.write_text(json.dumps(settings))
