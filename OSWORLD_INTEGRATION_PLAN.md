# OSWorld V2 on Multiverse

## Objective

Run OSWorld V2 tasks inside our existing OrbStack-hosted Linux containers. Record actions and outcomes, restore verified checkpoints, explore alternative continuations, and replay a consolidated successful workflow from a clean task start. Preserve unsuccessful exploration as useful graph evidence.

This is a researched implementation plan, not a claim that OSWorld tasks already run here. No agent experiments or benchmark task setup were executed during planning.

## Evidence and scope

Reviewed OSWorld release `v2026.08.08`, commit `d578d2d4e0dc82b43e270fdaa7fa89d9708cd154`. Its manifest pins 108 tasks, task assets, websites, and an x86 Ubuntu QCOW2 image. Official V2 task implementations are Python classes distributed separately through a gated Hugging Face dataset; the public checkout's legacy JSON examples are not the V2 suite.

Our running `fork-work` container is ARM64 Debian 12 with Chromium and LibreOffice. GIMP, VLC, and VS Code were not found on its PATH. OSWorld's Docker provider manages a VM; it is not a drop-in equivalent of our desktop container.

Keep this runtime and adapt supported tasks. Record environment differences and task changes. Report results as adapted-runtime experiments until equivalence with the official environment is established. Do not promise all 108 tasks before inspecting their downloaded implementations and dependencies.

## Existing foundation and gaps

| Area | Exists | Required addition |
| --- | --- | --- |
| Desktop | Linux desktop, Chromium, PyAutoGUI, Playwright, observation REPL | Per-task application and environment compatibility |
| Agent | Responses API, code tools, run budgets | OSWorld task lifecycle and resumable agent state |
| Task loader | JSON `id`, `prompt`, required `start_url` | Python task classes, setup, native-app starts, phases, simulator support |
| Scoring | Local web-form state checker; injectable checker callback | Original OSWorld evaluator contract, partial scores, evaluation artifacts |
| Traces | Tool-call JSONL, tree hashes, conditional screenshots | Atomic actions, full observations, ancestry, replayable parameters |
| Checkpoint | Docker filesystem image, tab URLs, serializable REPL variables | Restore verification, app recovery, external service state, agent context |
| Fork | Multiple child containers from a checkpoint | Goal-directed scheduler and isolated task backends |
| Merge | Promote one branch image | Verified route composition and clean-start replay |

The current agent always navigates to `start_url` on startup. A continuation must bypass that bootstrap. Rewind currently stops an active run when its container identity changes; it does not resume the model conversation.

## 1. Pin inputs and audit task compatibility

- Pin OSWorld code, task classes, assets, websites, and hashes to the same release. Keep upstream dependencies in a separate environment where necessary.
- Obtain authorized dataset access; download task implementations and required assets. Treat missing access as a setup dependency, not a failed task.
- Generate a compatibility inventory per task: applications, architecture, OS assumptions, paths/user, fonts, display size, locale, setup operations, evaluator getters, external services, phase and simulator requirements.
- Classify tasks as supported, adaptable, or blocked with concrete reasons. Keep adaptation patches explicit; preserve evaluator meaning.
- Select one local-file task supported by our apps, then one mock-web task, then one multi-app or multi-phase task. Exact task IDs follow the inventory, not guesses from legacy examples.

Acceptance: selected task assets are verified, prerequisites are present, and unsupported dependencies fail before the agent runs.

## 2. Implement the OSWorld environment adapter

Proposed modules: `fork/osworld/adapter.py`, `tasks.py`, `evaluate.py`, plus a versioned dependency/profile manifest.

- Expose the environment/controller surface used by selected task classes: setup, command execution, launch/open, file transfer and retrieval, screenshots, observations, actions, and evaluation.
- Prefer reusing upstream setup/evaluation implementations with compatible controller endpoints. Audit actual task calls before deciding how much of the OSWorld guest service to package versus bridge through our REPL.
- Route every operation to the correct branch. Do not create an upstream VM as a side effect of constructing an environment adapter.
- Execute task setup once on a clean container. Verify setup completion and establish the root checkpoint before requesting model actions.
- Support phase transitions and user-simulator state when required; setup for the next phase is not an agent action.
- Separate agent-visible instruction/state from evaluator internals and expected artifacts.
- Preserve original final score and detailed partial-score output. Record setup, infrastructure, model, and evaluator errors distinctly.

Acceptance: one genuine V2 task sets up and evaluates inside our container; initial incomplete state scores appropriately; a known-correct fixture passes the evaluator; then one live agent run completes and receives an independent score.

## 3. Record every action and observation

Proposed modules: `fork/trace/events.py`, `store.py`; integrate with `fork/agent/tools.py` and the REPL.

- Append an action-intent event before execution and a completion/error event afterward, so crashes leave an explicit uncertain action rather than a missing trace.
- Record task/release/run/branch/parent/checkpoint IDs, event sequence, model/tool call IDs, action type, target locator, coordinates where applicable, key/text parameters, timing, outcome, and errors.
- Store before/after screenshots and complete accessibility observations, active window, URL/title, screen versus browser-viewport coordinate space, resolution, scale, and relevant artifact fingerprints.
- Use immutable content-addressed observation blobs to deduplicate unchanged screens. Capture for storage after each atomic action; model input can remain selective.
- A generated script can contain many actions. Use instrumented action primitives and restrict bypasses in trace-complete mode. Keep uninstrumented arbitrary code explicitly labeled as opaque; do not claim every click is logged merely because the script is saved.
- Both custom code tools and optional native OpenAI computer actions must feed the same recorder. Split native action batches into individual trace events.
- Allow a stability wait after input; record timeout or unresolved animation rather than declaring the next state verified.

