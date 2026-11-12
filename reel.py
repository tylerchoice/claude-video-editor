#!/usr/bin/env python3
"""
reel.py: a talking-head video editor for 9:16 Reels, TikToks and Shorts.

It cuts every pause, breath and filler between words, adds small spoken-word
captions and renders a 1080x1920 video. Runs on macOS and Windows.

Commands
  python reel.py doctor [--get-model]        check the setup (and download the speech model)
  python reel.py selftest                    make a spoken test video and edit it end to end
  python reel.py transcribe raw/myvideo.mov
  python reel.py cut myvideo --clean [--start 13.2] [--end 58.0]
  python reel.py render myvideo --small-captions [--speed 1.2]
  python reel.py open myvideo                open final.mp4 and show its folder

Everything for a video lives in out/<name>/.
"""
import argparse, glob, json, os, platform, re, shutil, subprocess, sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):   # Windows pipes default to a legacy code page
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
STYLE = json.loads((ROOT / "style.json").read_text(encoding="utf-8"))
WINDOWS = sys.platform == "win32"


def _ffmpeg_dirs():
    """Where to look for ffmpeg, best first: REEL_FFMPEG (a folder, or the ffmpeg file
    itself), Homebrew's ffmpeg-full (it has libass, which captions need), the usual
    Windows install places (winget, Chocolatey, Scoop, C:\\ffmpeg), then PATH."""
    env = os.environ.get("REEL_FFMPEG")
    if env:
        yield str(Path(env).parent if Path(env).is_file() else Path(env))
    if WINDOWS:
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            yield os.path.join(la, "Microsoft", "WinGet", "Links")
            yield from sorted(glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages",
                                                     "Gyan.FFmpeg*", "*", "bin")), reverse=True)
        yield os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin")
        yield os.path.join(str(Path.home()), "scoop", "shims")
        yield r"C:\ffmpeg\bin"
    else:
        yield "/opt/homebrew/opt/ffmpeg-full/bin"
        yield "/usr/local/opt/ffmpeg-full/bin"


def _find_ffmpeg():
    x = ".exe" if WINDOWS else ""
    for d in _ffmpeg_dirs():
        if os.path.isfile(os.path.join(d, "ffmpeg" + x)):
            p = os.path.join(d, "ffprobe" + x)
            return os.path.join(d, "ffmpeg" + x), p if os.path.isfile(p) else (shutil.which("ffprobe") or "ffprobe")
    return shutil.which("ffmpeg") or "ffmpeg", shutil.which("ffprobe") or "ffprobe"


FFMPEG, FFPROBE = _find_ffmpeg()


def run(cmd, cwd=None):
    try:
        return subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        sys.exit(f"Can't find {cmd[0]}. Run `python reel.py doctor` to see what's missing.")


def sh(cmd, cwd=None):
    r = run(cmd, cwd)
    if r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(map(str, cmd))}\n{r.stderr[-3000:]}")
    return r.stdout


def filter_file_args(path):
    """ffmpeg 7+ reads a filter graph from a file with -/filter_complex; older builds only
    know -filter_complex_script (newer ones removed it). Long cuts are too big for a
    Windows command line, so the graph always goes through a file."""
    v = re.search(r"version n?(\d+)\.", sh([FFMPEG, "-hide_banner", "-version"]))
    return ["-filter_complex_script" if v and int(v[1]) < 7 else "-/filter_complex", str(path)]


def whisper():
    """The speech model: models/<whisper_model> when it's there, else faster-whisper downloads
    it once. CPU by default (style.json "whisper_device"): "auto" picks an NVIDIA GPU on
    Windows, which then fails unless CUDA and cuDNN are installed."""
    import logging
    import ctranslate2
    from faster_whisper import WhisperModel
    ctranslate2.set_log_level(logging.ERROR)   # no "try another backend" tips in the output
    name = STYLE["whisper_model"]
    local = ROOT / "models" / name
    return WhisperModel(str(local) if (local / "model.bin").exists() else name,
                        device=STYLE.get("whisper_device", "cpu"), compute_type="int8")


def render_speed(name, given=None):
    """The speed out/<name>/final.mp4 was rendered at (for the check tools)."""
    if given is not None:
        return float(given)
    f = OUT / name / "render.json"
    return float(json.loads(f.read_text(encoding="utf-8")).get("speed", 1.0)) if f.exists() else 1.0


