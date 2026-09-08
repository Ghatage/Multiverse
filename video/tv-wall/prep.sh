#!/usr/bin/env sh
# Cuts the footage pool for the TV wall into assets/ (ignored by git).
# Sources: Blender Foundation open movies (CC-BY: Tears of Steel, Sintel trailer, Big Buck Bunny,
# Caminandes, Elephants Dream teaser) plus our own Multiverse/Slancha renders.
set -eu
SRC=${SRC:?path to the downloaded source films}
OWN=${OWN:-$HOME/Downloads}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/assets
mkdir -p "$OUT/near" "$OUT/pool" "$OUT/mosaic"
LEN=14

cut() { # cut <in> <start> <out> <w> <h>; skips clips already cut
  [ -s "$3" ] && return 0
  ffmpeg -v error -y -ss "$2" -t $LEN -i "$1" \
    -vf "scale=$4:$5:force_original_aspect_ratio=increase,crop=$4:$5,fps=30,format=yuv420p" \
    -an -c:v libx264 -preset fast -crf 18 -g 15 -movflags +faststart "$3"
}

TOS=$SRC/tos.mov; SIN=$SRC/sintel_trailer.mp4; BBB=$SRC/big_buck_bunny_480p_h264.mov
CAM=$SRC/caminandes_gran_dillama.mp4; ED=$SRC/elephantsdream_teaser.mp4
MV=$OWN/multiverse-browser-fork-1080p.mp4; MVD=$OWN/Multiverse-demo-v1.mp4
GLASS=$OWN/Macro_shot_two_coupe_glasses.mp4; ASTRA=$OWN/astra-duel-preview.mp4
TOUR=$OWN/full-tour.webm; FORKDIR=$OWN/multiverse-browser-fork
MARS=$FORKDIR/film-mars.webm; JUP=$FORKDIR/film-jupiter.webm; SRCWEB=$FORKDIR/source.webm

# Individually driven TVs (hero + 14 neighbours), full HD.
i=0
for spec in "$TOS 490" "$TOS 384" "$SRCWEB 4" "$MARS 10" "$SIN 20" "$CAM 40" "$BBB 200" "$TOUR 0" \
            "$MV 14" "$CAM 100" "$GLASS 0" "$MV 30" "$JUP 10" "$TOS 46" "$TOS 504"; do
  set -- $spec
  if [ $i -eq 0 ]; then name=hero; else name=$(printf 'n%02d' $i); fi
  cut "$1" "$2" "$OUT/near/$name.mp4" 1920 1080 &
  i=$((i+1))
done
wait

# Pool for the mosaics, quarter HD.
i=0
for spec in "$TOS 490" "$SIN 20" "$CAM 60" "$BBB 200" "$TOS 384" "$CAM 40" "$MV 10" "$TOS 108" \
            "$SIN 14" "$CAM 100" "$GLASS 0" "$TOS 46" "$BBB 400" "$ASTRA 5" "$TOS 504" \
            "$TOS 384" "$TOS 432" "$TOS 324" "$TOS 480" "$TOS 516" "$TOS 50" "$BBB 100" "$SRCWEB 4" \
            "$BBB 450" "$MV 30" "$CAM 130" "$BBB 150" "$TOUR 3" "$MARS 10" "$JUP 12"; do
  set -- $spec
  cut "$1" "$2" "$OUT/pool/p$(printf '%02d' $i).mp4" 960 540 &
  i=$((i+1))
  [ $((i % 8)) -eq 0 ] && wait
done
wait
N=$i

# Four 6x6 mosaics, 1920x1128 (tile 320x188, screen 296x166 inside a dark bezel).
for v in 0 1 2 3; do
  inputs=""; filters=""; layout=""; k=0
  for row in 0 1 2 3 4 5; do for col in 0 1 2 3 4 5; do
    idx=$(( (k * 7 + v * 5 + row) % N ))
    off=$(awk "BEGIN{printf \"%.2f\", $v * 1.3 + ($k % 5) * 0.55}")
    inputs="$inputs -ss $off -t 12 -i $OUT/pool/p$(printf '%02d' $idx).mp4"
    flip=""; [ $(( (k + v) % 3 )) -eq 0 ] && flip="hflip,"
    filters="$filters[$k:v]${flip}scale=296:166,pad=320:188:9:9:color=0x0b0b0c,setsar=1[t$k];"
    layout="$layout|$((col*320))_$((row*188))"
    k=$((k+1))
  done; done
  layout=${layout#|}
  ins=""; j=0; while [ $j -lt $k ]; do ins="$ins[t$j]"; j=$((j+1)); done
  ffmpeg -v error -y $inputs -filter_complex "${filters}${ins}xstack=inputs=$k:layout=$layout,fps=30,format=yuv420p[out]" \
    -map "[out]" -t 12 -an -c:v libx264 -preset fast -crf 18 -g 15 -movflags +faststart "$OUT/mosaic/m$v.mp4"
done
ls -la "$OUT/near" "$OUT/mosaic"

# Native Inkscape outcome from the existing captured demo (presentation motion, not editing gestures).
VECTOR_SOURCE=${VECTOR_SOURCE:-/Users/laul_pogan/Source/Multiverse/video/output/v2-silent.mp4}
ffmpeg -v error -y -ss 17.5 -t 10 -i "$VECTOR_SOURCE" \
  -vf 'crop=720:450:152:440,scale=960:600' -an -c:v libx264 -preset fast "$OUT/near/vector.mp4"
