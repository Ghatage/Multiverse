"""Serve the installed dashboard against local runtime recordings."""

import json
import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from dotenv import load_dotenv

from fork.dashboard.api import create_app
from fork.dashboard.data import Settings

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main():
    """Explore recorded executions without running or restoring them."""


@app.command()
def serve(
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8000,
    host: Annotated[str, typer.Option(help="Loopback address only.")] = "127.0.0.1",
    data: Annotated[Path | None, typer.Option("--data-dir")] = None,
    runs: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    load_dotenv()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        typer.echo(
            json.dumps({"error": "Dashboard must bind to a loopback address"})
            if json_output
            else "Dashboard must bind to a loopback address",
            err=True,
        )
        raise typer.Exit(1)
    if data is not None:
        os.environ["FORK_DATA_DIR"] = str(data.resolve())
    if runs is not None:
        os.environ["FORK_RUNS_DIR"] = str(runs.resolve())
    url = f"http://{'[' + host + ']' if ':' in host else host}:{port}"
    typer.echo(
        json.dumps({"status": "starting", "url": url})
        if json_output
        else f"Fork dashboard · {url}"
    )
    uvicorn.run(
        create_app(Settings.environment()),
        host=host,
        port=port,
        access_log=not json_output,
        log_level="warning" if json_output else "info",
    )
