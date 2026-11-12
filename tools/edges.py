"""Stricter edge check than check_cut: sound in the mid OR hi band (floor+8 dB, 20 ms frames) that runs
across a piece edge. Catches soft endings check_cut misses, like the hiss and quiet vowel of "in-SANE".
Prints edges where more than 60 ms of sound continues past an end (or leads into a start) in the raw.
Unreliable on noise-reduced audio (pauses gated to digital silence): measure dBFS there instead.
usage: python tools/edges.py <name>"""
import json
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; sr = 16000; n = 320
d = reel.workdir(name)
a = decode_audio(reel.aligned_audio((d / "source.txt").read_text(encoding="utf-8").strip()), sampling_rate=sr)
F = np.fft.rfftfreq(n, 1 / sr); k = len(a) // n
X = np.abs(np.fft.rfft(a[:k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2
mid = 10 * np.log10(X[:, (F > 300) & (F < 3000)].sum(1) + 1e-12)
hi = 10 * np.log10(X[:, F > 4000].sum(1) + 1e-12)
act = (mid > np.percentile(mid, 20) + 8) | (hi > np.percentile(hi, 20) + 8)
segs = json.loads((d / "segments.json").read_text(encoding="utf-8"))
acc, bad = 0.0, []
for i, (s, e) in enumerate(segs):
    joined_next = i + 1 < len(segs) and abs(segs[i + 1][0] - e) < 0.02
    joined_prev = i > 0 and abs(segs[i - 1][1] - s) < 0.02
    if not joined_next:   # sound running on past the end
        j, after = int(round(e * 50)), 0
        while j < len(act) and act[j]:
            after += 20; j += 1
        if after > 60:
            bad.append(f"end   raw {e:7.2f} (cut {acc + e - s:6.2f}): {after} ms more sound")
    if not joined_prev:   # sound leading into the start
        j, before = int(round(s * 50)) - 1, 0
        while j >= 0 and act[j]:
            before += 20; j -= 1
        if before > 60:
            bad.append(f"start raw {s:7.2f} (cut {acc:6.2f}): {before} ms of sound before it")
    acc += e - s
print("\n".join(bad) if bad else "no edges with sound running past them")
