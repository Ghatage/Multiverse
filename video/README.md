# Multiverse: one-minute first cut

60 seconds, 1920 × 1080, 30 fps. Internal demo built with Slancha Studio’s deterministic HTML capture workflow.

The film uses real Debian container forks, native Inkscape screenshots, a live dashboard snapshot, three-transition warm replays, and checkpoint restoration. Inputs are scripted; no model calls or performance speedup claims. The A/B criterion checks three orbit paths in the actual SVG. Rewind checks that the later SVG is absent after restoration. Checkpoints restore supported saved state, not process memory.

`stage.py` records the local fixture through ReplayHooks and ingests its real transitions. `native.py` prepares and captures both Inkscape branches in parallel. `fixture/` contains the local demonstration site. Captured evidence and narration live in ignored `assets/`; frames and renders live in ignored `output/` and `.frames/`.

`capture.mjs` and `vendor/clock.js` come from the local Slancha Studio demo kit. Geist fonts also come from that kit. Narration uses the existing Dell Chatterbox service; the quiet music bed is synthesized for this cut.

With the captured assets present and the studio Playwright environment available:

```sh
node video/capture.mjs . --out "$PWD/video/output/silent.mp4"
ffmpeg -y -i video/output/silent.mp4 -i video/assets/mix.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -t 60 -movflags +faststart video/output/Multiverse-demo-v1.mp4
```

`script.json` contains narration; `scene.js` drives every frame through `hf-seek`. Capture assets are intentionally kept out of Git. This is a first cut for review, not a claim of autonomous model performance.
