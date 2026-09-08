"""Download pinned task 008 inputs and build a self-contained, forkable desktop."""

import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote
import zipfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/osworld"
RELEASE = "v2026.08.08"
WEB_COMMIT = "90ec2218f7747b15fe5117cdbe59b8978446ab9c"
APPS = ("mailhub", "vaultbank", "expenseflow")
ASSET_PREFIX = "https://huggingface.co/datasets/xlangai/osworld_v2_assets/resolve/main/"


def run(*args, cwd=ROOT, **kwargs):
    return subprocess.run(args, cwd=cwd, check=True, text=True, **kwargs)


def download(repo, *files, destination):
    run("hf", "download", repo, *files, "--repo-type", "dataset", "--revision", RELEASE, "--local-dir", str(destination))


def prepare():
    DATA.mkdir(parents=True, exist_ok=True)
    upstream = DATA / "upstream"
    if not upstream.exists():
        run("git", "clone", "--depth", "1", "--branch", RELEASE, "https://github.com/xlang-ai/OSWorld-V2.git", str(upstream))
    tasks, assets, web = DATA / "tasks", DATA / "assets", DATA / "web"
    download("xlangai/osworld_v2_tasks", "task_008.py", "manifests/task_hashes.json", destination=tasks)
    task_bytes = (tasks / "task_008.py").read_bytes()
    expected = json.loads((tasks / "manifests/task_hashes.json").read_text())["files"]["task_008.py"]["sha256"]
    task_hash = hashlib.sha256(task_bytes).hexdigest()
    if task_hash != expected:
        raise RuntimeError("Task 008 does not match its release hash manifest")
    download("xlangai/osworld_v2_assets_gated", "--include", "task_008/**", destination=assets)
    refs = set()
    for filename in ("state_1.json", "state_2.json"):
        content = (assets / "task_008" / filename).read_text()
        refs.update(unquote(p) for p in re.findall(re.escape(ASSET_PREFIX) + r'''([^\s"<>\\')]+)''', content))
    if refs:
        download("xlangai/osworld_v2_assets_gated", *sorted(refs), destination=assets)
    if not web.exists():
        run("git", "clone", "--depth", "1", "--branch", RELEASE, "https://github.com/Task-Web/OSWorld-web.git", str(web))
    actual = run("git", "rev-parse", "HEAD", cwd=web, capture_output=True).stdout.strip()
    if actual != WEB_COMMIT:
        raise RuntimeError(f"Website checkout must be {WEB_COMMIT}; found {actual}")
    run("git", "submodule", "update", "--init", "--depth", "1", *(f"{a}_web" for a in APPS), cwd=web)
    build = DATA / "build"
    if build.exists():
        shutil.rmtree(build)  # Only the generated context owned by this script.
    (build / "seed").mkdir(parents=True)
    versions = {}
    for app in APPS:
        source = web / f"{app}_web"
        versions[app] = run("git", "rev-parse", "HEAD", cwd=source, capture_output=True).stdout.strip()
        for directory in ("backend", "frontend"):
            shutil.copytree(source / directory, build / "web" / app / directory,
                            ignore=shutil.ignore_patterns("node_modules", ".venv", "__pycache__", "dist", "files", ".env"))
        with (build / "web" / app / "backend/requirements.lock").open("w") as output:
            run("uv", "export", "--project", str(source / "backend"), "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt", stdout=output)
    public = refs | {"task_008/SF-HK-boarding-pass.png"}
    for relative in sorted(public):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Invalid asset path")
        destination = build / "public" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets / relative, destination)
    for app, name in (("mailhub", "state_1.json"), ("vaultbank", "state_2.json")):
        content = (assets / "task_008" / name).read_text().replace(ASSET_PREFIX, "http://assets.localhost:8080/")
        (build / "seed" / f"{app}.json").write_text(content)
    (build / "seed/expenseflow.json").write_text("{}")
    with zipfile.ZipFile(assets / "task_008/guideline.zip") as archive:
        for member in archive.namelist():
            if Path(member).is_absolute() or ".." in Path(member).parts:
                raise ValueError("Unsafe guideline archive member")
        archive.extractall(build / "seed")
    instruction = next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(task_bytes))
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "instruction" for t in node.targets))
    manifest = {"task": "008", "release": RELEASE, "task_sha256": task_hash,
                "web_commit": WEB_COMMIT, "web_apps": versions,
                "adaptations": ["Debian x86-64", "DISPLAY=:1", "Chromium", "local per-desktop websites", "local asset URLs", "saved-file restore only"],
                "assets": {p: hashlib.sha256((assets / p).read_bytes()).hexdigest() for p in sorted(public)}}
    (build / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (DATA / "task008.json").write_text(json.dumps({"id": "osworld-v2-008", "prompt": instruction,
        "start_url": "http://expenseflow.localhost:8080/", "release": RELEASE, "osworld_task": "008"}, indent=2))
    return build


if __name__ == "__main__":
    build = prepare()
    run("make", "branch-build")
    image = f"fork-osworld008:build-{uuid4().hex[:12]}"
    run("docker", "buildx", "build", "--platform", "linux/amd64", "--load", "--build-context", f"osworld={build}",
        "-f", "env/osworld/Dockerfile", "-t", image, "-t", "fork-osworld008:latest", ".")
    print(f"Built {image}. Use this immutable tag for branches; keep it while checkpoints depend on it. Use fork-agent run --resume.")
