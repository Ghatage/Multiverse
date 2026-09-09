"""Assemble a 45-second picture cut; only the supplied opening has narration."""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output/picture45-apps"
OPENING = ROOT / "output/v3-opening/Multiverse-opening-v3.mp4"
PREVIOUS = ROOT / "output/v2-silent.mp4"
BROLL = Path.home() / "Downloads/multiverse-broll"
REQUEST = ROOT / "assets/v3/request-actions.mp4"
GRAPH = ROOT / "assets/live-graph-v2/dashboard-live.webm"
WALL = Path.home() / "Downloads/multiverse-tv-wall-10s.mp4"
DESTINATION = Path.home() / "Downloads/Multiverse-45s-picture-v6.mp4"

# The supplied wall is an editorial closing montage, not a concurrency metric.
# Existing large narration captions are removed from the earlier picture source.
SHOTS = [
    (OPENING, 0, 5, "null"),
    (PREVIOUS, 7.4, 2, "crop=1920:914:0:0,pad=1920:1080:0:0:color=0x0b0d13"),
    (REQUEST, 4.5, 5, "crop=1872:800:24:202,scale=1920:-2,pad=1920:1080:0:(oh-ih)/2:color=0x0b0d13"),
    (PREVIOUS, 20, 3, "crop=1752:534:84:365,scale=1920:-2,pad=1920:1080:0:(oh-ih)/2:color=0x0b0d13"),
    (GRAPH, 22, 8, "null"),
    (PREVIOUS, 38, 3, "crop=1920:914:0:0,pad=1920:1080:0:0:color=0x0b0d13"),
    (PREVIOUS, 46, 4, "crop=1920:914:0:0,pad=1920:1080:0:0:color=0x0b0d13"),
    (BROLL / "calc/03-format-sort.mp4", 8, 2.5, "crop=1280:720:0:0,scale=1920:1080"),
    (BROLL / "inkscape/01-geometric.mp4", 11, 2.5, "crop=1440:810:240:70,scale=1920:1080"),
    (WALL, 0, 10, "null"),
]


def run(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], check=True)


def render():
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (source, start, duration, crop) in enumerate(SHOTS):
        # These shots are unchanged from the preceding cut.
        cached = ROOT / f"output/picture45-live/shot-{i}.mp4"
        if i not in (7, 8) and cached.exists():
            shutil.copyfile(cached, OUT / f"shot-{i}.mp4")
            continue
        run("-ss", start, "-i", source, "-t", duration, "-an", "-vf",
            f"{crop},fps=30,setsar=1,setpts=PTS-STARTPTS", "-c:v", "libx264",
            "-preset", "veryfast", "-threads", 2, "-crf", 18, "-pix_fmt", "yuv420p", OUT / f"shot-{i}.mp4")
        print(f"Shot {i + 1}/{len(SHOTS)} complete", flush=True)
    manifest = OUT / "shots.txt"
    manifest.write_text("".join(f"file 'shot-{i}.mp4'\n" for i in range(len(SHOTS))))
    bed = "0.009*sin(2*PI*110*t)+0.006*sin(2*PI*164.81*t)+0.004*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.2*t))+0.025*sin(2*PI*55*t)*exp(-mod(t,0.5)*28)+0.005*sin(2*PI*6600*t)*exp(-mod(t,0.25)*60)"
    run("-f", "concat", "-safe", 0, "-i", manifest, "-i", OPENING, "-f", "lavfi", "-i",
        f"aevalsrc='{bed}':s=48000:d=45", "-filter_complex",
        "[1:a]apad=whole_dur=45[voice];[2:a]afade=t=in:d=1,afade=t=out:st=42:d=3[bed];"
        "[voice][bed]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[audio]",
        "-map", "0:v", "-map", "[audio]", "-c:v", "copy", "-c:a", "aac", "-ar", 48000,
        "-b:a", "192k", "-t", 45, "-movflags", "+faststart", "-metadata",
        "title=Multiverse 45-second picture cut - remaining narration pending", DESTINATION)


if __name__ == "__main__":
    render()
