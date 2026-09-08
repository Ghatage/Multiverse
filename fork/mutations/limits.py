"""Stop before another mutation can exhaust retained-checkpoint resources."""

import os
import shutil

from fork.cu import docker


def admit(store, backend, run_id: str) -> None:
    cap = int(os.environ.get("FORK_MAX_RUN_CHECKPOINTS", "300"))
    reserve = int(os.environ.get("FORK_CHECKPOINT_RESERVE_BYTES", str(2 * 1024**3)))
    if cap < 3 or reserve < 1:
        raise ValueError(
            "Checkpoint limits must allow at least three images and a positive disk reserve"
        )
    with store.connection() as con:
        retained = con.execute(
            "SELECT COUNT(*) FROM checkpoint_candidates WHERE json_extract(payload_json,'$.run_id')=?",
            (run_id,),
        ).fetchone()[0]
    # Keep room for a drift baseline as well as this action's post-image.
    if retained + 2 > cap:
        raise RuntimeError(
            "Checkpoint admission stopped: per-run image limit reached; retained images were preserved"
        )
    if shutil.disk_usage(store.path.parent).free < reserve:
        raise RuntimeError(
            "Checkpoint admission stopped: host evidence disk reserve reached"
        )
    free = (
        docker.command("exec", "fork-" + backend.branch, "df", "-Pk", "/home/user")
        .splitlines()[-1]
        .split()[3]
    )
    if int(free) * 1024 < reserve:
        raise RuntimeError(
            "Checkpoint admission stopped: guest image disk reserve reached"
        )
