# Multiverse demo cuts

The current 45-second picture cut is rendered with `python3 video/picture45.py` to `~/Downloads/Multiverse-45s-picture-v6.mp4`. Direct cuts remove transition holds. Timeline: opening 0–5, fork 5–7, request actions 7–12, Inkscape comparison 12–15, live execution graph 15–23, replay 23–26, rewind 26–30, spreadsheet sorting and Inkscape drawing 30–35, and the full supplied TV wall 35–45. A synthesized pulse runs beneath the opening narration.

The graph is a real dashboard recording, not an animated snapshot. `live-graph-run.py` executes and observes four scripted browser actions in the local onboarding fixture; `capture-graph.mjs` records the dashboard as its persisted graph updates. Successful capture run `r_20260908-233634-577a` grew from 9 states / 18 transitions to 13 states / 22 transitions. The cut uses seconds 22–30 of that recording at normal speed, including its final two updates. Evidence lives in ignored `assets/live-graph-v2/evidence.json`. The local view at `http://127.0.0.1:8010` reads these execution records; container inspection is disabled, so desktop status there is persisted status. This capture does not claim checkpoint coverage or autonomous model input.

The 30–35 second inserts show actual application changes: Calc sorting fictional regional data and Inkscape drawing a star. They use normal-speed portions of `calc/03-format-sort.mp4` (8–10.5 seconds) and `inkscape/01-geometric.mp4` (11–13.5 seconds), cropped toward the work. Terminal and build-canvas inserts were removed. These are scripted desktop recordings, not autonomous performance evidence. The TV wall is user-supplied editorial montage; its visible third-party video attribution is preserved. No scale or throughput claim is derived from it.

Only the supplied opening words have narration in the 45-second picture cut. The exact 50-word script and the outline after “The fork” were not supplied, so the remaining narration is pending. Final playback review is also pending; frame and encoding checks do not certify audiovisual synchronization.

Revision 2: your desktop and your saved work lead the story. A short technical beat connects screenshots and interface trees to clicks, keystrokes, code, and checked outcomes. Branch trails, staggered desktop entrances, replay highlights, a rewind reveal, and sound accents add motion without changing the captured product evidence.

60 seconds, 1920 × 1080, 30 fps. Internal demo built with Slancha Studio’s deterministic HTML capture workflow.

The film uses real Debian container forks, native Inkscape screenshots, a live dashboard snapshot, three-transition warm replays, and checkpoint restoration. Inputs are scripted; no model calls or performance speedup claims. The A/B criterion checks three orbit paths in the actual SVG. Rewind checks that the later SVG is absent after restoration. Checkpoints restore supported saved state, not process memory.

`stage.py` records the local fixture through ReplayHooks and ingests its real transitions. `native.py` prepares and captures both Inkscape branches in parallel. `fixture/` contains the local demonstration site. Captured evidence and narration live in ignored `assets/`; frames and renders live in ignored `output/` and `.frames/`.

`capture.mjs` and `vendor/clock.js` come from the local Slancha Studio demo kit. Geist fonts also come from that kit. Narration uses the existing Dell Chatterbox service; the quiet music bed is synthesized for this cut.

With the captured assets present and the studio Playwright environment available:

```sh
node video/capture.mjs . --out "$PWD/video/output/v2-silent.mp4"
ffmpeg -y -i video/output/v2-silent.mp4 -i video/assets/v2-final-mix.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -t 60 -movflags +faststart video/output/Multiverse-demo-v2.mp4
```

`script.json` contains narration; `scene.js` drives every frame through `hf-seek`. Capture assets are intentionally kept out of Git. This is a first cut for review, not a claim of autonomous model performance.
