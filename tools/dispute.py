"""Level strips (rough.mp4, cut timeline) around caption starts, with C = caption start, W = Whisper start.
usage: python tools/dispute.py <name> <caption start, cut seconds>:<ms caption minus spoken, final timeline> ...
The final's speed comes from its render (out/<name>/render.json)."""
import numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; sr = 16000; n = 320
speed = reel.render_speed(name)
a = decode_audio(str(reel.workdir(name) / "rough.mp4"), sampling_rate=sr)
fr = np.lib.stride_tricks.sliding_window_view(np.pad(a, (0, n)), n)[::n][:len(a) // n]
spec = np.abs(np.fft.rfft(fr * np.hanning(n), axis=1)) ** 2; f = np.fft.rfftfreq(n, 1 / sr)
band = lambda m: (lambda x: x - np.percentile(x, 20))(10 * np.log10(spec[:, m].mean(1) + 1e-12))
hi, mid = band(f >= 4000), band((f >= 300) & (f < 3000))
ch = lambda v: " " if v < 4 else "." if v < 8 else ":" if v < 12 else "|" if v < 18 else "#"
for arg in sys.argv[2:]:
    c, d = arg.split(":"); c = float(c); w = c - float(d) * speed / 1000
    t0 = min(c, w) - 0.5; i0, i1 = int(t0 / .02), int((max(c, w) + 0.6) / .02)
    mk = [" "] * (i1 - i0)
    for t, s in ((c, "C"), (w, "W")):
        k = int(round(t / .02)) - i0
        if 0 <= k < len(mk): mk[k] = s if mk[k] == " " else "X"
    print(f"{c:7.2f} hi  " + "".join(ch(v) for v in hi[i0:i1]))
    print(f"        mid " + "".join(ch(v) for v in mid[i0:i1]))
    print(f"            " + "".join(mk) + f"   (strip from {t0:.2f})")
