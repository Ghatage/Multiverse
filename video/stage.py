"""Record scripted inputs through the real harness; no model responses or scores are fabricated."""
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from fork.agent.log import RunLogger
from fork.agent.policy import ObservationPolicy
from fork.cu import db
from fork.repl_client import ReplClient
from fork.replay.executor import ReplayHooks
from fork.replay.metrics import refresh
from fork.store.db import Store
from fork.store.ingest import ingest_run

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
ROOT = Path(__file__).resolve().parents[1]


def record(branch, flow, mode):
    task = {'id': 'demo-' + flow, 'prompt': 'Scripted demonstration: prepare and save a local ' + flow + ' draft.',
            'start_url': 'http://127.0.0.1:8765/?flow=' + flow, 'final_url_pattern': '#saved$'}
    log = RunLogger(branch, task['id'], 'low', backend='scripted', root=ROOT / 'runs')
    (log.path / 'task.json').write_text(json.dumps(task, indent=2))
    store = Store(artifact_root=ROOT / 'runs')
    store.record_run(log.run_id, task['id'], branch, mode, 'low')
    client = ReplClient(f"http://127.0.0.1:{db.get_branch(branch).ports['repl']}")

    def checker():
        obs = client.call('observe', mode='tree', target='browser', max_tree_chars=2000000)
        passed = obs.get('url', '').endswith('#saved') and 'saved' in obs.get('title', '').lower()
        return {'pass': passed, 'errors': [] if passed else ['Saved screen not observed']}

    hooks = ReplayHooks(mode)
    summary = None
    try:
        executor = hooks.bind(branch, task, log, store, ObservationPolicy(), lambda: None, checker,
                              deadline=time.monotonic() + 600)
        executor.source = 'scripted'
        hooks.bootstrap()
        if mode == 'cold':
            for i in range(3):
                result = executor.execute({'name': 'act', 'call_id': 'scripted-' + str(i),
                    'arguments': json.dumps({'actions': [{'operation': 'click', 'selector': '#next'}]})})
                if executor.history[-1].get('error'):
                    raise RuntimeError(executor.history[-1]['error'])
        else:
            for _ in range(8):
                result = hooks.before_model_turn()
                if result and result.get('complete'):
                    break
                if not result or not result.get('replayed'):
                    raise RuntimeError('Recorded path did not replay: ' + str(result))
            if hooks.hits != 3:
                raise RuntimeError('Expected all three recorded transitions to replay')
        verdict = checker()
        if not verdict['pass']:
            raise RuntimeError('Independent saved-screen check failed')
        extra = {'stop_reason': 'final_answer', 'checker': verdict, 'mode': mode, 'usage_source': 'scripted',
                 'demonstration': 'scripted inputs; live container execution; no model calls'}
        hooks.finish(extra)
        summary = log.finish('final_answer', text='Local draft saved and observed.', checker=verdict,
                             extra={k: v for k, v in extra.items() if k not in {'stop_reason', 'checker'}})
        ingest_run(store, log.path)
        refresh(store)
        print(json.dumps({'run': log.run_id, 'branch': branch, 'flow': flow, 'mode': mode,
                          'hits': hooks.hits, 'steps': log.steps}), flush=True)
        return summary
    finally:
        client.close()
        if summary is None and hooks.coordinator:
            hooks.coordinator.__exit__(None, None, None)


if __name__ == '__main__':
    import sys
    record(*sys.argv[1:])
