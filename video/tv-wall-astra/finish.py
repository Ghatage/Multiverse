"""Generate an original ten-second sound bed, then mux the browser render."""
import math
import random
import struct
import subprocess
import wave
from pathlib import Path

root = Path(__file__).resolve().parent
rate = 48000
rng = random.Random(73)
audio = bytearray()
noise = 0.0
for i in range(rate * 10):
    t = i / rate
    fade = min(1.0, t / .3, (10-t) / .5)
    swell = .035 + .055 * min(t / 8.0, 1)
    pad = sum(math.sin(2 * math.pi * f * t + .15 * math.sin(t)) for f in (55, 82.4069, 110, 164.8138)) / 4
    noise = .94 * noise + .06 * rng.uniform(-1, 1)
    whoosh = noise * .65 * math.exp(-((t - 6.5) / 1.3) ** 2)
    hit = 0
    if t >= 8.15:
        d = t - 8.15
        hit = .13 * math.exp(-d * 2) * math.sin(2 * math.pi * (70*d + 10*(1-math.exp(-d*9))))
        hit += .035 * math.exp(-d*1.4) * sum(math.sin(2*math.pi*f*d) for f in (220,329.6276,440))/3
    v = (swell*pad + whoosh + hit) * fade
    for gain in (1.0,.96):
        audio.extend(struct.pack('<h', int(max(-1,min(1,v*gain))*32767)))
with wave.open(str(root/'output/sound.wav'), 'wb') as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes(audio)
subprocess.run(['ffmpeg','-v','error','-y','-i',str(root/'output/silent.mp4'),'-i',str(root/'output/sound.wav'),'-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-b:a','192k','-t','10','-movflags','+faststart',str(root/'output/multiverse-tv-wall-astra-10s.mp4')],check=True)
