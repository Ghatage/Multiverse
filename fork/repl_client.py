"""Synchronous host client for a branch's JSON-RPC server."""

import uuid
from typing import Any

import httpx


class ReplError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.data = code, data or {}


class ReplClient:
    def __init__(self, base_url: str, timeout_s: float = 60):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout_s)

    def call(self, method: str, **params: Any) -> dict:
        rid = str(uuid.uuid4())
        try:
            response = self.http.post(
                f"{self.base}/rpc",
                json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ReplError(-32003, "REPL transport failure") from exc
        if body.get("id") != rid or response.headers.get("x-fork-request-id") != rid:
            raise ReplError(-32600, "Request id mismatch")
        if "error" in body:
            e = body["error"]
            raise ReplError(e["code"], e["message"], e.get("data"))
        return body["result"]

    def health(self) -> dict:
        response = self.http.get(f"{self.base}/health")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
