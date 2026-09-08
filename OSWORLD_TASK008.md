# OSWorld V2 task 008

Pinned release: `v2026.08.08`. Adapted to Debian x86-64, Chromium, and three per-desktop website backends. This is not an official Ubuntu VM benchmark result.

## Build

Accept both OSWorld V2 gated datasets on Hugging Face and authenticate `hf` locally. Then:

```sh
uv run --with huggingface-hub python scripts/setup_osworld008.py
```

Use the unique image tag printed by the script. Keep source image tags while their checkpoints remain in use. All task assets and backend dependencies are staged locally; gold evaluator data stays on the host.

## Checkpoint, fork, continue, rewind

```sh
uv run cu branch create task008 --from IMAGE_TAG
uv run cu checkpoint task008 --label start --json
uv run cu fork task008 --from CHECKPOINT_ID --names task008-a,task008-b --json
uv run fork-agent run --branch task008-a --task data/osworld/task008.json --resume
uv run cu checkpoint task008-a --label progress --json
uv run cu branch create task008-next --from PROGRESS_CHECKPOINT_ID
uv run fork-agent run --branch task008-next --task data/osworld/task008.json --resume
uv run cu rewind task008-a CHECKPOINT_ID --json
```

`--resume` observes the existing desktop without navigating to the task's start URL. Races also resume. The model run uses the configured API and incurs usage.

Checkpoint after saving changes. Checkpoints capture backend state, files, browser local/session storage, cookies, tab URLs and serializable REPL variables. Writer reopens the saved guideline document. Unsaved Writer edits, dialogs, selections, scroll position and process memory are not restored.

## Evaluate

```sh
uv run --with requests --with pillow python scripts/evaluate_osworld008.py task008-a
```

Runs the pinned original evaluator using host-side adapters. Default evaluation skips paid image judging and reports incomplete evaluation if attachments need it. Add `--vision` for official model-based attachment judging with the required provider credentials. A partial score is not task completion.

## Local proof

`runs/osworld008-proof/` contains checkpoint and restore evidence. The initial isolation test saved a 0.05 partial header in A, observed 0.0 in B, forked A with 0.05 retained, and rewound A to 0.0. This is checkpoint proof, not a completed reimbursement task or a full model benchmark.

Current ready desktop: `osworld008-ready` ([open desktop](http://localhost:20074/vnc.html?autoconnect=1)). Clean checkpoint: `ck_b3c7dc27`.

Current progress desktop: `osworld008-continued` ([open desktop](http://localhost:20084/vnc.html?autoconnect=1)). Progress checkpoint: `ck_dc67224f`. Its restored form visibly retains `Chan Tai Man` and `Overseas_Travelling`, and evaluates to 0.05. Browser-storage restoration was verified on the live fork; default/resume and race-resume behavior passed two focused tests. No model benchmark run was performed.

The fully rebuilt image is `fork-osworld008:task008-complete-build` (`linux/amd64`). The live proof desktops use the equivalent storage-fix overlay and retained checkpoint images. Earlier temporary proof desktops were removed after verification.
