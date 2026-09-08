"""Dashboard endpoints; graph/timeline reads cannot execute or restore a desktop."""

import asyncio
import json
from importlib.resources import files
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fork.agent.steer import SteerChannel
from fork.dashboard.artifacts import read_reference
from fork.dashboard.data import Reader, Settings, digest
from fork.dashboard.startup import router as startup_router
from fork.locks import data_dir


def filtered(payload, run_id=None, branch=None):
    if not run_id and not branch:
        return payload
    runs = [
        r
        for r in payload["runs"]
        if (not run_id or r["id"] == run_id) and (not branch or r["branch"] == branch)
    ]
    ids = {r["id"] for r in runs}
    occurrences = [s for s in payload["graph"]["occurrences"] if s["run_id"] in ids]
    nodes = {s[k] for s in occurrences for k in ("from", "to") if s[k]}
    graph = {
        "nodes": [n for n in payload["graph"]["nodes"] if n["id"] in nodes],
        "links": [s for s in occurrences if s["from"] and s["to"]],
        "occurrences": occurrences,
        "paths": [p for p in payload["graph"]["paths"] if p["run_id"] in ids],
        "gap_count": sum(s["gap_before"] or s["gap_after"] for s in occurrences),
    }
    return {
        **payload,
        "graph": graph,
        "runs": runs,
        "events": [e for e in payload["events"] if e["run_id"] in ids],
        "metrics": Reader._metrics(runs, occurrences),
        "revision": digest([payload["revision"], run_id, branch])[:24],
    }


class Steering(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16384)


def create_app(settings=None):
    settings = settings or Settings.environment()
    reader = Reader(settings)
    app = FastAPI(title="Fork execution explorer", docs_url=None, redoc_url=None)
    app.state.reader = reader
    # Lifecycle writes must target the same workspace the dashboard displays.
    if settings.data.resolve() == data_dir().resolve() and not settings.synthetic:
        app.include_router(startup_router())
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )
    assets = files("dashboard")

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-fork-dashboard") != "1" or (
                origin and urlsplit(origin).netloc != request.headers.get("host")
            ):
                return Response(
                    "Same-origin dashboard request required", status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "frame-src http://localhost:* http://127.0.0.1:*; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(str(assets / "index.html"))

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": "fork-dashboard",
            "recovery_available": False,
        }

    @app.get("/api/snapshot")
    def snapshot(
        request: Request,
        run_id: str | None = None,
        branch: str | None = None,
        after: str | None = None,
    ):
        payload = filtered(reader.snapshot(), run_id, branch)
        if (
            after == payload["revision"]
            or request.headers.get("if-none-match") == f'"{payload["revision"]}"'
        ):
            return Response(status_code=304)
        return Response(
            json.dumps(payload),
            media_type="application/json",
            headers={"ETag": f'"{payload["revision"]}"'},
        )

    @app.get("/api/graph")
    def graph(run_id: str | None = None, branch: str | None = None):
        return filtered(reader.snapshot(), run_id, branch)["graph"]

    @app.get("/api/branches")
    def branches():
        return reader.snapshot()["branches"]

    @app.get("/api/metrics")
    def metrics(run_id: str | None = None, branch: str | None = None):
        return filtered(reader.snapshot(), run_id, branch)["metrics"]

    @app.get("/api/runs")
    def runs(branch: str | None = None):
        return filtered(reader.snapshot(), branch=branch)["runs"]

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        payload = reader.snapshot()
        found = next((r for r in payload["runs"] if r["id"] == run_id), None)
        if not found:
            raise HTTPException(404, "Run not found")
        return {
            **found,
            "steps": [
                s for s in payload["graph"]["occurrences"] if s["run_id"] == run_id
            ],
        }

    @app.get("/api/runs/{run_id}/steps")
    def steps(run_id: str):
        return run(run_id)["steps"]

    @app.get("/api/runs/{run_id}/steps/{index}")
    def step(run_id: str, index: int):
        with reader.lock:
            reader.snapshot()
            found = reader.details.get(f"{run_id}:{index}")
            if not found:
                raise HTTPException(404, "Tool call not found")
            return found

    @app.get("/api/runs/{run_id}/actions")
    def actions(run_id: str):
        with reader.lock:
            run(run_id)
            return {
                "actions": reader.actions.get(run_id, []),
                "recovery_available": False,
            }

    @app.get("/api/nodes/{node_id}")
    def node(node_id: str):
        with reader.lock:
            reader.snapshot()
            found = reader.node_details.get(node_id)
            if not found:
                raise HTTPException(404, "Recorded state not found")
            return found

    @app.get("/api/checkpoints")
    def checkpoints(branch: str | None = None):
        return [
            c
            for c in reader.snapshot()["checkpoints"]
            if not branch or c["branch"] == branch
        ]

    @app.get("/api/evidence/{token}")
    def evidence(token: str):
        with reader.lock:
            reader.snapshot()
            ref = reader.artifacts.references.get(token)
        if not ref:
            raise HTTPException(404, "Evidence reference not found")
        try:
            content = read_reference(ref)
        except (ValueError, OSError):
            raise HTTPException(
                410, "Recorded evidence is unavailable or changed"
            ) from None
        return Response(
            content,
            media_type=ref.mime,
            headers={"Content-Disposition": "inline; filename=evidence"},
        )

    @app.post("/api/steer/{branch}")
    def steer(branch: str, body: Steering):
        if settings.data.resolve() != data_dir():
            raise HTTPException(
                409, "Steering is unavailable for this alternate recording directory"
            )
        found = next(
            (
                b
                for b in reader.snapshot()["branches"]
                if b["name"] == branch and b["status"] != "removed"
            ),
            None,
        )
        if not found:
            raise HTTPException(404, "Active branch not found")
        try:
            item = SteerChannel.send(branch, body.text)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except OSError:
            raise HTTPException(503, "Steering channel unavailable") from None
        return {"queued": True, **item}

    @app.get("/api/history")
    def history(run_id: str | None = None, branch: str | None = None):
        return filtered(reader.snapshot(), run_id, branch)["events"]

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            last = request.headers.get("last-event-id") or request.query_params.get(
                "after"
            )
            yield "retry: 2500\n\n"
            while not await request.is_disconnected():
                payload = await asyncio.to_thread(reader.snapshot)
                if payload["revision"] != last:
                    last = payload["revision"]
                    yield f"id: {last}\nevent: revision\ndata: {json.dumps({'revision': last})}\n\n"
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    return app
