"""Per piece of the cut: low rumble (80-150 Hz minus 300-3k, vs the median piece) and loudness
(90th percentile of 50 ms RMS, vs the median piece). Rumble +6 dB or more is worth flagging.
usage: python tools/pieces.py <name> [speed]   (speed defaults to the one the final was rendered at)"""
import json
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio

name = sys.argv[1]; sr = 16000
speed = reel.render_speed(name, sys.argv[2] if len(sys.argv) > 2 else None)
d = reel.workdir(name)
a = decode_audio(reel.aligned_audio((d / "source.txt").read_text(encoding="utf-8").strip()), sampling_rate=sr)
segs = json.loads((d / "segments.json").read_text(encoding="utf-8"))
def band(x, lo, hi):
    X = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2; F = np.fft.rfftfreq(len(x), 1 / sr)
    return 10 * np.log10(X[(F > lo) & (F < hi)].sum() + 1e-12)
rows, acc = [], 0.0
for s, e in segs:
    x = a[int(s * sr):int(e * sr)]; fr = x[:len(x) // 800 * 800].reshape(-1, 800)
    if not len(fr):
        acc += e - s; continue
    loud = np.percentile(20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9), 90)
    rows.append((band(x, 80, 150) - band(x, 300, 3000), loud, s, acc)); acc += e - s
mr, ml = np.median([r[0] for r in rows]), np.median([r[1] for r in rows])
for v, l, s, c in rows:
    flag = "  <- rumble" if v - mr >= 6 else ""
    print(f"raw {s:7.2f}  final {c / speed:5.1f}s  rumble {v - mr:+5.1f} dB  loudness {l - ml:+5.1f} dB{flag}")
