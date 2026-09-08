"""Local, authenticated receiver for desktop snapshots and browser state."""

import argparse
import base64
import hmac
import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fork.cu import branch, docker
from fork.handoff.task_runner import launch_task, read_state
from fork.locks import data_dir
from fork.repl_client import ReplClient, ReplError

MAX_BODY = 24 * 1024 * 1024
logger = logging.getLogger(__name__)
LIMITATIONS = "Reconstructed browser workspace; native applications, logins, unsaved edits and browser memory are not transferred. Checkpoint preserves browser URLs and guest files; scroll and video state are retained in the handoff manifest but may require reapplying after restart."


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TaskRequest(Model):
    prompt: str = Field(max_length=16000)

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value):
        return value.strip()


class Scroll(Model):
    x: float = Field(ge=0, le=100_000_000)
    y: float = Field(ge=0, le=100_000_000)


class Video(Model):
    current_time: float = Field(ge=0, le=10_000_000)
    paused: bool = True
    playback_rate: float = Field(default=1, ge=0.0625, le=16)


class Tab(Model):
    url: str = Field(max_length=16384)
    title: str | None = Field(default=None, max_length=4096)
    active: bool = False
    window_id: int | None = None
    index: int | None = Field(default=None, ge=0)
    scroll: Scroll | None = None
    video: Video | None = None

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Only http(s) URLs without credentials are supported")
        return value


class Source(Model):
    platform: str = Field(max_length=100)
    display: dict | None = None


class Screenshot(Model):
    data_url: str
    width: int = Field(gt=0, le=32768)
    height: int = Field(gt=0, le=32768)

    @field_validator("data_url")
    @classmethod
    def png(cls, value):
        prefix = "data:image/png;base64,"
        if not value.startswith(prefix):
            raise ValueError("Screenshot must be a PNG data URL")
        try:
            raw = base64.b64decode(value[len(prefix) :], validate=True)
        except ValueError as exc:
            raise ValueError("Invalid screenshot encoding") from exc
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG signature")
        return value


class Handoff(TaskRequest):
    prompt: str = Field(default="", max_length=16000)
    version: Literal[1]
    captured_at: datetime
    source: Source
    screenshot: Screenshot | None = None
    tabs: list[Tab] = Field(default_factory=list, max_length=50)


RESTORE_JS = """
const spec = SPEC;
const p = await context.newPage();
globalThis.__handoffPages = globalThis.__handoffPages || {};
globalThis.__handoffPages[spec.handoff_index] = p;
const result = {url:spec.url,status:'restored',warnings:[]};
try {
  const response = await p.goto(spec.url,{waitUntil:'domcontentloaded',timeout:20000});
  if(response && response.status()>=400) result.warnings.push('HTTP '+response.status());
  result.actual_url = p.url();
  if(spec.scroll) {
    result.scroll = await p.evaluate(s=>{window.scrollTo(s.x,s.y);return {x:window.scrollX,y:window.scrollY};},spec.scroll);
    if(Math.abs(result.scroll.x-spec.scroll.x)>5 || Math.abs(result.scroll.y-spec.scroll.y)>5) result.warnings.push('Scroll position could not be fully restored');
  }
  if(spec.video) {
    result.video = await p.evaluate(async v=>{
      const deadline = performance.now()+6000;
      const wait = () => new Promise(resolve=>setTimeout(resolve,100));
      let el;
      const canSeek = () => el && Array.from({length:el.seekable.length},(_,i)=>[el.seekable.start(i),el.seekable.end(i)]).some(([start,end])=>v.current_time>=start && v.current_time<=end);
      while(performance.now()<deadline) {
        el = [...document.querySelectorAll('video')].sort((a,b)=>b.clientWidth*b.clientHeight-a.clientWidth*a.clientHeight)[0];
        if(el && el.readyState>=2 && canSeek()) break;
        await wait();
      }
      if(!el) return {restored:false,reason:'No accessible video element'};
      if(el.readyState<1) return {restored:false,reason:'Video metadata did not load'};
      if(!canSeek()) return {restored:false,reason:'Requested timestamp is not seekable; media source may lack byte-range support',current_time:el.currentTime,paused:el.paused,playback_rate:el.playbackRate};
      let seeked=false;
      const onSeeked=()=>{seeked=true;};
      el.addEventListener('seeked',onSeeked);
      try {
        el.pause();
        el.playbackRate=v.playback_rate;
        let nextAttempt=0;
        while(performance.now()<deadline) {
          if(performance.now()>=nextAttempt && !el.seeking) {
            el.currentTime=v.current_time;
            nextAttempt=performance.now()+700;
          }
          await wait();
          if(!el.seeking && Math.abs(el.currentTime-v.current_time)<0.5 && (seeked || v.current_time===0)) break;
        }
        const positionRestored=!el.seeking && Math.abs(el.currentTime-v.current_time)<0.5;
        if(!v.paused && positionRestored) await Promise.race([el.play().catch(()=>{}),new Promise(resolve=>setTimeout(resolve,300))]);
        return {restored:positionRestored && el.paused===v.paused && Math.abs(el.playbackRate-v.playback_rate)<0.01,current_time:el.currentTime,paused:el.paused,playback_rate:el.playbackRate};
      } catch(e) {return {restored:false,reason:String(e)};}
      finally {el.removeEventListener('seeked',onSeeked);}
    },spec.video);
    if(!result.video.restored) result.warnings.push('Video position/playback could not be fully restored');
  }
} catch(e) {result.status='failed';result.error=String(e);}
return result;
"""


