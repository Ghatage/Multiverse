"""One Responses session per branch; injected clients allow key-free wire tests."""

import copy
import os
import queue
import threading
import time
from typing import Any


class TransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code="transport_error",
        status: int | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code, self.status, self.retry_after = code, status, retry_after


class WallTimeout(TransportError):
    pass


def plain(value: Any) -> dict:
    return (
        value
        if isinstance(value, dict)
        else value.model_dump(mode="json", by_alias=True)
    )


def failure(exc: Exception) -> TransportError:
    if isinstance(exc, TransportError):
        return exc
    body = getattr(exc, "body", None) or {}
    if isinstance(body, dict):
        body = body.get("error", body)
    code = (
        body.get("code", "transport_error")
        if isinstance(body, dict)
        else "transport_error"
    )
    status = getattr(exc, "status_code", None)
    headers = getattr(getattr(exc, "response", None), "headers", {})
    try:
        delay = float(headers.get("retry-after"))
    except (TypeError, ValueError):
        delay = None
    # Do not copy HTTP headers, bearer credentials, or request bodies into run logs.
    return TransportError(
        f"Model transport failed ({code}, status={status})",
        code=code,
        status=status,
        retry_after=delay,
    )


def live_client():
    if os.environ.get("FORK_DISABLE_API") == "1":
        raise ValueError("API calls disabled by FORK_DISABLE_API=1")
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(
            "OPENAI_API_KEY is missing; use make agent-test-offline for key-free validation"
        )
    from openai import OpenAI

    return OpenAI(max_retries=0, timeout=60)


class HttpTransport:
    name = "http"

    def __init__(self, client=None):
        self.client = client
        self.owns_client = client is None
        self.history: list[dict] = []
        self.last_response_id: str | None = None

    def __enter__(self):
        if self.client is None:
            self.client = live_client()
        return self

    def __exit__(self, *args):
        if self.owns_client and self.client:
            self.client.close()

    def create(self, *, timeout_s: float = 60, **body) -> dict:
        body = {k: v for k, v in body.items() if k not in {"type", "stream_id"}}
        stateless = body.get("store") is False
        if stateless:
            previous = body.pop("previous_response_id", None)
            if previous and previous != self.last_response_id:
                raise TransportError(
                    "HTTP continuation unavailable", code="previous_response_not_found"
                )
            body["input"] = copy.deepcopy(
                (self.history if previous else []) + body.get("input", [])
            )
        try:
            result = plain(
                self.client.with_options(
                    timeout=timeout_s, max_retries=0
                ).responses.create(**body)
            )
            if stateless:
                # Commit only after success: retrying cannot duplicate prior tool outputs.
                self.history = copy.deepcopy(body["input"] + result.get("output", []))
                self.last_response_id = result["id"]
            return result
        except Exception as exc:
            raise failure(exc) from exc

    def reset(self):
        self.history = []
        self.last_response_id = None

    def steer(self, *args, **kwargs):
        raise NotImplementedError("Mid-turn steering is not implemented")


class WsTransport(HttpTransport):
    name = "ws"

    def __init__(self, client=None):
        super().__init__(client)
        self.connection = None
        self.manager = None
        self.reader = None
        self.events = queue.Queue()
        self.current_response_id: dict[str, str] = {}

    def __enter__(self):
        super().__enter__()
        return self

    def _connect(self, timeout_s: float):
        try:
            self.manager = self.client.responses.connect(
                max_retries=0,
                websocket_connection_options={"open_timeout": min(timeout_s, 20)},
            )
            self.connection = self.manager.__enter__()
        except Exception as exc:
            raise failure(exc) from exc
        connection, events = self.connection, self.events

        def read():
            try:
                for event in connection:
                    events.put(plain(event))
                events.put(TransportError("WebSocket closed", code="connection_lost"))
            except Exception:  # noqa: BLE001 - reader communicates failures to caller
                events.put(
                    TransportError("WebSocket disconnected", code="connection_lost")
                )

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def create(self, *, timeout_s: float = 60, **body) -> dict:
        deadline = time.monotonic() + timeout_s
        if self.connection is None:
            self._connect(timeout_s)
        lane = body.get("stream_id", "default")
        try:
            self.connection.send({"type": "response.create", **body})
        except Exception as exc:
            raise TransportError(
                "WebSocket send failed", code="connection_lost"
            ) from exc
        active = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.reset()
                raise WallTimeout(
                    "Model response exceeded wall deadline", code="wall_cap"
                )
            try:
                event = self.events.get(timeout=remaining)
            except queue.Empty as exc:
                self.reset()
                raise WallTimeout(
                    "Model response exceeded wall deadline", code="wall_cap"
                ) from exc
            if isinstance(event, Exception):
                raise event
            if event.get("stream_id", lane) != lane:
                continue
            kind = event.get("type")
            response = event.get("response") or {}
            if kind == "response.created":
                active = response["id"]
                self.current_response_id[lane] = active
            elif kind == "error":
                err = event.get("error", event)
                code = err.get("code", "server_error")
                status = (
                    429
                    if code in {"rate_limit_exceeded", "slow_down"}
                    else (503 if code in {"server_error", "overloaded"} else None)
                )
                raise TransportError(
                    f"WebSocket error: {code}", code=code, status=status
                )
            elif kind in {
                "response.completed",
                "response.failed",
                "response.incomplete",
            }:
                if active and response.get("id") != active:
                    continue
                if response.get("error"):
                    err = response["error"]
                    code = err.get("code", "server_error")
                    status = (
                        429
                        if code == "rate_limit_exceeded"
                        else (503 if code == "server_error" else None)
                    )
                    raise TransportError(
                        f"Response failed: {code}", code=code, status=status
                    )
                return response

    def reset(self):
        if self.manager is not None:
            self.manager.__exit__(None, None, None)
        if self.reader:
            self.reader.join(timeout=1)
        self.manager = self.connection = self.reader = None
        self.current_response_id.clear()
        self.events = queue.Queue()

    def __exit__(self, *args):
        self.reset()
        super().__exit__(*args)
