"""Cut check: sync of rough.mp4 against the raw per piece, piece edges that drop sound,
and leftover quiet gaps inside the cut.
usage: python tools/check_cut.py <name>"""
import json
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio

name = sys.argv[1]; sr = 16000; hop = 160  # 10 ms frames
d = reel.workdir(name)
src = (d / "source.txt").read_text(encoding="utf-8").strip()
raw = decode_audio(reel.aligned_audio(src), sampling_rate=sr)
cut = decode_audio(str(d / "rough.mp4"), sampling_rate=sr)
segs = json.loads((d / "segments.json").read_text(encoding="utf-8"))
db = lambda x: 10 * np.log10(np.mean(x.reshape(-1, hop) ** 2, axis=1) + 1e-12)
lvl = db(raw[:len(raw) // hop * hop])
floor = np.percentile(lvl, 20)
loud = lvl > floor + 12          # voice-level frames (10 ms)

cors, offs, acc = [], [], 0.0
for s, e in segs:
    a = raw[int(s * sr):int(e * sr)]; b = cut[int(acc * sr):int(acc * sr) + len(a)]
    n = min(len(a), len(b))
    if n > sr // 5:
        a, b = a[:n], b[:n]
        lags = range(-800, 801, 4)   # ±50 ms
        cc = [np.corrcoef(a[800:n - 800], b[800 + L:n - 800 + L])[0, 1] for L in lags]
        k = int(np.nanargmax(cc)); offs.append(abs(lags[k]) / sr * 1000); cors.append(float(cc[k]))
    acc += e - s
if cors:
    print(f"  sync: correlation min {min(cors):.2f} median {np.median(cors):.2f}; offset {min(offs):.1f}..{max(offs):.1f} ms")

lose = []
for i, (s, e) in enumerate(segs):
    k = int(s * 100); before = 0
    while k - 1 >= 0 and loud[k - 1] and not (i and abs(segs[i - 1][1] - (k - 1) / 100) < 0.02): before += 10; k -= 1
    if i and abs(segs[i - 1][1] - s) < 0.02: before = 0
    k = int(e * 100); after = 0
    while k < len(loud) and loud[k]: after += 10; k += 1
    if i + 1 < len(segs) and abs(segs[i + 1][0] - e) < 0.02: after = 0
    if before > 40 or after > 40:
        lose.append((round(s, 2), round(e, 2), before, after))
print("  edges losing >40 ms of sound (start, end, ms before, ms after):", lose)

cl = db(cut[:len(cut) // hop * hop]) > floor + 6
gaps, run = [], 0
starts = np.cumsum([0] + [e - s for s, e in segs])
for k, v in enumerate(cl):
    if not v: run += 1
    else:
        if run >= 20:
            t = (k - run) / 100; j = np.searchsorted(starts, t, "right") - 1
            gaps.append((round(float(t), 2), run * 10, round(float(segs[j][0] + t - starts[j]), 2)))
        run = 0
print("  leftover quiet gaps >=0.2 s (cut time, ms, raw time):", gaps)