def restore(payload: Handoff, directory: Path, update):
    name = "handoff-" + directory.name[:12]
    update(branch=name)
    created = branch.create(name, source="fork-branch:latest")
    update(
        viewer_url=f"http://127.0.0.1:{created['ports']['novnc']}/vnc.html?autoconnect=1&resize=scale"
    )
    guest = "/home/user/Desktop/Transferred-desktop"
    docker.command("exec", f"fork-{name}", "mkdir", "-p", guest)
    for filename in ("manifest.json", "desktop.png", "README.txt"):
        if (directory / filename).exists():
            docker.command(
                "cp", str(directory / filename), f"fork-{name}:{guest}/{filename}"
            )
    docker.command(
        "exec", "--user", "root", f"fork-{name}", "chown", "-R", "user:user", guest
    )
    results = []
    with ReplClient(f"http://127.0.0.1:{created['ports']['repl']}") as repl:
        for index, tab in enumerate(payload.tabs):
            try:
                result = repl.call(
                    "exec_js",
                    code=RESTORE_JS.replace(
                        "SPEC", json.dumps({**tab.model_dump(), "handoff_index": index})
                    ),
                    timeout_ms=28000,
                )["value"]
            except (ReplError, ValueError, OSError, KeyError, TypeError) as exc:
                result = {"url": tab.url, "status": "failed", "error": str(exc)}
            results.append(result)
            update(tabs=results)
        active = next((i for i, t in enumerate(payload.tabs) if t.active), 0)
        if results:
            # Keep exact page identity even when an individual call failed to create a tab.
            repl.call(
                "exec_js",
                code=f"const chosen=globalThis.__handoffPages?.[{active}]; if(chosen && !chosen.isClosed()) {{ page=chosen; await page.bringToFront(); }} return page.url();",
            )
    checkpoint = branch.checkpoint(
        name, label="Desktop handoff: reconstructed tabs + source snapshot"
    )
    warnings = [LIMITATIONS]
    for index, result in enumerate(results):
        warnings.extend(
            f"Tab {index + 1}: {warning}" for warning in result.get("warnings", [])
        )
        if result.get("error"):
            warnings.append(f"Tab {index + 1}: {result['error']}")
    if not payload.screenshot:
        warnings.append(
            "No desktop screenshot supplied; only browser metadata was transferred."
        )
    incomplete = not payload.tabs or any(
        r.get("status") != "restored" or r.get("warnings") for r in results
    )
    if not payload.tabs:
        warnings.append(
            "No browser tabs supplied; only the desktop snapshot and manifest were transferred."
        )
    return {
        "status": "partial" if incomplete else "ready",
        "checkpoint_id": checkpoint["id"],
        "tabs": results,
        "warnings": warnings,
    }


