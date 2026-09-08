# Multiverse: Astra TV wall

Independent 10-second internal concept film. One cinematic player pulls back through a wall of 459 placed televisions. Surrounding screens fade to reveal an M built from the remaining TVs. The “Multiverse: Your Desktop. Forkable” lockup holds from 8.5 to 10 seconds while the footage keeps moving.

## Playback and rendering

Final preview: `output/multiverse-tv-wall-astra-10s.mp4`.

Serve `video/` with a local HTTP server, then open `tv-wall-astra/index.html`. The scene loops without audio. The MP4 includes an original synthesized stereo sound bed.

Use an existing Playwright installation through `node_modules` (this machine links `/Users/laul_pogan/Source/astra-reflex/node_modules`). No dependencies were installed.

```sh
sh video/tv-wall-astra/prepare.sh
node video/tv-wall-astra/capture.mjs --stills
node video/tv-wall-astra/capture.mjs
python3 video/tv-wall-astra/finish.py
```

`scene.js` defines an independent Canvas scene with a seconds-based `hf-seek` clock. The browser capture awaits media seeks and paints before taking each frame. Its server supports byte ranges; without those, Chromium can return stale video frames. `output/browser-check.json` records runtime and HTTP errors. `capture.mjs` writes 300 PNGs and encodes H.264. Media, render frames, and output are gitignored.

## Media and limits

- `hero.mp4`, `city.mp4`: Tears of Steel, Blender Foundation. Copied from the prepared local pool (source offsets 490 and 384 seconds).
- `fantasy.mp4`: Sintel trailer, Blender Foundation; prepared pool offset 20 seconds.
- `desert.mp4`: Caminandes: Gran Dillama, Blender Foundation; prepared pool offset 60 seconds.
- `forest.mp4`: Big Buck Bunny, Blender Foundation; prepared pool offset 200 seconds.
- `jupiter.mp4`, `mars.mp4`, `browser.mp4`: existing native-desktop Chromium recordings under `Downloads/multiverse-browser-fork`; trimmed past their blank lead-in. Browser find, scrolling, and alternate navigation are recorded activity.
- `vector.mp4`: crop of the Inkscape panel from `Multiverse/video/output/v2-silent.mp4`, 17.5–27.5 seconds. This is captured native-app outcome material with presentation motion, not a continuous recording of editing gestures.
- Audio: original deterministic synthesis in `finish.py`.

Open-movie authorship comes from the existing preparation manifest; licenses were not independently re-audited for external release. The player chrome, TV wall, M shape, and wordmark are authored film composition. Nine media sources repeat across the wall. This is not evidence of hundreds of live forked desktops. No live desktop instances are created by the scene. No Marvel media, external downloads, or paid API calls were used.

## Verified delivery

- 10.000 seconds; 1920×1080; H.264; 30 fps; 300 frames; stereo AAC audio.
- Actual Chromium execution passed for deterministic capture, live scene playback (`--live`), and encoded MP4 playback (`--playback`), with changing frames and no page or HTTP errors.
- Reviewed frames extracted from the encoded MP4 at 0, 3, 5, 8.5, and 9.967 seconds. The last two show the full M and wordmark. Contact sheet: `output/review-contact.jpg`.
- Delivery copy: `/Users/laul_pogan/Downloads/multiverse-tv-wall-astra-10s.mp4`.
- `preview.html` plays the encoded MP4 with native controls. Nodeterm display was unavailable because this process has no `NODETERM_NODE_ID`.
