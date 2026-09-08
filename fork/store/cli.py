"""Inspect, capture and ingest local trajectory evidence."""

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer
from dotenv import load_dotenv

from fork import locks
from fork.cu import db as branches
from fork.repl_client import ReplClient
from fork.store.db import Store
from fork.store.graph import export_json, export_mermaid
from fork.store.ingest import ingest_run
from fork.store.signature import normalise

app = typer.Typer(no_args_is_help=True)
Json = Annotated[bool, typer.Option("--json")]


@app.callback()
def main(ctx: typer.Context, db: Annotated[Path | None, typer.Option("--db")] = None):
    """Stored UI states, reusable actions and filesystem-checkpoint evidence."""
    load_dotenv()
    ctx.obj = db


def invoke(ctx, json_output, action):
    try:
        result = action(Store(ctx.obj, artifact_root=Path.cwd()))
        typer.echo(json.dumps(result, indent=None if json_output else 2))
    except (ValueError, RuntimeError, OSError, sqlite3.Error, KeyError) as exc:
        typer.echo(
            json.dumps({"error": str(exc)}) if json_output else f"Error: {exc}",
            err=not json_output,
        )
        raise typer.Exit(1) from exc


@app.command("nodes")
def nodes(
    ctx: typer.Context,
    app_name: Annotated[str | None, typer.Option("--app")] = None,
    json_output: Json = False,
):
    invoke(ctx, json_output, lambda store: store.nodes(app_name))


@app.command("edges")
def edges(
    ctx: typer.Context,
    from_node: Annotated[str, typer.Option("--from")],
    status: str = "active",
    json_output: Json = False,
):
    invoke(ctx, json_output, lambda store: store.edges_from(from_node, status))


@app.command("graph")
def graph(
    ctx: typer.Context,
    format: str = "json",
    app_name: Annotated[str | None, typer.Option("--app")] = None,
    json_output: Json = False,
):
    if format == "json":
        invoke(ctx, json_output, lambda store: export_json(store, app_name))
    elif format == "mermaid":
        if json_output:
            invoke(
                ctx, True, lambda store: {"mermaid": export_mermaid(store, app_name)}
            )
        else:
            try:
                typer.echo(export_mermaid(Store(ctx.obj), app_name), nl=False)
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(1) from exc
    else:
        typer.echo(
            json.dumps({"error": "Format must be json or mermaid"})
            if json_output
            else "Error: Format must be json or mermaid",
            err=not json_output,
        )
        raise typer.Exit(1)


@app.command("metrics")
def metrics(ctx: typer.Context, json_output: Json = False):
    invoke(ctx, json_output, lambda store: store.metrics())


@app.command("ingest")
def ingest(
    ctx: typer.Context,
    run_id: str,
    runs_dir: Path = Path("runs"),
    parameterise: bool = False,
    max_cost_usd: float = 0.5,
    json_output: Json = False,
):
    """Import once; --parameterise optionally makes bounded model calls for reusable literals."""

    def action(store):
        if not re.fullmatch(r"r_[a-zA-Z0-9_-]+", run_id):
            raise ValueError("Invalid run ID")
        return ingest_run(
            store,
            runs_dir / run_id,
            template_model=parameterise,
            max_cost_usd=max_cost_usd,
        )

    invoke(ctx, json_output, action)


@app.command("capture")
def capture(
    ctx: typer.Context,
    url: str,
    name: str,
    branch: str | None = None,
    output_dir: Path = Path("env/fixtures/pages"),
    json_output: Json = False,
):
    """Navigate a selected desktop and save a complete browser tree fixture."""

    def action(_):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise ValueError("Invalid fixture name")
        chosen = branch
        if chosen is None:
            match = re.match(r"/t/([^/]+)/", urlsplit(url).path)
            if not match:
                raise ValueError("Provide --branch for this URL")
            chosen = match[1]
        state = branches.get_branch(chosen)
        with (
            locks.branch(chosen),
            ReplClient(f"http://localhost:{state.ports['repl']}") as repl,
        ):
            repl.call("exec_js", code="await page.goto(" + json.dumps(url) + ")")
            observed = repl.call(
                "observe", mode="tree", target="browser", max_tree_chars=2_000_000
            )
        tree = observed.get("tree", "")
        if not tree or "(truncated " in tree:
            raise ValueError("Observation was empty or truncated")
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / (name + ".aria.txt")
        path.write_text(tree + "\n")
        return {
            "path": str(path),
            "signature": normalise(tree).signature,
            "branch": chosen,
        }

    invoke(ctx, json_output, action)