def create_app(token: str | None = None, storage: Path | None = None, runtime=restore):
    secret = (
        token if token is not None else os.environ.get("MULTIVERSE_HANDOFF_TOKEN", "")
    )
    if len(secret) < 24:
        raise ValueError("MULTIVERSE_HANDOFF_TOKEN must contain at least 24 characters")
    root = storage or data_dir() / "handoffs"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in root.glob("*/status.json"):
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                continue
        except (ValueError, OSError):
            continue
        if state.get("status") in {"queued", "restoring"}:
            state.update(
                status="failed",
                error="Receiver restarted during transfer; existing branch retained.",
            )
            path.write_text(json.dumps(state))
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="handoff")
    busy = threading.Lock()
    app = FastAPI()

    def auth(request: Request):
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(401, "Invalid bearer token")

    @app.get("/health")
    def health():
        return {"service": "multiverse-handoff", "status": "ok", "version": 1}

    @app.post("/api/handoffs", status_code=202, dependencies=[Depends(auth)])
    async def submit(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise HTTPException(413, "Handoff exceeds 24 MiB")
        try:
            payload = Handoff.model_validate_json(body)
        except ValidationError:
            raise HTTPException(
                422,
                "Invalid handoff: expected version 1, timestamp, source, safe http(s) tabs and optional PNG screenshot",
            )
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "A transfer is already running")
        identifier = uuid.uuid4().hex
        directory = root / identifier
        state = {"id": identifier, "status": "queued", "warnings": []}

        def update(**changes):
            state.update(changes)
            temporary = directory / "status.tmp"
            temporary.write_text(json.dumps(state))
            temporary.replace(directory / "status.json")

        try:
            directory.mkdir(mode=0o700)
            manifest = payload.model_dump(mode="json")
            if payload.screenshot:
                (directory / "desktop.png").write_bytes(
                    base64.b64decode(payload.screenshot.data_url.split(",", 1)[1])
                )
                manifest["screenshot"] = {
                    "file": "desktop.png",
                    "width": payload.screenshot.width,
                    "height": payload.screenshot.height,
                }
            (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))
            (directory / "README.txt").write_text(LIMITATIONS + "\n")
            update()
        except Exception:
            busy.release()
            raise

        def work():
            try:
                update(status="restoring")
                update(**runtime(payload, directory, update))
                if (
                    payload.prompt
                    and state.get("status") in {"ready", "partial"}
                    and state.get("checkpoint_id")
                    and state.get("branch")
                ):
                    try:
                        launch_task(directory, state["branch"], payload.prompt)
                    except Exception:
                        logger.exception("Task launch failed after successful handoff")
            except Exception as exc:
                logger.exception("Desktop handoff job failed")
                update(
                    status="failed",
                    error=str(exc),
                    warnings=[
                        LIMITATIONS,
                        "Any created branch has been retained for inspection.",
                    ],
                )
            finally:
                busy.release()

        executor.submit(work)
        return {"id": identifier, "status": "queued"}

    @app.get("/api/handoffs/{identifier}", dependencies=[Depends(auth)])
    def status(identifier: str):
        if not re.fullmatch("[a-f0-9]{32}", identifier):
            raise HTTPException(404, "Unknown handoff")
        path = root / identifier / "status.json"
        if not path.exists():
            raise HTTPException(404, "Unknown handoff")
        result = json.loads(path.read_text())
        task = read_state(path.parent)
        if task is not None:
            result["task"] = task
        return result

    @app.post(
        "/api/handoffs/{identifier}/task", status_code=202, dependencies=[Depends(auth)]
    )
    async def start_task(identifier: str, request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 128 * 1024:
                raise HTTPException(413, "Task request too large")
        try:
            task = TaskRequest.model_validate_json(body)
        except ValidationError:
            raise HTTPException(422, "Expected a prompt of at most 16000 characters")
        if not task.prompt:
            raise HTTPException(422, "Prompt cannot be empty")
        state = status(identifier)
        if (
            state["status"] not in {"ready", "partial"}
            or not state.get("checkpoint_id")
            or not state.get("branch")
        ):
            raise HTTPException(
                409, "Desktop must finish restoring before starting a task"
            )
        try:
            launched = launch_task(root / identifier, state["branch"], task.prompt)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        except Exception:
            logger.exception("Task worker launch failed")
            raise HTTPException(503, "Unable to launch task worker")
        return {"task": launched}

    @app.post(
        "/api/handoffs/{identifier}/task/cancel",
        status_code=202,
        dependencies=[Depends(auth)],
    )
    def cancel_task(identifier: str):
        state = status(identifier)
        task = state.get("task")
        if task is None:
            raise HTTPException(409, "No task exists for this handoff")
        if task["status"] in {"queued", "running"}:
            (root / identifier / "task.cancel").touch()
            task["cancellation_requested"] = True
        return {"task": task}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17865)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