Acceptance: clicks, typing, scrolling, failed actions, and partial script execution produce reconstructable ordered events with correct coordinate spaces.

## 4. Make checkpoint and continuation semantics reliable

Use three levels:

1. Observation point: cheap screenshot/tree/action boundary; useful for diagnosis but not independently restorable.
2. Durable checkpoint: container filesystem plus task/phase state, app recovery recipe, external service snapshot references, and agent continuation context.
3. Verified restore: checkpoint recreated in a child and checked against expected files, app state, task progress, and backend identity.

Docker commit is not a process-memory snapshot. Unsaved documents, modal dialogs, browser JS memory, selection, and scroll position are not guaranteed to survive. Saving a file solely for checkpointing can itself change task semantics. Where lossless restore is unavailable, replay from an earlier durable checkpoint to reconstruct the desired action boundary and verify the result.

- Acquire the branch lock across the action boundary, sidecar capture, and image commit. Coordinate filesystem flush and app-specific recovery.
- Persist model-visible conversation/tool outputs or a bounded continuation summary, task phase, action history, remaining budget, and simulator state; do not depend solely on an API response ID.
- Resume through a distinct entry point that observes the restored state instead of navigating to the starting URL.
- Forking a browser does not fork its web server. Web experiments require branch-specific database/files/service state and browser sessions. Snapshot the complete service set consistently, or mark that task non-forkable.
- Never run a potentially mutating evaluator against an active branch by default. Respect `intermediate_eval_safe`; use a disposable evaluation branch only when all affected state can be isolated. Otherwise defer evaluation.

Acceptance: two children start from verified equivalent state, mutations in one do not affect the other, and rewind restores the expected task state. Repeat this for both a local-file task and an isolated web task.

## 5. Add bounded exploration

Proposed modules: `fork/search/scheduler.py`, `progress.py`.

- Begin with one branch and at most two concurrent children, subject to measured memory and host headroom.
- Fork at meaningful decisions, repeated failures, stagnation, or verified milestones; do not create a container at every click.
- Give each child a distinct approach and the same goal. Track per-criterion progress, regressions, execution cost, and time rather than only an aggregate score.
- Prefer promising branches without treating partial credit as a guarantee that the remaining task is reachable.
- Enforce shared wall-time, token/cost, action, fork-count, disk, and concurrency budgets. Preserve trace evidence before pruning containers.
- If privileged evaluator feedback guides search, label it an evaluator-assisted experiment. Keep a separate evaluation mode where the agent cannot access that feedback or solutions.

Acceptance: a deliberately unsuccessful continuation is retained, a sibling succeeds, and the scheduler can continue from the last verified checkpoint within a bounded budget.

## 6. Build the graph and validate the cached route

Proposed modules: `fork/graph/build.py`, `route.py`, `fork/replay.py`; visual layer uses `3d-force-graph`.

- Keep immutable concrete execution states separate from abstract app states such as a settings screen. Visual similarity is a candidate match, not proof of interchangeable state.
- Edges carry action parameters, preconditions, observations, outcome, duration, and provenance. Track evidence counts before describing an edge's reliability.
- Annotate checkpoint/phase boundaries, failures, partial-score changes, and successful routes. Build incrementally so exploration can be inspected before completion.
- Start with the best fully verified observed trajectory. Propose shorter routes by joining only compatible states; verify relevant files, session/backend state, and prerequisites.
- Replay every proposed merged route from fresh task setup and run the original final evaluator. Repeat successful replay before caching it as reusable.
- Cache recipes with semantic targets and state guards, plus task/app/environment/release versions. On mismatch, invalidate the affected segment and resume exploration.
- Retain losing paths and failure reasons. App coverage grows through visited states; solving a task does not establish whole-app coverage.

Acceptance: a clean-start replay passes independently, its route links to original evidence, and a changed starting state triggers recovery instead of blind coordinate playback.

## Experiment sequence and measurements

Run the same selected tasks as: baseline single branch, bounded fork search, then cached replay. Charge all exploration and evaluator costs to the fork experiment. Report final success, partial-score progression, restore fidelity, fork isolation, replay success across repeated runs, actions, wall time, model cost, checkpoint latency, and actual disk use.

First milestone: one real V2 task on the existing Linux container, fully traced, with one verified checkpoint, two isolated continuations, evaluator-confirmed success, and a successful clean replay. Expand task coverage only after that loop works.

## Sources

- [Pinned release manifest](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/benchmark_releases/osworld-v2-2026.08.08.json)
- [V2 task distribution and migration](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/docs/MIGRATING_FROM_OSWORLD_V1.md)
- [Task and multi-phase interfaces](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/desktop_env/task_base.py)
- [Partial score format](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/docs/EVALUATE_RESULT_JSON.md)
- [Intermediate evaluation implementation](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/lib_run_single.py)
- [Docker provider](https://github.com/xlang-ai/OSWorld-V2/blob/v2026.08.08/desktop_env/providers/docker/provider.py)
