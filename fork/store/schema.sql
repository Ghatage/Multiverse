CREATE TABLE IF NOT EXISTS nodes (
 id TEXT PRIMARY KEY, signature TEXT NOT NULL, tokens_json TEXT NOT NULL,
 app TEXT NOT NULL, url_pattern TEXT NOT NULL, title_pattern TEXT NOT NULL,
 sample_tree TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 hits INTEGER NOT NULL DEFAULT 0, UNIQUE(app, signature));
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, branch TEXT NOT NULL, mode TEXT NOT NULL,
 effort TEXT, started TEXT NOT NULL, finished TEXT, stop_reason TEXT,
 model_calls INTEGER DEFAULT 0, cache_hits INTEGER DEFAULT 0, cache_misses INTEGER DEFAULT 0,
 repairs INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
 cost_usd REAL DEFAULT 0, wall_s REAL DEFAULT 0, checker_pass INTEGER,
 checkpoint_coverage TEXT NOT NULL DEFAULT 'action_checkpoints_unavailable',
 ingest_sha TEXT);
CREATE TABLE IF NOT EXISTS edges (
 id TEXT PRIMARY KEY, from_node TEXT NOT NULL REFERENCES nodes(id),
 to_node TEXT NOT NULL REFERENCES nodes(id), tool TEXT NOT NULL, code TEXT NOT NULL,
 params_json TEXT NOT NULL, post_signature TEXT NOT NULL, subgoal TEXT,
 hits INTEGER NOT NULL DEFAULT 1, fails INTEGER NOT NULL DEFAULT 0, cost_ms INTEGER,
 tokens_saved INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active'
 CHECK(status IN ('active','demoted','retired')),
 created TEXT NOT NULL, last_verified TEXT NOT NULL,
 verification_scope TEXT NOT NULL DEFAULT 'task',
 recovery_status TEXT NOT NULL DEFAULT 'action_checkpoints_unavailable',
 UNIQUE(from_node,to_node,tool,code));
CREATE INDEX IF NOT EXISTS edges_from ON edges(from_node,status);
CREATE TABLE IF NOT EXISTS steps (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 idx INTEGER NOT NULL, from_node TEXT REFERENCES nodes(id), to_node TEXT REFERENCES nodes(id),
 edge_id TEXT REFERENCES edges(id), source TEXT NOT NULL, tool TEXT, code TEXT,
 verified INTEGER, exec_ms INTEGER, model_ms INTEGER, tokens_in INTEGER, cost_usd REAL,
 ts TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', UNIQUE(run_id,idx));
CREATE TABLE IF NOT EXISTS checkpoints (
 id TEXT PRIMARY KEY, branch TEXT NOT NULL, node_id TEXT REFERENCES nodes(id),
 image TEXT NOT NULL, run_id TEXT REFERENCES runs(id), step INTEGER, created TEXT NOT NULL,
 action_id TEXT, parent_id TEXT REFERENCES checkpoints(id), image_digest TEXT,
 platform TEXT, manifest_path TEXT, manifest_sha TEXT, fidelity_json TEXT,
 status TEXT NOT NULL CHECK(status IN ('pending','committed','failed','legacy')),
 incarnation TEXT, evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS actions (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), branch TEXT NOT NULL,
 incarnation TEXT NOT NULL, call_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 operation TEXT NOT NULL, arguments_json TEXT NOT NULL,
 pre_checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
 post_checkpoint_id TEXT REFERENCES checkpoints(id), outcome TEXT NOT NULL,
 checkpoint_status TEXT NOT NULL CHECK(checkpoint_status IN ('pending','committed','failed')),
 pre_evidence_json TEXT NOT NULL, post_evidence_json TEXT, created TEXT NOT NULL, completed TEXT,
 UNIQUE(run_id,call_id,sequence));
CREATE TABLE IF NOT EXISTS edge_actions (
 edge_id TEXT NOT NULL REFERENCES edges(id), ordinal INTEGER NOT NULL,
 action_id TEXT NOT NULL REFERENCES actions(id), PRIMARY KEY(edge_id,ordinal));
CREATE INDEX IF NOT EXISTS actions_run ON actions(run_id,created,sequence);
CREATE TABLE IF NOT EXISTS checkpoint_candidates (
 id TEXT PRIMARY KEY, action_id TEXT REFERENCES actions(id), payload_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS template_cache (
 code_sha TEXT NOT NULL, context_sha TEXT NOT NULL, result_json TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(code_sha,context_sha));
CREATE TABLE IF NOT EXISTS recoveries (
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id),
 old_incarnation TEXT NOT NULL, new_incarnation TEXT NOT NULL,
 evidence_json TEXT NOT NULL, created TEXT NOT NULL,
 UNIQUE(run_id,new_incarnation));
