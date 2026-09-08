"""Literal-only templates guarded by exact reconstruction of the original code."""

import json
import os
import re

from fork.agent.cost import Tokens, estimate_usd
from fork.agent.transport import HttpTransport

PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["template", "params"],
    "properties": {
        "template": {"type": "string"},
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type", "example_json", "source"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["string", "number", "boolean"]},
                    "example_json": {"type": "string"},
                    "source": {"type": "string", "enum": ["task", "literal"]},
                },
            },
        },
    },
}


def _positions(template: str) -> set[int]:
    """Reject placeholders embedded in strings, comments or identifier fragments."""
    positions = {match.start(): match for match in PLACEHOLDER.finditer(template)}
    allowed = set()
    quote = None
    comment = None
    i = 0
    while i < len(template):
        char = template[i]
        pair = template[i : i + 2]
        if comment:
            if comment == "line" and char == "\n":
                comment = None
            elif comment == "block" and pair == "*/":
                comment = None
                i += 1
        elif quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = None
        elif pair in {"//", "/*"}:
            comment = "line" if pair == "//" else "block"
            i += 1
        elif char == "#":
            comment = "line"
        elif char in {'"', "'", "`", "/"}:
            quote = char
        elif i in positions:
            match = positions[i]
            before = template[i - 1] if i else ""
            after = template[match.end() : match.end() + 1]
            if (before and (before.isalnum() or before in "_$")) or (
                after and (after.isalnum() or after in "_$")
            ):
                raise ValueError("A parameter must occupy a whole literal")
            allowed.add(i)
        i += 1
    if allowed != positions.keys():
        raise ValueError("Parameters cannot be embedded in strings or comments")
    return allowed


def render(template: str, params: dict) -> str:
    if not params:
        return template
    _positions(template)
    names = {m[1] for m in PLACEHOLDER.finditer(template)}
    if names != params.keys():
        raise ValueError("Template parameters do not match")
    for value in params.values():
        if type(value) not in {str, int, float, bool}:
            raise ValueError("Parameters must be scalar literals")
    return PLACEHOLDER.sub(
        lambda m: json.dumps(params[m[1]], ensure_ascii=False, allow_nan=False),
        template,
    )


def parameterise(code: str, context: dict, *, runner=None, use_model=True) -> dict:
    raw = {
        "template": code,
        "params": {},
        "model_calls": 0,
        "cost_usd": 0.0,
        "usage": None,
    }
    if not use_model or (os.environ.get("FORK_DISABLE_API") == "1" and runner is None):
        return {**raw, "fallback_reason": "model_disabled"}
    prompt = (
        "Return a template replacing task-specific scalar literals with {{name}}. "
        "Each placeholder replaces the ENTIRE JSON-compatible literal, including its quotes. "
        "Never place placeholders inside strings/comments. Do not change any other byte. "
        "For each parameter return its semantic name, type, example_json (the original JSON literal), "
        "and source=task only when the literal appears in the task; otherwise source=literal. "
        "If exact templating is impossible, return the unchanged code and an empty params list.\n"
        + json.dumps({"code": code, "context": context})
    )
    body = {
        "model": "gpt-6-astra",
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 1800,
        "input": [{"role": "user", "content": prompt}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "code_template",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    }
    # Conservatively bound input bytes plus protocol overhead before reserving
    # $0.25 per call at the runtime's configured estimated token prices.
    if len(json.dumps(body).encode()) > 12_000:
        return {**raw, "fallback_reason": "input_limit"}
    if runner:
        response = runner(**body)
    else:
        with HttpTransport() as tx:
            response = tx.create(timeout_s=45, **body)
    usage = Tokens.from_usage(response.get("usage"))
    raw.update(model_calls=1, cost_usd=estimate_usd(usage), usage=response["usage"])
    try:
        text = "".join(
            part.get("text", "")
            for item in response.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        result = json.loads(text)
        if response.get("status") != "completed" or set(result) != {
            "template",
            "params",
        }:
            raise ValueError("Incomplete template response")
        params = {}
        for item in result["params"]:
            value = json.loads(item["example_json"])
            kind = (
                "boolean"
                if type(value) is bool
                else "number"
                if type(value) in {int, float}
                else "string"
                if isinstance(value, str)
                else None
            )
            if (
                kind != item["type"]
                or item["source"] not in {"task", "literal"}
                or item["name"] in params
                or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", item["name"])
            ):
                raise ValueError("Invalid parameter metadata")
            source = item["source"]
            if source == "task" and str(value) not in context.get("prompt", ""):
                source = "literal"
            params[item["name"]] = {"type": kind, "example": value, "source": source}
        if (
            render(result["template"], {k: v["example"] for k, v in params.items()})
            != code
        ):
            raise ValueError("Template changed the original code")
        return {
            **raw,
            "template": result["template"],
            "params": params,
            "fallback_reason": None,
        }
    except (ValueError, TypeError, KeyError):
        return {**raw, "fallback_reason": "round_trip_guard"}
