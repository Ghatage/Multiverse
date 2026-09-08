"""Publish a single-medium repair only after independent task verification."""

import json

from fork.store.signature import normalise


def retained_prefix(actions: list[dict]) -> list[dict]:
    # Recovery restores the last attempted action's pre-boundary. Its post-image
    # may be missing, so checkpoint equality cannot identify the retained prefix.
    return [
        {"operation": action["operation"], **json.loads(action["arguments_json"])}
        for action in actions[:-1]
        if action["outcome"] in {"succeeded", "no_op"}
        and action["checkpoint_status"] == "committed"
    ]


def publish_replacement(store, candidates: list[dict]) -> str:
    for candidate in candidates:
        before, after = normalise(candidate["before"]), normalise(candidate["after"])
        if before.app != after.app:
            continue
        return store.upsert_edge(
            store.node_for(candidate["before"])[0],
            store.node_for(candidate["after"])[0],
            "act",
            candidate["code"],
            {},
            after.signature,
            "repaired transition",
            action_ids=candidate["action_ids"],
        )
    raise ValueError("Repair has no checkpointed replacement transition")
