"""Sound runs in the raw (20 ms frames above floor+8 dB in the mid or hi band), to place part edges.
Unreliable on noise-reduced audio (pauses gated to digital silence): measure dBFS there instead.
usage: python tools/runs.py <name> <from>-<to> [...]"""
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; sr = 16000; n = 320
a = decode_audio(reel.aligned_audio((reel.workdir(name) / "source.txt").read_text(encoding="utf-8").strip()),
                 sampling_rate=sr)
F = np.fft.rfftfreq(n, 1 / sr); k = len(a) // n
X = np.abs(np.fft.rfft(a[:k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2
mid = 10 * np.log10(X[:, (F > 300) & (F < 3000)].sum(1) + 1e-12)
hi = 10 * np.log10(X[:, F > 4000].sum(1) + 1e-12)
act = (mid > np.percentile(mid, 20) + 8) | (hi > np.percentile(hi, 20) + 8)
for r in sys.argv[2:]:
    t0, t1 = map(float, r.split("-")); out, j = [], int(t0 * 50)
    while j < min(int(t1 * 50), len(act)):
        if act[j]:
            e = j
            while e < min(int(t1 * 50), len(act)) and act[e]:
                e += 1
            if e - j >= 2:
                out.append(f"{j / 50:.2f}-{e / 50:.2f}")
            j = e
        else:
            j += 1
    print(f"{r:14s} " + " ".join(out))
