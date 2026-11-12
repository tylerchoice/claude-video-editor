"""Whisper on raw slices (padded 0.6 s), to test candidate edges. Ranges joined with + are spliced.
usage: python tools/slice.py <name> 23.9-26.0 24.3-26.0 0.8-2+23.9-25 ..."""
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; sr = 16000
src = (reel.workdir(name) / "source.txt").read_text(encoding="utf-8").strip()
a = decode_audio(reel.aligned_audio(src), sampling_rate=sr)
m = reel.whisper()
pad = np.zeros(int(0.6 * sr), dtype=a.dtype)
for spec in sys.argv[2:]:
    parts = [tuple(map(float, r.split("-"))) for r in spec.split("+")]
    x = np.concatenate([pad] + [a[int(s * sr):int(e * sr)] for s, e in parts] + [pad])
    segs, _ = m.transcribe(x, language="en", beam_size=5, condition_on_previous_text=False)
    print(f"{spec:28s} {' '.join(s.text.strip() for s in segs)}")
