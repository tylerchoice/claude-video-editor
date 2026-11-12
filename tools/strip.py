"""Compact level strip: one char per 20 ms, hi band (>4k) over mid band (300-3k).
' ' <4 dB  . <8  : <12  | <18  # >=18 over the 20th-percentile floor. Strip content starts after a 12-char prefix.
usage: python tools/strip.py <name> [--rough] <from>-<to> [...]   (raw by default; --rough reads rough.mp4 on the cut timeline)"""
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; args = sys.argv[2:]; sr = 16000; n = 320
d = reel.workdir(name)
if args and args[0] == "--rough":
    args, path = args[1:], str(d / "rough.mp4")
else:
    path = reel.aligned_audio((d / "source.txt").read_text(encoding="utf-8").strip())
a = decode_audio(path, sampling_rate=sr)
F = np.fft.rfftfreq(n, 1 / sr)
k = len(a) // n
X = np.abs(np.fft.rfft(a[:k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2
hi = 10 * np.log10(X[:, F > 4000].sum(1) + 1e-12)
mid = 10 * np.log10(X[:, (F > 300) & (F < 3000)].sum(1) + 1e-12)
fh, fm = np.percentile(hi, 20), np.percentile(mid, 20)
ch = lambda d: " " if d < 4 else "." if d < 8 else ":" if d < 12 else "|" if d < 18 else "#"
for r in args:
    t0, t1 = map(float, r.split("-"))
    for j in range(int(round(t0 * 50)), int(round(t1 * 50)), 50):
        j1 = min(j + 50, int(round(t1 * 50)))
        print(f"{j / 50:7.2f} hi  " + "".join(ch(v - fh) for v in hi[j:j1]))
        print("        mid " + "".join(ch(v - fm) for v in mid[j:j1]))
    print()
