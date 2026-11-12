"""Caption timing vs Whisper on final.mp4: each shown chunk's start against the spoken start of its first word.
usage: python tools/verify_final.py <name> [speed]   (speed defaults to the one the final was rendered at)
Aim for every caption within 100 ms. Whisper is checked in 14 s windows, since one long pass drifts."""
import json, os, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio

name = sys.argv[1]
speed = reel.render_speed(name, sys.argv[2] if len(sys.argv) > 2 else None)
d = reel.workdir(name); sr = 16000
norm = lambda s: re.sub(r"[^a-z0-9']", "", s.lower())
L = json.loads((d / "layout.json").read_text(encoding="utf-8")) if (d / "layout.json").exists() else {}
W = json.loads((d / "words_cut.json").read_text(encoding="utf-8"))
cf = L.get("captions_from", 0)
if not isinstance(cf, (int, float)):
    print(f'captions_from is "{cf}": checking every caption from the start')
    cf = 0
sc = reel.STYLE.get("small_captions", {})
shown = [w for w in W if w["s"] / speed >= cf - 0.01]
chunks = reel.mouthfuls(shown, sc.get("min_words", 2), sc.get("max_words", 3), sc.get("max_chars", 18),
                        sc.get("four_word_chars", 14), L.get("keep_together", []))
a = decode_audio(str(d / "final.mp4"), sampling_rate=sr); T = len(a) / sr
m = reel.whisper()
hw, w0 = [], 0.0
while True:
    w1 = min(T, w0 + 14)
    segs, _ = m.transcribe(a[int(w0 * sr):int(w1 * sr)], language="en", beam_size=5,
                           word_timestamps=True, condition_on_previous_text=False)
    for s in segs:
        for w in s.words:
            st = w.start + w0
            if (w0 == 0 or st >= w0 + 2) and (w1 >= T or st < w1 - 2):
                hw.append((norm(w.word), st))
    if w1 >= T:
        break
    w0 += 10
hw.sort(key=lambda x: x[1])
print("FINAL SAYS:", " ".join(x[0] for x in hw))
deltas = []
for c in chunks:
    first = norm(c[0]["w"].split()[0]); t = c[0]["s"] / speed
    cand = [s for wd, s in hw if wd == first and abs(s - t) < 0.8]
    text = " ".join(x["w"] for x in c)
    if not cand:
        print(f"  no match: {t:6.2f} {text}"); continue
    s = min(cand, key=lambda x: abs(x - t)); dl = (t - s) * 1000; deltas.append(dl)
    if abs(dl) > 100:
        print(f"  {t:6.2f} {text!r}: {dl:+.0f} ms")
deltas = np.array(deltas)
if len(deltas):
    print(f"{len(chunks)} captions, matched {len(deltas)}; caption minus spoken: median {np.median(deltas):+.0f} ms, "
          f"within 50 ms {100 * np.mean(abs(deltas) <= 50):.0f}%, within 100 ms {100 * np.mean(abs(deltas) <= 100):.0f}%")
else:
    print(f"{len(chunks)} captions, none matched to the speech")
