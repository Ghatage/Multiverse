"""Render the specified five-second opening from actual desktop captures."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets/v3"
OUT = ROOT / "output/v3-opening"


def render():
    OUT.mkdir(parents=True, exist_ok=True)
    durations = json.loads((ASSETS / "opening-durations.json").read_text())
    gap = (5 - sum(durations)) / 3
    if gap < 0.08:
        raise ValueError("Opening narration needs more room inside five seconds")
    frames = [round((duration + gap) * 30) for duration in durations[:2]]
    frames.append(150 - sum(frames))
    shots = ["desktop", "request", "document"]
    for i, (shot, count) in enumerate(zip(shots, frames)):
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "30",
            "-i", str(ASSETS / f"{shot}.png"), "-i", str(ASSETS / f"opening-{i}.wav"),
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0b0d13,setsar=1",
            "-af", "adelay=60:all=1,apad", "-t", str(count / 30),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-b:a", "192k", str(OUT / f"shot-{i}.mp4"),
        ], check=True)
    manifest = OUT / "shots.txt"
    manifest.write_text("".join(f"file 'shot-{i}.mp4'\n" for i in range(3)))
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-vf", "setpts=PTS-STARTPTS", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-af", "asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac",
        "-ar", "48000", "-b:a", "192k", "-t", "5", "-movflags", "+faststart",
        str(OUT / "Multiverse-opening-v3.mp4"),
    ], check=True)


if __name__ == "__main__":
    render()
