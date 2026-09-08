"""Human and JSON entry points for the local branch manager."""

import json
import os
import subprocess
from collections.abc import Callable
from typing import Annotated, Any

import httpx
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from fork.cu import branch as manager
from fork.cu import db

load_dotenv()
app = typer.Typer(no_args_is_help=True)
branch_app = typer.Typer(no_args_is_help=True)
base_app = typer.Typer(no_args_is_help=True)
app.add_typer(branch_app, name="branch")
app.add_typer(base_app, name="base")
Json = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON only.")]


def output(value: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(value))
        return
    rows = value if isinstance(value, list) else [value]
    if not rows:
        typer.echo("No branches.")
        return
    table = Table()
    keys = (
        [
            "name",
            "idx",
            "status",
            "image",
            "parent_checkpoint",
            "latest_checkpoint",
            "ports",
        ]
        if "ports" in rows[0]
        else list(rows[0])
    )
    for key in keys:
        table.add_column(key)
    for row in rows:
        table.add_row(
            *(
                json.dumps(row.get(k))
                if isinstance(row.get(k), (dict, list))
                else str(row.get(k, ""))
                for k in keys
            )
        )
    Console().print(table)


def invoke(as_json: bool, action: Callable, *args, **kwargs) -> None:
    try:
        result = action(*args, **kwargs)
    except (ValueError, RuntimeError, OSError) as exc:
        payload = {"error": str(exc)}
        if isinstance(exc, manager.ForkError):
            payload.update(branches=exc.branches, errors=exc.errors)
        if as_json:
            typer.echo(json.dumps(payload))
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    output(result, as_json)


@base_app.command("build")
def base_build(json_output: Json = False):
    def build():
        result = subprocess.run(
            ["make", "branch-build"], text=True, capture_output=True, check=False
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
        return {"image": "fork-branch:latest", "built": True}

    invoke(json_output, build)


@branch_app.command("create")
def create(
    name: str,
    source: Annotated[str | None, typer.Option("--from")] = None,
    effort: str = "low",
    proxy: bool = False,
    json_output: Json = False,
):
    invoke(json_output, manager.create, name, source, effort, proxy)


@app.command("checkpoint")
def checkpoint(name: str, label: str | None = None, json_output: Json = False):
    invoke(json_output, manager.checkpoint, name, label)


@app.command("fork")
def fork(
    name: str,
    n: Annotated[int | None, typer.Option("--n", min=1, max=99)] = None,
    source: Annotated[str | None, typer.Option("--from")] = None,
    names: str | None = None,
    json_output: Json = False,
):
    def run():
        width = n if n is not None else int(os.environ.get("FORK_MAX_BRANCHES", "2"))
        return manager.fork(
            name, width, source, names.split(",") if names is not None else None
        )

    invoke(json_output, run)


@app.command("rewind")
def rewind(name: str, checkpoint: str, json_output: Json = False):
    invoke(json_output, manager.rewind, name, checkpoint)


@app.command("diff")
def diff(
    name: str,
    other: Annotated[str | None, typer.Argument()] = None,
    json_output: Json = False,
):
    invoke(json_output, manager.diff, name, other)


@app.command("merge")
def merge(name: str, into: str = "main", json_output: Json = False):
    invoke(json_output, manager.merge, name, into)


@app.command("ls")
def ls(json_output: Json = False):
    invoke(json_output, manager.ls)


@app.command("rm")
def rm(
    name: Annotated[str | None, typer.Argument()] = None,
    all_branches: Annotated[bool, typer.Option("--all")] = False,
    json_output: Json = False,
):
    def run():
        if bool(name) == all_branches:
            raise ValueError("Choose one branch name or --all")
        if all_branches:
            return [manager.rm(b.name) for b in db.list_branches()]
        return manager.rm(name)

    invoke(json_output, run)


@app.command("ports")
def ports(name: str, json_output: Json = False):
    invoke(json_output, lambda: db.get_branch(name).ports)


@app.command("wait")
def wait(name: str, timeout: float = 20, json_output: Json = False):
    invoke(json_output, manager.wait, name, timeout)


@app.command("grid")
def grid(json_output: Json = False):
    def run():
        rows = manager.grid()
        if not json_output:
            try:
                if httpx.get("http://localhost:8000/health", timeout=1).is_success:
                    subprocess.run(
                        ["open", "http://localhost:8000/"],
                        check=False,
                        capture_output=True,
                    )
            except httpx.HTTPError:
                pass
        return rows

    invoke(json_output, run)
