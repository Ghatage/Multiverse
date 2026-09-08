#!/bin/sh
# Reuse local assets only; no downloads or paid services.
set -eu
cd "$(dirname "$0")"
POOL=${POOL:-/Users/laul_pogan/Source/Multiverse-tv-wall/video/tv-wall/assets}
ORIGINAL=${ORIGINAL:-/Users/laul_pogan/Source/Multiverse}
BROWSER=${BROWSER:-/Users/laul_pogan/Downloads/multiverse-browser-fork}
mkdir -p assets output
cp "$POOL/near/hero.mp4" assets/hero.mp4
cp "$POOL/pool/p01.mp4" assets/fantasy.mp4
cp "$POOL/pool/p02.mp4" assets/desert.mp4
cp "$POOL/pool/p03.mp4" assets/forest.mp4
cp "$POOL/pool/p04.mp4" assets/city.mp4
for name in jupiter mars; do
 ffmpeg -v error -y -ss 8 -i "$BROWSER/film-$name.webm" -t 10 -vf 'scale=1280:720,fps=30' -an -c:v libx264 -preset fast -g 15 "assets/$name.mp4"
done
ffmpeg -v error -y -ss 5 -i "$BROWSER/source.webm" -t 10 -vf 'scale=1280:720,fps=30' -an -c:v libx264 -preset fast -g 15 assets/browser.mp4
ffmpeg -v error -y -ss 17.5 -t 10 -i "$ORIGINAL/video/output/v2-silent.mp4" -vf 'crop=720:450:152:440,scale=960:600' -an -c:v libx264 -preset fast assets/vector.mp4
