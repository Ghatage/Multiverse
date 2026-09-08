"""Publish observed local browser actions to the live graph, without checkpoints."""
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from fork.agent.log import RunLogger
from fork.cu import db
from fork.repl_client import ReplClient
from fork.store.db import Store

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
log = RunLogger("demo-source", "demo-onboarding-live", "low", backend="scripted", root=ROOT / "runs")
store = Store(artifact_root=ROOT / "runs")
store.record_run(log.run_id, log.task_id, log.branch)
(log.path / "task.json").write_text(json.dumps({"id": log.task_id, "prompt": "Prepare, review, and save the local onboarding demonstration."}))
with ReplClient(f"http://127.0.0.1:{db.get_branch(log.branch).ports['repl']}") as repl:
    for index, expected in enumerate(["Start", "Prepare", "Review", "Saved"], 1):
        before = repl.call("observe", mode="tree", target="browser", max_tree_chars=2000000)
        code = "await page.goto('http://127.0.0.1:8765/?flow=onboarding');" if index == 1 else "await page.locator('#next').click();"
        started = time.monotonic()
        repl.call("exec_js", code=code, timeout_ms=15000)
        after = repl.call("observe", mode="both", target="browser", max_tree_chars=2000000)
        elapsed = round((time.monotonic() - started) * 1000)
        verified = after.get("title") == f"Onboarding checklist · {expected}"
        if not verified:
            raise RuntimeError(f"Expected {expected}, observed {after.get('title')}")
        pre = log.observation(before, index, "before")
        post = log.observation(after, index, "after")
        shot = log.screenshot(after["screenshot"], index)
        log.steps = index
        log.write(kind="tool", step=index, tool="exec_js", source="scripted", code=code,
                  tree_before=pre, tree_after=post, screenshot=shot, error=None, exec_ms=elapsed,
                  url_before=before.get("url"), url_after=after.get("url"))
        with store.transaction():
            a = store.node_for(before["tree"])[0]
            b = store.node_for(after["tree"])[0]
            store.record_step(log.run_id, index, a, b, "scripted", "exec_js", code,
                              verified, elapsed, None, None, None,
                              evidence={"before": pre, "after": post, "coverage": "tool_level"})
        print(json.dumps({"run": log.run_id, "step": index, "observed": after["title"]}), flush=True)
        time.sleep(1)
summary = log.finish("final_answer", text="Local onboarding saved and observed.", checker={"pass": True, "errors": []})
store.finish_run(log.run_id, stop_reason="final_answer", model_calls=0, checker_pass=True,
                 wall_s=summary["wall_s"], checkpoint_coverage="action_checkpoints_unavailable")