def from_phone(path):   # iPhone camera footage carries a "Core Media" handler tag
    return "Core Media" in sh([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream_tags=handler_name", "-of", "csv=p=0", str(path)])


def probe(path):
    data = json.loads(sh([FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_streams", "-show_format", str(path)]))
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    if rot in (90, 270):
        w, h = h, w
    return w, h, float(data["format"]["duration"])


_SYNC = {}


def av_sync(path):
    """(offset from analysis time to the container clock, fps). Analysis runs on
    aligned_audio(), which already sits on the container clock from 0, so the offset is
    only a non-zero video start (rare)."""
    if path not in _SYNC:
        data = json.loads(sh([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", str(path)]))
        a = next((x for x in data["streams"] if x["codec_type"] == "audio"), {})
        v = next(x for x in data["streams"] if x["codec_type"] == "video")
        num, den = map(float, v.get("r_frame_rate", "30/1").split("/"))
        off = -float(v.get("start_time", 0) or 0)
        _SYNC[path] = (off, num / den if den else 30.0)
    return _SYNC[path]


def snap_to_frames(segs, src):
    """Snap piece edges to whole video frames (on the container clock) so each piece's
    audio and video are the same length: otherwise concat pads the shorter stream and
    the audio drifts by up to a frame per piece. Starts round, ends round up, so no
    sound is lost; pieces never overlap."""
    import math
    off, fps = av_sync(src)
    out = []
    for s_, e_ in segs:
        a_ = round((s_ + off) * fps) / fps - off
        b_ = math.ceil((e_ + off) * fps - 1e-6) / fps - off
        if out and a_ < out[-1][1]:
            a_ = out[-1][1]
        if b_ - a_ > 0.02:
            out.append([a_, b_])
    return out


def workdir(name):
    d = OUT / name
    if not d.exists():
        sys.exit(f"No work folder for '{name}'. Run transcribe first.")
    return d


# ---------------------------------------------------------------- transcribe
def cmd_transcribe(a):
    src = Path(a.video).resolve()
    name = src.stem
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "source.txt").write_text(str(src), encoding="utf-8")

    if a.words:  # testing hook: use a prepared word list
        shutil.copy(a.words, d / "words.json")
    else:
        model = whisper()
        segs, _ = model.transcribe(aligned_audio(src), word_timestamps=True,
                                   vad_filter=False, language="en")
        words = []
        for s in segs:
            for w in s.words:
                t = w.word.strip()
                if t:
                    words.append({"w": t, "s": round(w.start, 3), "e": round(w.end, 3)})
        (d / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")

    words = json.loads((d / "words.json").read_text(encoding="utf-8"))
    write_transcript(words, d / "transcript_raw.txt")
    print(f"Transcribed {len(words)} words -> {d/'transcript_raw.txt'}")


def write_transcript(words, path):
    """One sentence per line, with its start time, so the hook can be found."""
    lines, cur, start = [], [], None
    for w in words:
        if start is None:
            start = w["s"]
        cur.append(w["w"])
        if re.search(r"[.?!]$", w["w"]):
            lines.append(f"[{start:6.2f}] {' '.join(cur)}")
            cur, start = [], None
    if cur:
        lines.append(f"[{start:6.2f}] {' '.join(cur)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- cut
STRETCH = 0.3   # a silence this long inside a word is a pause Whisper stretched over
EDGE = 0.35     # a cut may move out this far to reach where the voice really stops
TAIL = 0.15     # extra ring-out after the last word of each part
FILLERS = {"um", "uh", "umm", "uhh", "erm", "hmm", "mm"}
_AUDIO = {}


def aligned_audio(src):
    """The source's audio laid out on the file's own clock (16 kHz mono wav, cached).
    Every analysis (Whisper, loudness, voice) runs on this, so analysis time IS the time
    ffmpeg cuts on. Decoding the file directly drifted: phone audio can start 10-460 ms
    after the video, and corrupt packets get silently dropped (one take lost 196 ms at
    58.9 s), so cuts landed early and clipped the ends of words."""
    src = Path(src).resolve()
    st = src.stat()
    cache = ROOT / "cache"
    cache.mkdir(exist_ok=True)
    out = cache / f"{src.stem}-{st.st_size}-{int(st.st_mtime)}.wav"
    if not out.exists():
        sh([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn",
            "-af", "aresample=async=1:min_hard_comp=0.01:first_pts=0",
            "-ac", "1", "-ar", "16000", str(out)])
    return str(out)


def find_voice(src, start, end):
    """Where the voice actually is (Silero VAD). Breaths, half-breaths and
    drawn-out pauses fall outside these regions even when they're not silent.
    Returns (voice regions, gaps between them)."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    sr = 16000
    if src not in _AUDIO:
        _AUDIO[src] = decode_audio(aligned_audio(src), sampling_rate=sr)
    a = _AUDIO[src]
    end = min(end if end is not None else len(a) / sr, len(a) / sr)
    v = STYLE.get("voice", {})
    ts = get_speech_timestamps(a[int(start * sr):int(end * sr)], VadOptions(
        threshold=v.get("threshold", 0.5), min_silence_duration_ms=v.get("min_gap_ms", 120),
        speech_pad_ms=v.get("pad_ms", 50), min_speech_duration_ms=60))
    voice = [(start + t["start"] / sr, start + t["end"] / sr) for t in ts]
    gaps = [(b, c) for (_, b), (c, _) in zip(voice, voice[1:])]
    if voice:
        gaps = [(start, voice[0][0])] + gaps + [(voice[-1][1], end)]
    return voice, [(g0, g1) for g0, g1 in gaps if g1 - g0 > 0.05]


def find_silences(src, start, end):
    """Silent stretches in the source audio (Whisper often stretches a word over a pause)."""
    end = end if end is not None else 1e9
    r = run([FFMPEG, "-hide_banner", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
             "-i", str(src), "-vn", "-af",
             f"silencedetect=n={STYLE.get('silence_db', -35)}dB:d={STYLE['max_gap']}",
             "-f", "null", "-"])
    starts = [float(x) + start for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) + start for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends + [end] * (len(starts) - len(ends))))


def trim_to_speech(words, silences):
    """Shrink each word so it doesn't cover a real pause at its start or end.
    Overlaps under STRETCH are soft word edges ("suffer-ing"), not pauses: keep them."""
    out = []
    for w in words:
        s, e = w["s"], w["e"]
        for a, b in silences:
            if a <= s < b and min(b, e) - s >= STRETCH:
                s = b
            if a < e <= b and e - max(a, s) >= STRETCH:
                e = a
        out.append(dict(w, s=round(s, 3), e=round(e, 3)) if e > s else w)
    return out


def subtract_silences(segs, silences, pad, max_gap):
    """Cut silences out of the middle of kept segments too (Whisper hides pauses inside words)."""
    for a, b in silences:
        a, b = a + pad, b - pad
        if b - a <= max_gap:
            continue
        out = []
        for s, e in segs:
            if b <= s or a >= e:
                out.append([s, e])
                continue
            if a > s:
                out.append([s, a])
            if b < e:
                out.append([b, e])
        segs = out
    return [x for x in segs if x[1] - x[0] >= 0.06]


_DB = {}


def room_floor(db):
    """The room's noise level: 20th percentile of the frames that aren't digital silence.
    Noise reduction in editing apps (CapCut, for one) gates pauses to exact zero, which
    put the plain percentile at -180 dB and made every faint sound look loud."""
    import numpy as np
    live = db[db > -100]
    return float(np.percentile(live if len(live) > len(db) * 0.2 else db, 20))


def loudness(src):
    """Loudness in dB for every 10 ms of the source audio (cached)."""
    import numpy as np
    from faster_whisper.audio import decode_audio
    if src not in _DB:
        if src not in _AUDIO:
            _AUDIO[src] = decode_audio(aligned_audio(src), sampling_rate=16000)
        a = _AUDIO[src]
        n = len(a) // 160
        fr = a[:n * 160].reshape(n, 160).astype("float64")
        _DB[src] = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    return _DB[src]


def tight_segments(src, words, start, end, duration, skips=()):
    """Millisecond jump cuts with no pauses or breaths left in.
    Every kept piece is built from the words themselves, so everything *between*
    words (pauses, breaths, half-breaths) is cut. Word edges follow the actual
    loudness: a word's end runs on while the sound stays loud (soft endings like
    "fun", "-tion", "judg-ment" stay whole) and stops at the first real dip, so a
    breath after it never gets in. Long silences hidden inside a stretched Whisper
    word are cut; short dips inside a word (consonants like the "s" in "solution")
    are kept."""
    import numpy as np
    t = STYLE.get("tight", {})
    db = loudness(src)
    # thresholds follow this take's own room noise (rooms differ by 5+ dB)
    floor = room_floor(db)
    Q_LO = floor + t.get("quiet_above_floor", 6)      # true silence, cut regardless
    Q_HI = floor + t.get("quiet_unvoiced_above_floor", 11)   # quiet *and* no voice = pause/breath
    LOUD = floor + t.get("loud_above_floor", 15)      # a word's ending runs on while above this
    EDGE_TRIM = t.get("edge_trim", 0.2)
    MIN_IN_GAP, MAX_EXT, MAX_BACK = t.get("min_inner_gap", 0.15), t.get("max_ext", 0.35), t.get("max_back", 0.15)
    PAD_S, PAD_E, MERGE = t.get("pad_start", 0.02), t.get("pad_end", 0.03), t.get("merge_gap", 0.04)
    F = 0.01
    sm = np.convolve(db, np.ones(3) / 3, mode="same")   # 30 ms smoothing: ignore single noise spikes
    lvl = lambda x: sm[min(len(sm) - 1, max(0, int(x / F)))]
    voice, _ = find_voice(src, max(0.0, start - 1), (end + 1) if end is not None else None)
    voiced = lambda x: any(v0 <= x < v1 for v0, v1 in voice)

    def quiet(x):   # soft speech stays (the voice detector hears it); hiss and breath don't
        v = lvl(x)
        return v < Q_LO or (v < Q_HI and not voiced(x))
    hi_end = end if end is not None else duration
    keep = [w for w in words
            if w["s"] >= start - 0.01 and w["s"] < hi_end - 0.02
            and re.sub(r"[^a-z]", "", w["w"].lower()) not in FILLERS
            and not any(a <= (w["s"] + w["e"]) / 2 < b for a, b in skips)]
    if not keep:
        sys.exit("No words left in that range.")
    allw = sorted(words, key=lambda w: w["s"])
    pieces, prev_end = [], start
    for i, w in enumerate(keep):
        ws, we = max(w["s"], start), max(w["e"], w["s"] + 0.05)
        nxt = min((x["s"] for x in allw if x["s"] > w["s"] + 0.01), default=duration)
        # Whisper stretches words over silence: trim quiet off both ends first
        # (only when it's a real stretch: a short soft tail is the end of the word —
        # "tomorrow", "project", "stack that" were getting shaved)
        ws0, we0 = ws, we
        while ws < we - 0.06 and quiet(ws):
            ws += F
        if ws - ws0 < EDGE_TRIM or w.get("hold"):
            ws = ws0
        while we > ws + 0.06 and quiet(we - F):
            we -= F
        if we0 - we < EDGE_TRIM or w.get("hold"):   # "hold": a soft ending set by hand
            we = we0
        # soft onset: reach back while it's still loud (never past the last word or the part start)
        while ws - F > max(prev_end, start, w["s"] - MAX_BACK) and lvl(ws - F) > LOUD:
            ws -= F
        # soft ending: run on while it's still loud, up to just before the next word
        while we + F < min(nxt - 0.02, w["e"] + MAX_EXT, duration) and lvl(we) > LOUD:
            we += F
        s0, e0 = max(prev_end, ws - PAD_S), min(we + PAD_E, max(nxt - 0.01, we))
        # long silences hidden inside a stretched word get cut; short dips are consonants
        x, run0, inner = s0, None, []
        while x < e0:
            if quiet(x) and not (w.get("hold") and x >= w["e"] - 0.4):
                run0 = x if run0 is None else run0
            else:
                if run0 is not None and x - run0 >= MIN_IN_GAP:
                    inner.append((run0 + 0.02, x - 0.02))
                run0 = None
            x += F
        cur = s0
        for a, b in inner:
            if a > cur:
                pieces.append([cur, a])
            cur = b
        if e0 > cur:
            pieces.append([cur, e0])
        prev_end = max(prev_end, e0)
    # merge pieces that touch, then cut skipped ranges exactly
    segs = []
    for s, e in sorted(pieces):
        if segs and s - segs[-1][1] <= MERGE:
            segs[-1][1] = max(segs[-1][1], e)
        else:
            segs.append([s, e])
    for a, b in skips:
        out = []
        for s, e in segs:
            if b <= s or a >= e:
                out.append([s, e])
                continue
            if a > s:
                out.append([s, a])
            if b < e:
                out.append([b, e])
        segs = out
    segs = snap_to_frames([x for x in segs if x[1] - x[0] >= t.get("min_piece", 0.08)], src)   # drop blips
    if not segs:
        sys.exit("No speech left in that range.")
    total = sum(e - s for s, e in segs)

    def to_new(t_):
        off = 0.0
        for s, e in segs:
            if t_ < s:
                return off
            if t_ <= e:
                return off + t_ - s
            off += e - s
        return off
    out_words = []
    for w in keep:
        s, e = to_new(w["s"]), to_new(w["e"])
        if e - s >= 0.02:
            out_words.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3)})
    return segs, out_words, total


def silence_segments(src, words, start, end, duration, skips=()):
    """For takes with clean pauses (mouth closed, quiet room): cut only where it is
    really silent — the room's own noise floor — so no word ending or soft start ever gets
    shaved. The tight cutter treats anything under floor+11 dB with no detected voice as
    a pause, which chops the fading tails of words ("generation", "capacity"). Here:
    sound = above floor+5 dB; silences shorter than min_pause are a breath between
    syllables and stay; a stretch of sound with no voice in it (a breath, a lip smack)
    goes; each kept stretch gets a little pad so the cut lands in silence, not on the word."""
    import numpy as np
    t = STYLE.get("clean", {})
    db = loudness(src)
    floor = room_floor(db)
    SIL = floor + t.get("silence_above_floor", 5)
    LOUD = floor + t.get("loud_above_floor", 12)   # a real syllable peaks above this
    MIN_PAUSE, PRE, POST = t.get("min_pause", 0.15), t.get("pad_start", 0.03), t.get("pad_end", 0.05)
    MAX_ONSET, MAX_TAIL = t.get("max_onset", 0.15), t.get("max_tail", 0.3)
    F = 0.01
    sm = np.convolve(db, np.ones(3) / 3, mode="same")
    lvl = lambda x: sm[min(len(sm) - 1, max(0, int(x / F)))]
    hi_end = end if end is not None else duration
    voice, _ = find_voice(src, max(0.0, start - 1), min(duration, hi_end + 1))
    runs, cur, x = [], None, start
    while x < hi_end:
        if lvl(x) > SIL:
            cur = x if cur is None else cur
        elif cur is not None:
            runs.append([cur, x]); cur = None
        x += F
    if cur is not None:
        runs.append([cur, hi_end])
    merged = []
    for r in runs:   # short silences inside a phrase are articulation, not a pause
        if merged and r[0] - merged[-1][1] < MIN_PAUSE:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    spoken = []
    for a, b in merged:
        loud = [k * F + a for k in range(int((b - a) / F)) if lvl(a + k * F) > LOUD]
        if not loud:
            # a soft last syllable after a short dip ("transforma-tion") never gets loud
            # and the voice detector misses it: keep it when it follows a word right away
            if spoken and a - spoken[-1][1] <= 0.2 and b - a <= 0.4:
                spoken[-1][1] = b
            continue   # otherwise it never got loud enough to be a syllable
        if not any(v0 < b and v1 > a for v0, v1 in voice):
            continue   # sound with no voice in it: a breath, a lip smack
        # a word fades out naturally (keep it), but a long low trail after it is an exhale
        spoken.append([max(a, loud[0] - MAX_ONSET), min(b, loud[-1] + MAX_TAIL)])
    segs = []
    for i, (a, b) in enumerate(spoken):
        lo = max(start, a - PRE, (segs[-1][1] if segs else start))
        nxt = spoken[i + 1][0] if i + 1 < len(spoken) else hi_end
        hi = min(hi_end, b + POST, (b + nxt) / 2 if i + 1 < len(spoken) else hi_end)
        segs.append([lo, hi])
    # a word marked "hold" in words.json keeps exactly its set span (a soft ending the
    # tail cap would shave, like the "-tion" of "transformation")
    held = [[max(start, w["s"]), min(hi_end, w["e"])] for w in words
            if w.get("hold") and start <= w["s"] < hi_end]
    if held:
        merged_ = []
        for s, e in sorted(segs + held):
            if merged_ and s <= merged_[-1][1] + 0.01:
                merged_[-1][1] = max(merged_[-1][1], e)
            else:
                merged_.append([s, e])
        segs = merged_
    for a, b in skips:
        out = []
        for s, e in segs:
            if b <= s or a >= e:
                out.append([s, e]); continue
            if a > s:
                out.append([s, a])
            if b < e:
                out.append([b, e])
        segs = out
    segs = snap_to_frames([x for x in segs if x[1] - x[0] >= 0.08], src)
    if not segs:
        sys.exit("No speech left in that range.")
    total = sum(e - s for s, e in segs)

    def to_new(t_):
        off = 0.0
        for s, e in segs:
            if t_ < s:
                return off
            if t_ <= e:
                return off + t_ - s
            off += e - s
        return off
    keep = [w for w in words if w["s"] >= start - 0.01 and w["s"] < hi_end - 0.02
            and not any(a <= (w["s"] + w["e"]) / 2 < b for a, b in skips)]
    out_words = []
    for w in keep:
        s, e = to_new(w["s"]), to_new(w["e"])
        if e - s >= 0.02:
            out_words.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3)})
    return segs, out_words, total


def build_segments(words, start, end, duration, silences=(), skips=(), voice=()):
    """Keep speech, drop every gap longer than max_gap. Fillers and skipped ranges removed."""
    pad, max_gap = STYLE["pad"], STYLE["max_gap"]
    keep = [w for w in words
            if w["s"] >= start - 0.01 and (end is None or w["s"] < end - 0.02)   # a word that starts in the part stays whole
            and re.sub(r"[^a-z]", "", w["w"].lower()) not in FILLERS
            and not any(a <= (w["s"] + w["e"]) / 2 < b for a, b in skips)]
    # Soft-spoken words and word edges can sit below the silence threshold
    # ("inside of you", "suffer-ing"). Unless the silence is a real pause stretched
    # across the word (>= STRETCH inside it), it's speech: don't cut it.
    def overlap(w, a, b):
        return min(w["e"], b) - max(w["s"], a)
    soft = [w for w in keep
            if 0 < sum(max(0, overlap(w, a, b)) for a, b in silences) < STRETCH
            or any(a <= w["s"] and w["e"] <= b and w["e"] - w["s"] <= 0.8 for a, b in silences)]
    # Protect only the part of a soft word that actually has voice in it — Whisper
    # sometimes places a short word inside the pause before it ("It", "but").
    spans = []
    for w in soft:
        if voice:
            spans += [(max(w["s"], v0), min(w["e"], v1)) for v0, v1 in voice
                      if min(w["e"], v1) - max(w["s"], v0) > 0.03]
        else:
            spans.append((w["s"], w["e"]))
    for s0, s1 in spans:
        out = []
        for a, b in silences:
            if a < s1 + pad and s0 - pad < b:
                out += [(a, s0 - pad), (s1 + pad, b)]
            else:
                out.append((a, b))
        silences = [(a, b) for a, b in out if b > a]
    # cut skipped ranges exactly (subtract_silences shrinks each range by pad)
    silences = list(silences) + [(a - pad, b + pad) for a, b in skips]
    if not keep:
        sys.exit("No words left in that range.")
    segs = []
    for w in keep:
        s, e = max(0.0, w["s"] - pad), min(duration, w["e"] + pad)
        # merge when the gap is short, or when the voice detector hears the voice carry
        # on across it (Whisper ends words early: "solu|tion" was being split)
        if segs and (s - segs[-1][1] <= max_gap
                     or any(v0 <= segs[-1][1] and s <= v1 for v0, v1 in voice)):
            segs[-1][1] = max(segs[-1][1], e)
        else:
            if segs and s < segs[-1][1]:
                s = segs[-1][1]
            segs.append([s, e])
    # Whisper's word edges are often early/late: if a cut lands inside a stretch of
    # voice that ends (or starts) just past it, move the edge out to where the voice
    # really stops, so the end of a word isn't clipped.
    for seg in segs:
        for r0, r1 in voice:
            if r0 <= seg[1] < r1 and r1 - seg[1] <= EDGE:
                seg[1] = min(duration, r1)
            if r0 < seg[0] <= r1 and seg[0] - r0 <= EDGE:
                seg[0] = max(0.0, r0, start - 0.02)   # never reach back before the part's start
    merged = []
    for s, e in sorted(segs):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    segs = subtract_silences(merged, silences, pad, max_gap)
    if not segs:
        sys.exit("No speech left in that range.")
    total = sum(e - s for s, e in segs)

    def to_new(t):  # source time -> cut timeline (times inside a removed gap snap forward)
        offset = 0.0
        for s, e in segs:
            if t < s:
                return offset
            if t <= e:
                return offset + t - s
            offset += e - s
        return offset

    # map every kept word onto the new timeline
    out_words = []
    for w in keep:
        s, e = to_new(w["s"]), to_new(w["e"])
        if e - s >= 0.02:
            out_words.append({"w": w["w"], "s": round(s, 3), "e": round(e, 3)})
    return segs, out_words, total


def vertical_filter(w, h):
    W, H = STYLE["width"], STYLE["height"]
    if w / h > W / H:   # too wide: crop the center
        return f"crop=ih*{W}/{H}:ih,scale={W}:{H},setsar=1"
    return f"scale={W}:-2,crop={W}:{H},setsar=1" if w / h < W / H else f"scale={W}:{H},setsar=1"


def retime_words(src, words, ranges, d):
    """Whisper's word times over a whole long take drift by a few tenths of a second,
    which clips words at cut points. Re-transcribing each part on its own (short
    window, a little padding) gives accurate times; replace the words there.
    The original list is kept once as words_orig.json."""
    from faster_whisper.audio import decode_audio
    if not (d / "words_orig.json").exists():
        (d / "words_orig.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    model = whisper()
    audio = decode_audio(aligned_audio(src), sampling_rate=16000)
    for start, end in ranges:
        a0 = max(0.0, start - 0.6)
        a1 = min(len(audio) / 16000, (end if end is not None else len(audio) / 16000) + 0.6)
        segs, _ = model.transcribe(audio[int(a0 * 16000):int(a1 * 16000)], language="en",
                                   word_timestamps=True,
                                   initial_prompt="Um, uh, so, like, I, I, you know.")
        fresh = [{"w": w.word.strip(), "s": round(w.start + a0, 3), "e": round(w.end + a0, 3)}
                 for s in segs for w in s.words if w.word.strip() and w.end - w.start >= 0.02]   # drop hallucinated zero-length words
        if not fresh:
            continue
        # replace everything in the window (not just between the fresh first/last
        # word), so an old copy of a word can't survive next to its new copy
        words = [w for w in words if not (a0 <= w["s"] < a1)] + fresh
        # neighbouring windows overlap: drop the second copy of the same word
        words.sort(key=lambda w: w["s"])
        dedup = []
        for w in words:
            if dedup and dedup[-1]["w"].lower().strip(".,?!") == w["w"].lower().strip(".,?!") \
                    and abs(dedup[-1]["s"] - w["s"]) < 0.35:
                continue
            dedup.append(w)
        words = dedup
    words.sort(key=lambda w: w["s"])
    (d / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    print(f"Retimed {len(ranges)} parts from the raw audio")
    return words


def cmd_cut(a):
    d = workdir(a.name)
    src = (d / "source.txt").read_text(encoding="utf-8").strip()
    words = json.loads((d / "words.json").read_text(encoding="utf-8"))
    w, h, dur = probe(src)
    skips = [tuple(map(float, r.split("-"))) for r in a.skip]
    raw_parts = set()   # "raw:A-B" keeps that range exactly as filmed (e.g. an opening edited elsewhere)
    if a.part:   # pieces of the take, joined in the order given
        ranges = []
        for r in a.part:
            if r.startswith("raw:"):
                raw_parts.add(len(ranges))
                r = r[4:]
            ranges.append(tuple(map(float, r.split("-"))))
    else:
        ranges = [(a.start if a.start is not None else words[0]["s"], a.end)]
    if a.retime:
        words = retime_words(src, words, ranges, d)

    # each range gets its own seeked input, so pieces can come in any order
    segs, new_words, total, inputs, pieces = [], [], 0.0, [], []
    for k, (start, end) in enumerate(ranges):
        if not a.loose:   # default: tight word-based cuts, nothing between words survives
            if k in raw_parts:
                rs = snap_to_frames([[start, end]], src)
                rt = rs[0][1] - rs[0][0]
                rw = [{"w": x["w"], "s": round(x["s"] - rs[0][0], 3), "e": round(min(x["e"], rs[0][1]) - rs[0][0], 3)}
                      for x in words if rs[0][0] - 0.01 <= x["s"] < rs[0][1] - 0.02]
            else:
                cutter = silence_segments if a.clean else tight_segments
                rs, rw, rt = cutter(src, words, start, end, dur, skips)
            if segs and segs[-1][0] < rs[0][0] < segs[-1][1]:
                rt -= segs[-1][1] - rs[0][0]
                rs[0][0] = segs[-1][1]
            base = max(0.0, start - 1)
            span = (end if end is not None else dur) + 1 - base
            inputs += ["-ss", f"{base:.3f}", "-t", f"{span:.3f}", "-i", src]
            pieces += [(k, s + av_sync(src)[0] - base, e + av_sync(src)[0] - base) for s, e in rs]
            new_words += [dict(x, s=round(x["s"] + total, 3), e=round(x["e"] + total, 3)) for x in rw]
            segs += rs
            total += rt
            continue
        silences = find_silences(src, max(0.0, start - 1), end)
        voice, breaths = find_voice(src, max(0.0, start - 1), (end + 1) if end else None)
        # Cut what the voice detector says isn't voice (pauses, breaths, half-breaths).
        # A quiet dip *inside* a voiced phrase is a soft word:
        # keep it, so only the parts of the loudness-based silences outside voice count.
        quiet = []
        for a_, b_ in silences:
            pieces_ = [(a_, b_)]
            for v0, v1 in voice:
                pieces_ = [p for q0, q1 in pieces_
                           for p in ((q0, min(q1, v0)), (max(q0, v1), q1)) if p[1] - p[0] > 0.02]
            quiet += pieces_
        silences = quiet + breaths
        rs, rw, rt = build_segments(trim_to_speech(words, silences), start, end, dur,
                                    silences, skips, voice)
        # let the last word of each part ring out: endings often trail off softly
        # ("fro-m", "selfish-ness") and a hard stop right at the voice edge sounds clipped
        # soft first sounds ("Th-at's") read as non-voice: start the part at its
        # first word's real start (retimed), never before the part's start point
        first = min((x["s"] for x in words if start - 0.01 <= x["s"] < (end or 1e9)), default=None)
        if first is not None and not any(a_ <= first < b_ for a_, b_ in skips):
            new0 = max(start, first - STYLE["pad"], rs[0][0] - 0.12)   # a soft onset, not a pause
            if new0 < rs[0][0]:
                rt += rs[0][0] - new0
                rs[0][0] = new0
        # back-to-back parts: never replay audio the previous part already used
        if segs and segs[-1][0] < rs[0][0] < segs[-1][1]:
            rt -= segs[-1][1] - rs[0][0]
            rs[0][0] = segs[-1][1]
        tail = min(TAIL, dur - rs[-1][1])
        mid_voice = any(v0 <= rs[-1][1] < v1 and v1 - rs[-1][1] > EDGE for v0, v1 in voice)
        in_skip = any(a <= rs[-1][1] < b for a, b in skips)
        if mid_voice and not in_skip and end is not None:
            # the speaker keeps talking past the cut: finish the last word right up to where
            # the next (unkept) word begins — Whisper marks word ends early
            nxt = min((x["s"] for x in words if x["s"] >= end - 0.02), default=None)
            if nxt is not None and nxt - 0.03 > rs[-1][1]:
                grow = min(nxt - 0.03, rs[-1][1] + 0.3) - rs[-1][1]
                rs[-1][1] += grow
                rt += grow
        elif not in_skip:
            rs[-1][1] += tail
            rt += tail
        # an explicit part end wins over the detectors: soft last syllables ("fun",
        # "judgment") read as non-voice, but the part end was set to include them
        if end is not None and not in_skip and end > rs[-1][1]:
            nxt = min((x["s"] for x in words if x["s"] >= end - 0.02), default=end + 1)
            stop = min(end, nxt - 0.03, dur)
            if stop > rs[-1][1]:
                rt += stop - rs[-1][1]
                rs[-1][1] = stop
        base = max(0.0, start - 1)
        span = (end if end is not None else dur) + 1 - base
        inputs += ["-ss", f"{base:.3f}", "-t", f"{span:.3f}", "-i", src]
        pieces += [(k, s + av_sync(src)[0] - base, e + av_sync(src)[0] - base) for s, e in rs]
        new_words += [dict(x, s=round(x["s"] + total, 3), e=round(x["e"] + total, 3)) for x in rw]
        segs += rs
        total += rt

    parts, labels = [], []
    half = 0.5 / av_sync(src)[1]   # video trims sit between frames, so ms rounding never adds a
    for i, (k, s, e) in enumerate(pieces):   # frame (that padded the audio 33 ms a time; one cut drifted 0.24 s)
        parts.append(f"[{k}:v]trim={max(0.0, s - half):.4f}:{e - half:.4f},setpts=PTS-STARTPTS[v{i}];"
                     f"[{k}:a]atrim={s:.4f}:{e:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.008,afade=t=out:st={max(0,e-s-0.008):.3f}:d=0.008[a{i}];")
        labels.append(f"[v{i}][a{i}]")
    fc = "".join(parts) + "".join(labels) + \
        f"concat=n={len(pieces)}:v=1:a=1[cv][ca];[cv]{vertical_filter(w, h)},fps=30[v]"
    (d / "cut.filter").write_text(fc, encoding="utf-8")
    sh([FFMPEG, "-y", *inputs, *filter_file_args(d / "cut.filter"),
        "-map", "[v]", "-map", "[ca]", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(d / "rough.mp4")])

    (d / "segments.json").write_text(json.dumps(segs), encoding="utf-8")
    (d / "words_cut.json").write_text(json.dumps(new_words, indent=1), encoding="utf-8")
    write_transcript(new_words, d / "transcript_cut.txt")
    sh([FFMPEG, "-y", "-ss", "1", "-i", str(d / "rough.mp4"), "-frames:v", "1",
        "-q:v", "3", str(d / "rough_still.jpg")])
    print(f"Rough cut: {dur:.1f}s -> {total:.1f}s ({len(segs)} pieces) -> {d/'rough.mp4'}")


# ------------------------------------------------------------------- render
FONTS_REL = "../../fonts"   # from out/<name>/, where render runs ffmpeg


def ass_time(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def chunk_words(words, lo=1, hi=None, max_chars=None, four_chars=None):
    """Caption chunks: usually lo..hi words, breaking on punctuation or a gap.
    max_chars caps how wide a chunk gets ("there's something you're not" is too wide);
    a 4th word is only allowed when the chunk stays narrow (<= four_chars). A word that
    ends a sentence joins the chunk before it if that still fits, else stands alone —
    it never runs into the next sentence. A lone comma word rolls into what follows."""
    hi = hi or STYLE["caption_words"]
    max_chars = max_chars or 999
    width = lambda ws: len(" ".join(x["w"] for x in ws))

    def full(cur, w):
        if width(cur + [w]) > max_chars:
            return True
        if len(cur) < hi:
            return False
        return not (four_chars and len(cur) == hi and width(cur + [w]) <= four_chars)

    chunks, cur = [], []
    for w in words:
        if cur and (full(cur, w) or (len(cur) >= lo and w["s"] - cur[-1]["e"] > 0.25)):
            chunks.append(cur); cur = []
        cur.append(w)
        end_ = re.search(r"[.?!]$", w["w"])
        if end_ or re.search(r",$", w["w"]):
            if len(cur) >= lo:
                chunks.append(cur); cur = []
            elif end_ and chunks and width(chunks[-1] + cur) <= max_chars:
                chunks[-1] += cur; cur = []
            elif end_:
                chunks.append(cur); cur = []
    if cur:
        if len(cur) < lo and chunks and width(chunks[-1] + cur) <= max_chars:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return chunks


# Caption breaks follow the "mouthful" idea: split by meaning,
# a piece you could say in one breath. Best breaks first: sentence end, ; : , —, before a
# new clause, before a phrase. Never right after a small connecting word.
CLAUSE = {"and", "but", "because", "which", "that", "when", "if", "though", "so", "or",
          "while", "where", "who", "until", "unless", "since"}
PHRASE = {"in", "with", "through", "without", "toward", "towards", "from", "of", "to", "for",
          "about", "at", "on", "by", "into", "as", "like", "over", "under", "after", "before"}
SMALL = {"the", "a", "an", "of", "to", "for", "and", "is", "his", "her", "my", "your", "their",
         "our", "its", "that", "this", "i", "in", "on", "at", "with", "by", "from", "be", "are",
         "was", "were", "it's", "i'm", "you're", "we", "you", "they", "he", "she", "it", "as",
         "but", "or", "so", "if", "than", "not", "do", "does", "how", "what", "who", "some", "more"}


def mouthfuls(words, lo=2, hi=3, max_chars=18, four_chars=14, keep=()):
    """Split caption words into short mouthfuls: best break points by meaning, within
    the size limits (usually lo..hi words, a narrow 4th, max_chars wide). Picks the
    cheapest set of breaks across each sentence instead of filling greedily."""
    # phrases to keep on screen together, even past the width rule
    # (layout.json "keep_together"): fuse their words into one unbreakable piece
    for ph in keep:
        toks = [re.sub(r"[^a-z0-9']", "", t) for t in ph.lower().split()]   # same normalizing as the words
        i = 0
        while i <= len(words) - len(toks):
            if [re.sub(r"[^a-z0-9']", "", x["w"].lower()) for x in words[i:i + len(toks)]] == toks:
                words = words[:i] + [{"w": " ".join(x["w"] for x in words[i:i + len(toks)]),
                                      "s": words[i]["s"], "e": words[i + len(toks) - 1]["e"]}] \
                    + words[i + len(toks):]
            i += 1
    bare = lambda w: re.sub(r"[^a-z0-9']", "", w["w"].lower())
    width = lambda ws: len(" ".join(x["w"] for x in ws))

    def break_cost(i):   # cost of a break after words[i]
        w, nxt = words[i]["w"], words[i + 1] if i + 1 < len(words) else None
        if nxt is None or re.search(r"[.?!]$", w):
            return 0
        if re.search(r"[;:,\u2014-]$", w):
            c = 1
        elif bare(nxt) in CLAUSE:
            c = 2
        elif bare(nxt) in PHRASE:
            c = 3
        else:
            c = 6
        if bare(words[i]) in SMALL and not re.search(r"[,;:]$", w):
            c += 20                       # never break right after a small word
        if nxt["s"] - words[i]["e"] > 0.25:
            c -= 1                        # the speaker actually pauses here
        return c

    def size_cost(ws):
        n, wd = len(ws), width(ws)
        if wd > max_chars and n > 1:
            return None
        if n > hi + 1 or (n == hi + 1 and wd > four_chars):
            return None
        return {1: 14}.get(n, 0) + (1 if n == hi + 1 else 0)

    out, start = [], 0
    for i, w in enumerate(words):         # solve one sentence at a time
        if re.search(r"[.?!]$", w["w"]) or i == len(words) - 1:
            sent = words[start:i + 1]
            n = len(sent)
            best = [0.0] + [float("inf")] * n
            back = [0] * (n + 1)
            for j in range(1, n + 1):
                for k in range(max(0, j - hi - 1), j):
                    sc = size_cost(sent[k:j])
                    if sc is None or best[k] == float("inf"):
                        continue
                    c = best[k] + sc + (break_cost(start + j - 1) if j < n else 0)
                    if c < best[j]:
                        best[j], back[j] = c, k
            if best[n] == float("inf"):   # nothing fits (very long words): greedy fallback
                out += [[x] for x in sent]
            else:
                cuts, j = [], n
                while j > 0:
                    cuts.append((back[j], j)); j = back[j]
                out += [sent[a:b] for a, b in reversed(cuts)]
            start = i + 1
    return out


def hook_lines(hook):
    return [l.strip() for l in hook.splitlines() if l.strip()]


def hook_layout(hook, at, photo_h):
    """Where the hook (and photo above it) go. Bottom keeps clear of TikTok's UI:
    the caption/username block at the bottom and the button rail on the right."""
    k, W, H = STYLE["hook"], STYLE["width"], STYLE["height"]
    gap = STYLE.get("photo", {}).get("gap", 30)
    ml, mr = 90, (k.get("margin_right_bottom", 170) if at != "top" else 90)
    if at == "above-photo":   # hook text, then the photo under it; photo bottom on the safe line
        mb = k.get("margin_bottom", 470)
        return {"align": 2, "ml": ml, "mr": mr, "mv": mb + photo_h + gap,
                "photo_y": H - mb - photo_h}
    # estimate wrapped line count so the photo can sit right above the text
    per_line = max(1, int((W - ml - mr) / (k["size"] * 0.40)))
    n = sum(-(-len(l) // per_line) for l in hook_lines(hook)) if hook else 0
    text_h = n * k["size"] * 1.2
    if at == "bottom":
        mv = k.get("margin_bottom", 470)
        photo_y = H - mv - text_h - gap - photo_h
        return {"align": 2, "ml": ml, "mr": mr, "mv": mv, "photo_y": photo_y}
    mv = k["margin_top"] + (photo_h + gap if photo_h else 0)
    return {"align": 8, "ml": ml, "mr": mr, "mv": mv, "photo_y": k["margin_top"]}


def build_ass(words, hook, total, captions=True, at="top", photo_h=0, small_mv=None, keep=(), small_x=None):
    """small_mv: use the small spoken-word captions, bottom edge
    `small_mv` px above the frame bottom, centered with the hook. small_x: pin
    their center to this x instead (e.g. to clear something held up in frame)."""
    S, W, H = STYLE, STYLE["width"], STYLE["height"]
    c, k, sc = S["captions"], S["hook"], S.get("small_captions", {})
    L = hook_layout(hook, at, photo_h)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{S['font']},{c['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{-1 if c['bold'] else 0},0,0,0,100,100,0,0,1,{c['outline']},0,2,90,90,{c['margin_bottom']},1
Style: Hook,{k.get('font', S['font'])},{k['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,{k.get('shadow_colour', '&H00000000')},{-1 if k['bold'] else 0},0,0,0,100,100,{k.get('spacing', 0)},0,1,{k['outline']},{k.get('shadow', 0)},{L['align']},{L['ml']},{L['mr']},{L['mv']},1
Style: Small,{sc.get('font', k.get('font', S['font']))},{sc.get('size', 46)},&H00FFFFFF,&H00FFFFFF,&H00000000,{k.get('shadow_colour', '&H00000000')},0,0,0,0,100,100,0,0,1,{sc.get('outline', 1.5)},{sc.get('shadow', 1)},2,{L['ml']},{L['mr']},{small_mv or c['margin_bottom']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if hook:
        hook_end = total if k["seconds"] in (0, None) else min(total, k["seconds"])
        text = r"\N".join(ass_escape(line) for line in hook_lines(hook))
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{text}")
    chunks = (mouthfuls(words, sc.get("min_words", 2), sc.get("max_words", 3),
                        sc.get("max_chars", 18), sc.get("four_word_chars", 14), keep)
              if small_mv is not None else chunk_words(words)) if captions else []
    for i, ch in enumerate(chunks):
        s = ch[0]["s"]
        e = chunks[i + 1][0]["s"] if i + 1 < len(chunks) else ch[-1]["e"] + 0.3
        e = min(e, ch[-1]["e"] + 0.6, total)
        text = " ".join(w["w"] for w in ch)
        if c.get("strip_end_punctuation"):
            text = re.sub(r"[.,]+$", "", text)
        if small_mv is not None:
            if sc.get("lowercase", True):
                text = text.lower()
            # \q2: never wrap, so every caption sits on the same fixed line
            pos = f"\\an2\\pos({small_x},{H - small_mv})" if small_x is not None else ""
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Small,,0,0,0,,{{\\q2{pos}}}{ass_escape(text)}")
        else:
            ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{ass_escape(text)}")
    return head + "\n".join(ev) + "\n"


def cmd_render(a):
    d = workdir(a.name)
    rough = d / "rough.mp4"
    if not rough.exists():
        sys.exit("Run cut first.")
    words = json.loads((d / "words_cut.json").read_text(encoding="utf-8"))
    _, _, total = probe(rough)
    speed = a.speed or 1.0
    lay0 = json.loads((d / "layout.json").read_text(encoding="utf-8")) if (d / "layout.json").exists() else {}

    def first_cut_1x():   # where the first jump cut lands on the unsped cut timeline
        segs_, o = json.loads((d / "segments.json").read_text(encoding="utf-8")), 0.0
        for (s0, e0), (s1, _) in zip(segs_, segs_[1:]):
            o += e0 - s0
            if s1 - e0 > 2:
                return o
        return total

    # per-section speed (layout.json "speed_map": [[0, 1.0], ["first_cut", 1.2]]):
    # e.g. the cold open at 1x and everything after it at 1.2x
    spans = None
    if lay0.get("speed_map"):
        pts = sorted((first_cut_1x() if t == "first_cut" else float(t), float(sp))
                     for t, sp in lay0["speed_map"])
        spans = [(t0, (pts[i + 1][0] if i + 1 < len(pts) else total), sp)
                 for i, (t0, sp) in enumerate(pts)]

        def fmap(t):
            out = 0.0
            for t0, t1, sp in spans:
                if t <= t0:
                    break
                out += (min(t, t1) - t0) / sp
            return out
    else:
        fmap = lambda t: t / speed
    if spans or speed != 1.0:   # captions and length follow the sped-up timeline
        words = [dict(w, s=fmap(w["s"]), e=fmap(w["e"])) for w in words]
        total = fmap(total)
    hook = a.hook or ""
    (d / "hook.txt").write_text(hook, encoding="utf-8")

    # optional photo above the hook
    inputs, photo_h, pre = ["-i", "rough.mp4"], 0, "[0:v]"
    if a.photo:
        photo = Path(a.photo)
        if not photo.is_absolute():
            photo = ROOT / "photos" / photo if (ROOT / "photos" / photo).exists() else Path.cwd() / photo
        if not photo.exists():
            sys.exit(f"No photo at {photo}")
        pw, ph = map(int, sh([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0", str(photo)]).strip().split(","))
        width = STYLE.get("photo", {}).get("width", 620)
        photo_h = round(ph * width / pw)
        inputs += ["-loop", "1", "-i", str(photo)]
    L = hook_layout(hook, a.hook_at, photo_h)
    W, H = STYLE["width"], STYLE["height"]

    # measure where the hook text really lands — no guessing at line wraps
    m = None
    if hook and (a.photo or a.small_captions):
        (d / "hook_measure.ass").write_text(build_ass([], hook, 1.0, False, a.hook_at, photo_h), encoding="utf-8")
        r = run([FFMPEG, "-hide_banner", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:d=0.1",
                 "-vf", f"subtitles=hook_measure.ass:fontsdir={FONTS_REL},bbox",
                 "-frames:v", "1", "-f", "null", "-"], cwd=d)
        m = re.search(r"x1:(\d+) x2:(\d+) y1:(\d+) y2:(\d+)", r.stderr)
    stack_top = int(m[3]) if m and a.hook_at != "top" else None

    if a.photo:
        # center the photo on the hook text and sit it `gap` px above it (bottom)
        x = (int(m[1]) + int(m[2])) / 2 - width / 2 if m else (W - width) / 2
        y = L["photo_y"]
        if a.hook_at == "bottom" and stack_top is not None:
            y = stack_top - STYLE.get("photo", {}).get("gap", 30) - photo_h
            stack_top = y
        if not hook and a.hook_at != "top":
            # no hook: photo on the safe line, lined up with the captions
            x = (L["ml"] + W - L["mr"]) / 2 - width / 2
            stack_top = y

    # free layout for this video (out/<name>/layout.json): any number of images and
    # text blocks at exact spots, e.g. a comment screenshot top-left + a still top-right
    lay = json.loads((d / "layout.json").read_text(encoding="utf-8")) if (d / "layout.json").exists() else {}
    # mirror the picture: layout.json "mirror": true/false, else style.json
    # "mirror_phone_footage" (flips iPhone footage). Captions, hook and images go on after
    # the flip so they read right, and side-placed ones swap sides with the speaker
    mirror = lay.get("mirror")
    if mirror is None:
        mirror = bool(STYLE.get("mirror_phone_footage")) and from_phone((d / "source.txt").read_text(encoding="utf-8").strip())
    print("Mirrored" if mirror else "Not mirrored")
    flip = (lambda x, w: W - x - w) if mirror else (lambda x, w: x)
    if lay.get("small_captions"):   # per-video caption look
        STYLE["small_captions"] = {**STYLE.get("small_captions", {}), **lay["small_captions"]}

    def when(v):   # seconds on the final timeline; "first_cut" = where the first jump cut lands
        if v != "first_cut":
            return v
        segs_, o = json.loads((d / "segments.json").read_text(encoding="utf-8")), 0.0
        for (s0, e0), (s1, _) in zip(segs_, segs_[1:]):
            o += e0 - s0
            if s1 - e0 > 2:
                return fmap(o)
        return total
    overlays = []   # (input index, width, x, y, shown from, shown until)
    if a.photo:
        overlays.append((1, width, x, y, None, None))
    for it in lay.get("images", []):
        f = Path(it["image"])
        f = f if f.is_absolute() else ROOT / "photos" / f
        overlays.append((inputs.count("-i"), it["w"], flip(it["x"], it["w"]), it["y"],
                         when(it["from"]) if it.get("from") is not None else None,
                         when(it["until"]) if it.get("until") is not None else None))
        inputs += ["-loop", "1", "-i", str(f)]

    # small captions sit just above the stack (photo, else hook text)
    small_mv = None
    if a.small_captions:
        gap = STYLE.get("small_captions", {}).get("gap", 24)
        small_mv = H - stack_top + gap if stack_top is not None else STYLE["captions"]["margin_bottom"]
        if lay.get("captions_y"):
            small_mv = H - lay["captions_y"]
    if lay.get("captions_from") is not None:   # no captions before this point
        t0 = when(lay["captions_from"])
        words = [w for w in words if w["s"] >= t0 - 0.01]
    ass = build_ass(words, hook, total, not a.no_captions, a.hook_at, photo_h, small_mv,
                    lay.get("keep_together", []),
                    flip(lay["captions_x"], 0) if lay.get("captions_x") is not None else None)
    # "captions_y_at": [{"from": 8.6, "until": 9.6, "y": 960}] (seconds after speed) moves the small
    # captions that start in that stretch to another bottom edge, e.g. while the speaker looks down onto them
    def y_at(line):
        p = line.split(",", 9)
        if not line.startswith("Dialogue:") or len(p) < 10 or p[3] != "Small":
            return line
        h_, m_, s_ = p[1].split(":")
        t = int(h_) * 3600 + int(m_) * 60 + float(s_)
        for z in lay.get("captions_y_at", []):
            if z["from"] - 0.01 <= t < z["until"]:
                p[7] = str(H - z["y"])
        return ",".join(p)
    if lay.get("captions_y_at"):
        ass = "\n".join(y_at(l) for l in ass.split("\n"))
    if lay.get("texts"):
        k = STYLE["hook"]
        styles, events = [], []
        for i, t in enumerate(lay["texts"]):
            styles.append(f"Style: Box{i},{t.get('font', k.get('font', STYLE['font']))},{t.get('size', 44)},"
                          f"&H00FFFFFF,&H00FFFFFF,&H00000000,{k.get('shadow_colour', '&H00000000')},"
                          f"0,0,0,0,100,100,0,0,1,{t.get('outline', k['outline'])},{k.get('shadow', 0)},"
                          f"8,{flip(t['x'], t['w'])},{W - flip(t['x'], t['w']) - t['w']},{t['y']},1")
            txt = r"\N".join(ass_escape(l) for l in hook_lines(t["text"]))
            t_end = when(t["until"]) if t.get("until") is not None else total
            events.append(f"Dialogue: 1,{ass_time(t.get('from', 0))},{ass_time(t_end)},Box{i},,0,0,0,,{txt}")
        ass = ass.replace("\n\n[Events]", "\n" + "\n".join(styles) + "\n\n[Events]", 1)
        ass += "\n".join(events) + "\n"
    (d / "captions.ass").write_text(ass, encoding="utf-8")

    subs = f"subtitles=captions.ass:fontsdir={FONTS_REL}"
    hf = "hflip," if mirror else ""
    # "mirror_except": [[13.96, 31.77]] (rough-cut seconds) leaves those stretches unflipped, e.g. when
    # the phone turns round to film a screen, so the screen's text still reads right
    if mirror and lay.get("mirror_except"):
        off = "+".join(f"between(t,{a},{b})" for a, b in lay["mirror_except"])
        hf = f"hflip=enable='not({off})',"
    if spans:
        n_ = len(spans)
        fc = f"[0:v]{hf}split={n_}" + "".join(f"[vs{i}]" for i in range(n_)) + ";"
        for i, (t0, t1, sp) in enumerate(spans):
            fc += f"[vs{i}]trim={t0:.3f}:{t1:.3f},setpts=(PTS-STARTPTS)/{sp}[vt{i}];"
        fc += "".join(f"[vt{i}]" for i in range(n_)) + f"concat=n={n_}:v=1:a=0[sp];"
        cur = "[sp]"
    elif speed == 1.0:
        fc, cur = (f"[0:v]{hf[:-1]}[sp];", "[sp]") if mirror else ("", "[0:v]")
    else:
        fc, cur = f"[0:v]{hf}setpts=PTS/{speed}[sp];", "[sp]"
    for n, (idx, ow, ox, oy, t0, t1) in enumerate(overlays):
        on = ""   # timed images: only shown between from/until
        if t0 is not None or t1 is not None:
            on = f":enable='between(t,{t0 or 0:.3f},{t1 if t1 is not None else total:.3f})'"
        fc += (f"[{idx}:v]scale={ow}:-1,setsar=1[p{n}];"
               f"{cur}[p{n}]overlay=x={ox:.0f}:y={oy:.0f}:shortest=1{on}[o{n}];")
        cur = f"[o{n}]"
    fc += f"{cur}{subs}[v]"
    fc_video = fc
    # low thumps under a few lines (hands bumping the phone): layout.json
    # "lowcut": [{"from": 23.6, "until": 27.7, "hz": 150}] in rough-cut seconds; steep (2 passes)
    lc = "".join(f"highpass=f={c.get('hz', 150)}:enable='between(t,{c['from']},{c['until']})',"
                 for c in lay.get("lowcut", []) for _ in range(2))
    audio = ["-map", "0:a", "-c:a", "copy"]
    if lc and not spans and speed == 1.0:
        fc += f";[0:a]{lc[:-1]}[a]"
        audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    if spans:   # pitch stays natural with atempo
        n_ = len(spans)
        fc += f";[0:a]{lc}asplit={n_}" + "".join(f"[as{i}]" for i in range(n_)) + ";"
        for i, (t0, t1, sp) in enumerate(spans):
            fc += f"[as{i}]atrim={t0:.3f}:{t1:.3f},asetpts=PTS-STARTPTS,atempo={sp}[at{i}];"
        fc += "".join(f"[at{i}]" for i in range(n_)) + f"concat=n={n_}:v=0:a=1[a]"
        audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    elif speed != 1.0:   # pitch stays natural with atempo
        fc += f";[0:a]{lc}atempo={speed}[a]"
        audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]

    if a.preview is not None:   # one still to check the layout; final.mp4 untouched
        # seek after the filters so caption timing matches the real video
        sh([FFMPEG, "-y", *inputs, "-filter_complex", fc_video, "-ss", str(a.preview),
            "-map", "[v]", "-frames:v", "1", "-q:v", "2", "preview.jpg"], cwd=d)
        print(f"Preview: {d/'preview.jpg'}")
        return

    (d / "render.json").write_text(json.dumps({"speed": speed, "speed_map": lay0.get("speed_map")}), encoding="utf-8")
    sh([FFMPEG, "-y", *inputs, "-filter_complex", fc, "-map", "[v]", *audio,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart", "final.mp4"], cwd=d)
    sh([FFMPEG, "-y", "-ss", str(min(0.5, total / 2)), "-i", str(d / "final.mp4"),
        "-frames:v", "1", "-q:v", "2", str(d / "cover.jpg")])
    print(f"Final: {d/'final.mp4'} ({total:.1f}s)\nCover: {d/'cover.jpg'}")


# ------------------------------------------------------------ setup and checks
def cmd_doctor(a):
    """Check everything the editor needs and print the fix for anything missing."""
    problems = []

    def check(good, what, fix):
        print(f"  {'ok     ' if good else 'MISSING'}  {what}")
        if not good:
            problems.append(fix)
        return bool(good)

    print(f"{platform.system()} {platform.release()} ({platform.machine()}), "
          f"Python {platform.python_version()}, editor folder {ROOT}")
    check((3, 10) <= sys.version_info[:2] <= (3, 13), "Python 3.10 to 3.13 (3.12 is the tested one)",
          "Install Python 3.12 (" + ("winget install -e --id Python.Python.3.12" if WINDOWS
                                     else "brew install python@3.12") + ") and rebuild .venv with it.")
    venv_py = r".venv\Scripts\python.exe" if WINDOWS else ".venv/bin/python"
    check(sys.prefix != sys.base_prefix, "running inside the .venv virtual environment",
          f"Run reel.py with {venv_py} (create it first, see README).")
    try:
        import faster_whisper, av, numpy
        pk = check(True, f"faster-whisper {faster_whisper.__version__}, av {av.__version__}, "
                         f"numpy {numpy.__version__}", "")
    except ImportError as e:
        pk = check(False, f"Python packages ({e.name} is not installed)",
                   f"{venv_py} -m pip install -r requirements.txt")

    fix_ff = ("Install ffmpeg with caption support: " +
              ("winget install -e --id Gyan.FFmpeg , then open a new terminal or restart Claude Code."
               if WINDOWS else "brew install ffmpeg-full (reel.py finds it on its own).") +
              " Or set REEL_FFMPEG to the folder that has ffmpeg in it.")
    ff = check(shutil.which(FFMPEG) or os.path.isfile(FFMPEG), f"ffmpeg ({FFMPEG})", fix_ff)
    if ff:
        print("           " + run([FFMPEG, "-hide_banner", "-version"]).stdout.split("\n")[0][:90])
        check(re.search(r"\bsubtitles\b", run([FFMPEG, "-hide_banner", "-filters"]).stdout),
              "ffmpeg can draw captions (libass)", fix_ff)
    check(shutil.which(FFPROBE) or os.path.isfile(FFPROBE), f"ffprobe ({FFPROBE})", fix_ff)

    fonts = sorted(p.name for p in (ROOT / "fonts").glob("*.ttf"))
    check({"TikTokSans-SemiBold.ttf", "TikTokSans-Bold.ttf"} <= set(fonts),
          f"caption fonts in fonts/ ({', '.join(fonts) or 'none'})",
          "Put TikTokSans-SemiBold.ttf and TikTokSans-Bold.ttf back in fonts/ (from the original zip).")

    name = STYLE["whisper_model"]
    local = ROOT / "models" / name
    if a.get_model and pk and not (local / "model.bin").exists():
        print(f"  Downloading the {name} speech model into models/{name} (small.en is about 480 MB) ...")
        from faster_whisper import download_model
        download_model(name, output_dir=str(local))
    check((local / "model.bin").exists(), f"speech model in models/{name}",
          f"{venv_py} reel.py doctor --get-model   (downloads it once; small.en is about 480 MB)")

    if pk and ff:   # the two pieces that broke most often together: ffmpeg and PyAV
        import tempfile
        ok = False
        with tempfile.TemporaryDirectory() as t:
            w = Path(t) / "tone.wav"
            r = run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5", w])
            try:
                from faster_whisper.audio import decode_audio
                ok = r.returncode == 0 and 7000 < len(decode_audio(str(w), sampling_rate=16000)) < 9000
            except Exception as e:
                print(f"           {type(e).__name__}: {e}")
        check(ok, "audio decoding works", f"{venv_py} -m pip install av==15.1.0   "
                                          "(newer av versions break faster-whisper 1.2.1)")
    if problems:
        print("\nFix these, then run doctor again:")
        for p in dict.fromkeys(problems):
            print(f"  - {p}")
        sys.exit(1)
    print(f"\nEverything is ready. Next: {venv_py} reel.py selftest")


def _speak(text, wav):
    """Text to speech with what the computer already has: `say` on macOS, the built-in
    Windows voices through PowerShell, espeak on Linux."""
    if sys.platform == "darwin":
        aiff = wav.with_suffix(".aiff")
        sh(["say", "-o", aiff, text])
        sh([FFMPEG, "-y", "-v", "error", "-i", aiff, wav])
    elif WINDOWS:
        p = str(wav).replace("'", "''")
        sh(["powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{p}'); $s.Speak('{text}'); $s.Dispose()"])
    else:
        tts = shutil.which("espeak-ng") or shutil.which("espeak")
        if not tts:
            sys.exit("selftest needs a text-to-speech voice: install espeak-ng.")
        sh([tts, "-w", wav, text])


def cmd_selftest(a):
    """Speak four lines with pauses between them, edit the take end to end and check it."""
    import tempfile
    lines = ["This is a quick test of the reel editor.",
             "Every pause between these lines should be cut out.",
             "The captions should follow along, a few words at a time.",
             "And that is the end of the test."]
    name = "_selftest"
    d = OUT / name
    shutil.rmtree(d, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(prefix="reel-selftest-"))
    fails = []

    def check(good, what, detail=""):
        print(f"  {'pass' if good else 'FAIL'}  {what}{detail}")
        if not good:
            fails.append(what)

    try:
        print("Making a spoken test video ...")
        ins, fc = [], ""
        for i, text in enumerate(lines):
            w = tmp / f"line{i}.wav"
            _speak(text, w)
            ins += ["-i", w]
            fc += f"[{i}:a]aresample=48000,aformat=channel_layouts=mono,apad=pad_dur=1.2[a{i}];"
        fc += "".join(f"[a{i}]" for i in range(len(lines))) + f"concat=n={len(lines)}:v=0:a=1[a]"
        src = tmp / f"{name}.mp4"
        sh([FFMPEG, "-y", "-v", "error", *ins, "-f", "lavfi", "-i", "color=c=0x1f2a36:s=720x1280:r=30",
            "-filter_complex", fc, "-map", f"{len(lines)}:v", "-map", "[a]", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src])
        src_len = probe(src)[2]
        for step in (["transcribe", src], ["cut", name, "--clean"], ["render", name, "--small-captions"]):
            print(f"Running {step[0]} ...")
            r = run([sys.executable, ROOT / "reel.py", *step])
            if r.returncode != 0:
                print(r.stdout + r.stderr)
                check(False, f"{step[0]} ran")
                break
            print("  " + (r.stdout.strip().split("\n") or [""])[-1])
        else:
            words = json.loads((d / "words.json").read_text(encoding="utf-8"))
            said = " ".join(w["w"] for w in words).lower()
            heard = all(k in said for k in ("quick test", "pause", "caption", "end of the test"))
            check(len(words) >= 30 and heard, "speech recognised",
                  f" ({len(words)} words)" + ("" if heard else f': heard "{said}"'))
            cut_len = probe(d / "rough.mp4")[2]
            check(cut_len < src_len - 2.5, f"pauses cut ({src_len:.1f} s -> {cut_len:.1f} s)")
            fw, fh, fin_len = probe(d / "final.mp4")
            check((fw, fh) == (STYLE["width"], STYLE["height"]) and abs(fin_len - cut_len) < 0.3,
                  f"final.mp4 is {fw}x{fh}, {fin_len:.1f} s")
            n = (d / "captions.ass").read_text(encoding="utf-8").count(",Small,")
            check(n >= 8, f"{n} captions written")
            # the captions must come out in the bundled font, not a system fallback
            r = run([FFMPEG, "-hide_banner", "-v", "verbose", "-f", "lavfi", "-i",
                     f"color=black:s={STYLE['width']}x{STYLE['height']}:r=10:d={min(fin_len, 6):.1f}",
                     "-vf", f"subtitles=captions.ass:fontsdir={FONTS_REL}", "-f", "null", "-"], cwd=d)
            used = re.findall(r"fontselect: \((.*?)\) -> (.*)", r.stderr)
            good = bool(used) and all("TikTokSans" in f for _, f in used)
            check(good, "captions use the bundled TikTok Sans font",
                  "" if good else f" (got: {used[:2]})" if used else " (ffmpeg didn't say which font it used)")
            check((d / "cover.jpg").exists(), "cover.jpg written")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if not a.keep:
            shutil.rmtree(d, ignore_errors=True)
            for c in (ROOT / "cache").glob(f"{name}-*.wav"):
                c.unlink()
    if fails:
        print(f"\nSelf-test FAILED: {', '.join(fails)}. Run `python reel.py doctor` to check the setup.")
        sys.exit(1)
    print("\nSelf-test passed: the editor works on this computer.")
    if a.keep:
        a.name = name
        cmd_open(a)


def cmd_open(a):
    """Open the finished video and show it in its folder (Finder or File Explorer)."""
    d = workdir(a.name)
    f = next((d / n for n in ("final.mp4", "rough.mp4") if (d / n).exists()), None)
    if f is None:
        sys.exit(f"Nothing rendered yet in {d}.")
    if WINDOWS:
        subprocess.Popen(f'explorer /select,"{f}"')
        os.startfile(str(f))
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(f)])
        subprocess.run(["open", str(f)])
    else:
        subprocess.run(["xdg-open", str(d)])
        subprocess.run(["xdg-open", str(f)])
    print(f"Opened {f}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    t = sp.add_parser("transcribe"); t.add_argument("video"); t.add_argument("--words", help=argparse.SUPPRESS)
    dr = sp.add_parser("doctor", help="check the setup and say how to fix anything missing")
    dr.add_argument("--get-model", action="store_true", help="download the speech model into models/")
    st = sp.add_parser("selftest", help="make a short spoken test video and edit it end to end")
    st.add_argument("--keep", action="store_true", help="keep out/_selftest and open the result")
    op = sp.add_parser("open", help="open final.mp4 and show it in its folder")
    op.add_argument("name")
    c = sp.add_parser("cut"); c.add_argument("name")
    c.add_argument("--start", type=float, help="raw-video second where the real start (hook) begins")
    c.add_argument("--end", type=float, help="raw-video second to stop at")
    c.add_argument("--skip", action="append", default=[], metavar="START-END",
                   help="raw-video seconds to cut out, e.g. 351.6-353.1 (repeatable)")
    c.add_argument("--loose", action="store_true",
                   help="old cutter (voice-detector based); default is the tight word-based cutter")
    c.add_argument("--clean", action="store_true",
                   help="cut only on real silence (takes with clean pauses): "
                        "no word tail or soft start is ever shaved")
    c.add_argument("--retime", action="store_true",
                   help="re-transcribe each part on its own for accurate word times (fixes clipped words)")
    c.add_argument("--part", action="append", default=[], metavar="START-END",
                   help="raw-video range to use instead of --start/--end; repeat to join "
                        "several, in the order given; prefix raw: to keep a range exactly as filmed")
    r = sp.add_parser("render"); r.add_argument("name"); r.add_argument("--hook", default="")
    r.add_argument("--no-captions", action="store_true", help="hook only, no spoken-word captions")
    r.add_argument("--small-captions", action="store_true",
                   help="small lowercase spoken-word captions, 2-3 words at a time")
    r.add_argument("--speed", type=float, default=1.0, help="e.g. 1.2 = 20%% faster, pitch kept")
    r.add_argument("--hook-at", choices=["top", "bottom", "above-photo"], default="top",
                   help="bottom = above TikTok's caption area, clear of the button rail")
    r.add_argument("--photo", help="image (in photos/ or a path) shown just above the hook")
    r.add_argument("--preview", type=float, metavar="SECONDS",
                   help="write preview.jpg at this time instead of rendering final.mp4")
    a = p.parse_args()
    {"transcribe": cmd_transcribe, "cut": cmd_cut, "render": cmd_render, "doctor": cmd_doctor,
     "selftest": cmd_selftest, "open": cmd_open}[a.cmd](a)


if __name__ == "__main__":
    main()
