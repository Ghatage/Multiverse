"""Run the pinned Task008 evaluator with local-container getter adapters.

The scoring implementation stays in the downloaded upstream task file. Only
environment I/O is replaced. Unrelated OSWorld app getters are not imported.
"""

import argparse
import base64
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fork.cu import db, docker
from fork.locks import branch as branch_lock

DATA = ROOT / "data/osworld"
APPS = {"mailhub": 8101, "vaultbank": 8102, "expenseflow": 8103}


def package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


class Environment:
    def __init__(self, branch):
        state = db.get_branch(branch)
        docker.owned(branch, state.container_id)
        self.branch = branch
        self.cache_dir = str(DATA / "evaluations" / branch)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        self.failures = []

    def command(self, args):
        result = subprocess.run(["docker", "exec", f"fork-{self.branch}", *args], capture_output=True, check=True, timeout=60)
        return result.stdout

    def fetch(self, app, path):
        if app not in APPS or not path.startswith("/api/"):
            raise ValueError("Unsupported evaluator endpoint")
        return self.command(["curl", "-fsS", "--max-time", "30", "-H", "Cookie: user_id=osworld-task008", f"http://127.0.0.1:{APPS[app]}{path}"])


def evaluate(branch, vision=False):
    env = Environment(branch)
    upstream = DATA / "upstream"
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if commit != "d578d2d4e0dc82b43e270fdaa7fa89d9708cd154":
        raise ValueError("Unexpected OSWorld code revision")
    task_path = DATA / "tasks/task_008.py"
    expected = json.loads((DATA / "tasks/manifests/task_hashes.json").read_text())["files"]["task_008.py"]["sha256"]
    if hashlib.sha256(task_path.read_bytes()).hexdigest() != expected:
        raise ValueError("Task hash mismatch")
    os.environ["WEBSITE_HOST_SUFFIX"] = "localhost:8080"
    os.environ["OSWORLD_FILE_BASE_URL"] = str(DATA / "assets")
    sys.path.insert(0, str(upstream))
    # Namespace packages avoid importing every optional app's getter dependencies.
    getters = package("desktop_env.evaluators.getters", upstream / "desktop_env/evaluators/getters")
    package("desktop_env.evaluators.metrics", upstream / "desktop_env/evaluators/metrics")

    def cloud_file(_env, config):
        source = Path(config["path"])
        if not source.is_relative_to(DATA / "assets") or not source.is_file():
            raise ValueError("Expected a local pinned evaluator asset")
        dest = Path(env.cache_dir) / config["dest"]
        shutil.copy2(source, dest)
        return str(dest)

    def state_file(_env, config):
        app = urlparse(config["url"]).hostname.split(".")[0]
        data = env.fetch(app, config["file_path"])
        dest = Path(env.cache_dir) / Path(config.get("save_name") or config["file_path"]).name
        dest.write_bytes(data)
        return str(dest)

    getters.get_cloud_file = cloud_file
    getters.get_state_with_cookie = lambda _env, cfg: json.loads(env.fetch(urlparse(cfg["url"]).hostname.split(".")[0], "/api/state"))["state"]
    getters.get_state_file_with_cookie = state_file
    getters.get_vm_command_line = lambda _env, cfg: env.command(cfg["command"]).decode()
    website = importlib.import_module("desktop_env.controllers.website")
    website.build_website_url = lambda app: f"http://{app}.localhost:8080"
    spec = importlib.util.spec_from_file_location("osworld_task008", task_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_generate = module.generate_text

    def generate(*args, **kwargs):
        if not vision:
            env.failures.append("Attachment vision evaluation not enabled")
            raise RuntimeError(env.failures[-1])
        try:
            return original_generate(*args, **kwargs)
        except Exception as exc:
            env.failures.append(f"Vision evaluator failed: {type(exc).__name__}")
            raise

    module.generate_text = generate
    task = module.TASK_CLASS()
    # The base image's X policy is recorded at task setup; never silently assume safe.
    baseline = env.command(["cat", "/state/osworld/xhost-baseline.json"])
    baseline_path = Path(env.cache_dir) / "xhost-baseline.json"
    baseline_path.write_bytes(baseline)
    safety = importlib.import_module("desktop_env.safety.system_config")
    safety._baseline_path = lambda task_id: str(baseline_path)
    with contextlib.redirect_stdout(io.StringIO()) as output:
        result = task.evaluate(env)
    (Path(env.cache_dir) / "evaluator.log").write_text(output.getvalue())
    complete = not env.failures
    report = {"pass": result["score"] >= 0.9999 and complete, "score": result["score"],
              "evaluation_complete": complete, "errors": sorted(set(env.failures)),
              "result": result, "runtime": "adapted-linux-amd64", "release": "v2026.08.08"}
    (Path(env.cache_dir) / "result.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("branch")
    parser.add_argument("--vision", action="store_true", help="Enable paid attachment grading through the configured OpenAI evaluator")
    args = parser.parse_args()
    with branch_lock(args.branch):
        print(json.dumps(evaluate(args.branch, args.vision)))
