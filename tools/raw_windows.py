"""Windowed Whisper over the raw (aligned audio): 14 s windows every 10 s, word times.
Its prompt makes Whisper write out fillers (um, uh) it usually leaves out.
usage: python tools/raw_windows.py <name> [t0 t1]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reel
from faster_whisper.audio import decode_audio
name = sys.argv[1]; sr = 16000
src = (reel.workdir(name) / "source.txt").read_text(encoding="utf-8").strip()
a = decode_audio(reel.aligned_audio(src), sampling_rate=sr)
t0 = float(sys.argv[2]) if len(sys.argv) > 2 else 0; t1 = float(sys.argv[3]) if len(sys.argv) > 3 else len(a) / sr
m = reel.whisper()
t = t0
while t < t1:
    seg, _ = m.transcribe(a[int(t * sr):int(min(t + 14, t1 + 2) * sr)], language="en", beam_size=5, word_timestamps=True,
                          condition_on_previous_text=False, initial_prompt="Umm, uh, so, like, um, you know, uh.")
    ws = [w for s in seg for w in s.words]
    print(f"[{t:6.1f}] " + " ".join(f"{w.word.strip()}@{t + w.start:.2f}" for w in ws))
    t += 10
