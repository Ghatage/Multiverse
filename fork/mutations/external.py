"""Explicit local-test-app state adapter; arbitrary external services are not rewound."""

from urllib.parse import urlsplit

import httpx


def capture(url: str, branch: str) -> dict:
    parsed = urlsplit(url)
    if url == "about:blank":
        return {"supported": True, "tenant": None, "state": None}
    if parsed.netloc != "host.docker.internal:3000" or not parsed.path.startswith(
        "/t/" + branch + "/"
    ):
        return {"supported": False, "reason": "external_service_without_adapter"}
    response = httpx.get(f"http://localhost:3000/t/{branch}/state.json", timeout=5)
    response.raise_for_status()
    state = response.json()
    return {
        "supported": state.get("submitted") is False,
        "tenant": branch,
        "state": state,
    }


def restore(snapshot: dict, branch: str) -> None:
    if snapshot.get("supported") is not True:
        raise ValueError(
            "External effects require reconciliation; no restore adapter is available"
        )
    if snapshot["tenant"] is None:
        return
    state = snapshot["state"]
    if state.get("submitted") is not False or state.get("order_id") is not None:
        raise ValueError("Submitted orders cannot be rewound by the local adapter")
    prefix = f"http://localhost:3000/t/{branch}"
    with httpx.Client(timeout=5) as client:
        current = client.get(prefix + "/state.json")
        current.raise_for_status()
        if (
            current.json().get("submitted") is not False
            or current.json().get("order_id") is not None
        ):
            raise ValueError(
                "Current external effects require reconciliation; submitted orders cannot be reset by recovery"
            )
        client.post(prefix + "/reset").raise_for_status()
        fields = state["fields"]
        for number, names in [
            (1, ("first_name", "last_name", "email", "phone")),
            (2, ("company", "role", "start_date", "budget")),
            (3, ("priority", "newsletter", "notes")),
        ]:
            # Do not manufacture fields absent from this pre-action server snapshot.
            if any(name in fields for name in names):
                data = {
                    name: fields[name]
                    for name in names
                    if name in fields
                    and not (name == "newsletter" and fields[name] == "off")
                }
                client.post(prefix + f"/form/{number}", data=data).raise_for_status()
        for row in state["rows"]:
            client.post(prefix + "/rows", json=row).raise_for_status()
        got = client.get(prefix + "/state.json")
        got.raise_for_status()
        if got.json() != state:
            raise RuntimeError(
                "Local tenant restoration did not reproduce its captured state"
            )
