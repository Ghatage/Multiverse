"""Run a task on one branch with HTTP or WebSocket model transport."""

import json
from pathlib import Path
from typing import Annotated

import httpx
import typer
from dotenv import load_dotenv

from fork.agent.loop import load_task, run_task
from fork.agent.policy import Caps, ObservationPolicy
from fork.agent.transport import HttpTransport, WsTransport

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main():
    """Fork computer-use agent."""


@app.command("run")
def run(
    branch: Annotated[str, typer.Option("--branch")],
    task: Annotated[
        Path,
        typer.Option(
            "--task",
            exists=True,
            dir_okay=False,
            help="JSON containing id, prompt, and start_url. Use {{tenant}} for the branch name.",
        ),
    ],
    effort: str = "low",
    max_turns: int = 30,
    max_wall_s: float = 600,
    max_cost_usd: float = 5,
    shots: int = 2,
    transport: str = "ws",
    repl_url: str | None = None,
    seed: int | None = None,
    judge: bool = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    load_dotenv()
    try:
        if transport not in {"ws", "http"}:
            raise ValueError("Transport must be ws or http")
        caps = Caps(max_turns, max_wall_s, max_cost_usd)
        policy = ObservationPolicy(shots_first_n=shots)
        definition = load_task(task, branch)
        # Validate authentication before optional tenant reset. No key is read by offline test transports.
        tx = WsTransport() if transport == "ws" else HttpTransport()
        if seed is not None:
            from fork.agent.transport import live_client

            client = live_client()
            client.close()
            response = httpx.post(f"http://localhost:3000/t/{branch}/reset", timeout=5)
            response.raise_for_status()
        result = run_task(
            branch,
            definition,
            effort,
            caps,
            policy,
            transport=tx,
            repl_url=repl_url,
            seed=seed,
            judge=judge,
            progress=None
            if json_output
            else lambda cost: typer.echo(f"cost so far: ${cost:.4f}", err=True),
        )
        typer.echo(
            json.dumps(result)
            if json_output
            else f"{result['run_id']}: {result['stop_reason']}; "
            f"{result['steps']} tool steps; estimated ${result['cost_usd']:.4f}; checker={result['checker']['pass']}"
        )
        if (
            result["stop_reason"] != "final_answer"
            or result["checker"]["pass"] is False
        ):
            raise typer.Exit(1)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        typer.echo(
            json.dumps({"error": str(exc)}) if json_output else f"Error: {exc}",
            err=not json_output,
        )
        raise typer.Exit(1) from exc


@app.command("steer")
def steer(
    branch: str,
    text: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Add an instruction to the active agent on a desktop."""
    from fork.agent.steer import SteerChannel

    load_dotenv()
    try:
        item = SteerChannel.send(branch, text)
        typer.echo(
            json.dumps({"branch": branch, "status": "queued", **item})
            if json_output
            else f"Instruction queued for {branch}."
        )
    except (ValueError, OSError) as exc:
        typer.echo(
            json.dumps({"error": str(exc)}) if json_output else str(exc),
            err=not json_output,
        )
        raise typer.Exit(1) from exc


@app.command("race")
def race_command(
    checkpoint: Annotated[str, typer.Option("--from")],
    task: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    variants: str = "low,medium",
    stagger: float = 5,
    max_wall: float = 300,
    max_cost_usd: float = 2,
    max_turns: int = 30,
    judge: bool = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
):
    """Fork a checkpoint and report the first independently verified winner."""
    from fork.agent.race import race
    from fork.agent.transport import live_client

    load_dotenv()
    try:
        efforts = [value.strip() for value in variants.split(",")]
        client = live_client()
        client.close()
        result = race(
            checkpoint,
            [{"name": e, "effort": e} for e in efforts],
            task,
            stagger_s=stagger,
            max_wall_s=max_wall,
            max_cost_usd=max_cost_usd,
            max_turns=max_turns,
            judge=judge,
        )
        typer.echo(
            json.dumps(result)
            if json_output
            else f"Winner: {result['winner'] or 'none verified'}; "
            f"{result['elapsed_s']:.1f}s; estimated ${result['cost_usd']:.4f}"
        )
        if not result["winner"]:
            raise typer.Exit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        typer.echo(
            json.dumps({"error": str(exc)}) if json_output else str(exc),
            err=not json_output,
        )
        raise typer.Exit(1) from exc
