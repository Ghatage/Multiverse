"""UI transition matching; this does not certify concrete checkpoint restoration."""

import json
import math
from dataclasses import dataclass

from fork.store.signature import fuzzy_match, normalise


@dataclass(frozen=True)
class Verification:
    ok: bool
    score: float
    exact: bool
    reason: str


def verify(
    post_tree: str, edge: dict, expected: dict, *, threshold: float = 0.9
) -> Verification:
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid verification threshold")
    if (
        expected["id"] != edge["to_node"]
        or expected["signature"] != edge["post_signature"]
    ):
        raise ValueError("Edge destination and expected state disagree")
    if not post_tree or "(truncated " in post_tree:
        return Verification(False, 0, False, "incomplete_observation")
    actual = normalise(post_tree)
    if actual.app != expected["app"] or actual.url_pattern != expected["url_pattern"]:
        return Verification(False, 0, False, "wrong_app_or_page")
    if actual.signature == edge["post_signature"]:
        return Verification(True, 1, True, "exact")
    score = fuzzy_match(actual.tokens, json.loads(expected["tokens_json"]))
    return Verification(
        score > 0 and score >= threshold,
        score,
        False,
        "fuzzy" if score > 0 and score >= threshold else "state_mismatch",
    )
