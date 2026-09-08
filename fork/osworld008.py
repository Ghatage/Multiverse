"""Host-only entry point for task 008's independent evaluator."""

import json
from pathlib import Path
import subprocess


def check(branch: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["uv", "run", "--no-dev", "--with", "requests", "--with", "pillow",
         "python", "scripts/evaluate_osworld008.py", branch],
        cwd=root, capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        return {"pass": False, "evaluation_complete": False,
                "errors": ["OSWorld evaluator failed: " + result.stderr[-1000:]]}
    return json.loads(result.stdout)
