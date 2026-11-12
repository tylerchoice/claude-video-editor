# Reel Editor

You edit raw talking-head videos into finished 9:16 videos (TikTok, Reels, Shorts) for the person using this folder. All editing runs through `reel.py`. The look lives in `style.json`. Checking tools live in `tools/`.

The style is simple on purpose: every pause, breath and filler cut; small captions timed to the words; nothing else (no music, zooms, emojis, colors or B-roll) unless the user asks.

## Running commands

Run everything from this folder with the folder's own Python:

- macOS: `.venv/bin/python reel.py …`
- Windows: `.venv/Scripts/python.exe reel.py …` (in PowerShell: `.venv\Scripts\python.exe reel.py …`)

Below, `python` means that interpreter. Tools run the same way: `python tools/check_cut.py <name>`.

If `.venv` doesn't exist, or `python reel.py doctor` reports anything missing, do the setup first.

## First-time setup

Do this when the user asks to set up, or when something is missing. Check what's already there before installing anything, and tell the user before you install system software.

**macOS**
1. Homebrew, if missing (https://brew.sh). The user has to type their password for it.
2. `brew install python@3.12 ffmpeg-full`. `ffmpeg-full` includes libass, which captions need, and reel.py finds it on its own.
3. `$(brew --prefix)/bin/python3.12 -m venv .venv`

**Windows 10/11 (64-bit)**
1. `winget install -e --id Python.Python.3.12` and `winget install -e --id Gyan.FFmpeg`. Windows may ask the user to approve each install.
2. `py -3.12 -m venv .venv`. If `py` isn't found right after installing, use `"$LOCALAPPDATA/Programs/Python/Python312/python.exe" -m venv .venv`, or restart Claude Code so it picks up the new PATH.
3. reel.py looks for ffmpeg in winget's folder, so a new terminal isn't needed. If it still can't find it, set `REEL_FFMPEG` to the folder that holds `ffmpeg.exe`.

**Both**
1. `python -m pip install -r requirements.txt` (with the .venv python). Keep `av` at 15.1.0: newer versions break faster-whisper 1.2.1.
2. `python reel.py doctor --get-model`. This checks everything and downloads the speech model once (about 480 MB) into `models/`.
3. `python reel.py selftest`. This speaks a short test take with the computer's own voice, edits it end to end and checks the result. Tell the user in two lines that it passed, and how to edit their first video.

## Editing a video: "edit <file>"

1. The user puts a video in `raw/` or gives a path. Any common format works.
2. `python reel.py transcribe <path>` creates `out/<name>/` with:
   - `words.json`: every word with start and end times.
   - `transcript_raw.txt`: one sentence per line with its time.
3. Read the transcript and decide what stays in (see below).
4. `python reel.py cut <name> --clean --part A-B [--part C-D …]` (raw seconds, joined in the order given). For one range, use `--start` / `--end`. `--skip A-B` drops a stumble inside a range. Add `--retime` on multi-part cuts.
5. Check the cut (see "Checking a cut").
6. Fix the caption text, place the captions with previews, then render:
   `python reel.py render <name> --small-captions [--speed 1.2]`
7. `python reel.py open <name>` opens the final video and shows it in Finder or File Explorer. Then report back (see "Delivering").

The work folder is named after the file. If the user names their videos (for example by posting day), ask for the name when none is given, and use it for the folder. For a second video from the same raw file, make a new folder and copy `source.txt`, `words.json` and `transcript_raw.txt` into it, then cut and render that name.

**Ask once, then remember.** Ask the user's preferences the first time they matter, rather than guessing:
- Speed.
- Caption position.
- Whether they want burned-in hook text.
- How they name videos.

Record each answer in a short "This user's preferences" section at the end of this file, so the next session follows it.

## What stays in

- **Scripted takes** (read from a script, often with several takes of each line): the whole script is the video. Keep every scripted line in order, including a cold open. Remove false starts and repeated takes (keep the last or cleanest one). If you can't tell a retake from a new line, keep it and ask.
- **Unscripted takes**: find the real start. That's the first sentence that:
  - takes a stance,
  - names something concrete, and
  - a stranger would understand alone.

  **Propose** cuts to the content (warm-ups, side tangents) with times. Don't make them without the user's OK.
- **Keep the thread.** Never cut a line that carries the logic from one idea to the next (a setup, a reason, a cause and effect). Before removing a line, check that the next line still follows. When unsure, keep it and ask.
- **Always cut** fillers (um, uh), false starts, stutters and repeated words, pauses and breaths. **Never clip a word.**
- **Spoken editing notes** ("cut that", "start again from…") are instructions to you. Follow them, cut the note itself, and mention it in the flags.
- Swearing stays as said unless the user asks otherwise.
- If the user sends a file they already edited themselves, build on their version, not an older cut.
- Whatever you propose is what goes in. If you suggest a beat, either put it in or say plainly that you left it out and why.

## How the cutter works

- `--clean` (the default to use): cuts only where the audio is back near the room's noise floor. Word endings keep their natural fade, and stretches with no voice (breaths, lip smacks) go. It does **not** remove fillers by itself, so skip them with `--skip` or by splitting parts.
- No flag: the tight word-based cutter. Everything between words goes and fillers are dropped automatically. It is more aggressive and can shave soft word endings.
- `--loose`: an older voice-detector cutter, rarely needed.
- `--part raw:A-B` keeps a range exactly as filmed (an opening already edited elsewhere).
- `--retime` re-transcribes each part on its own for accurate word times. It rewrites `words.json` for those parts, so hand fixes there are lost: recut without `--retime` after fixing words by hand.
- **Holds.** In `out/<name>/words.json`, a word with `"hold": true` keeps exactly its `s`–`e` span. Use holds for:
  - a soft ending the cutter shaved;
  - a quiet word the voice detector dropped;
  - a line's first word that retime stamped too early (hold it at its real onset).

  Two touching held words force their pieces to join, so a hold can also pin a gap open: don't overuse them.
- Analysis runs on the file's own audio clock (cached in `cache/`), so phone audio offsets and dropped packets don't shift cuts. `rough.mp4` is always 30 fps, even from a 60 fps or sped-up source.
- Pieces under 0.08 s are dropped. Part edges snap to whole frames.

## Checking a cut

Run these after every cut, and fix what they find before rendering:

- `python tools/check_cut.py <name>`:
  - **sync:** correlation per piece should be about 0.9 or higher at under 1 ms offset;
  - **edges:** any edge losing more than 40 ms of sound must be a deliberate cut between words, never a word's own tail;
  - **gaps:** leftover quiet gaps of 0.2 s or more inside the cut. Split the parts around them.
- `python tools/edges.py <name>`: a stricter edge check for soft endings (s, f and th sounds, trailing vowels).
- **Listen-back:** compare what the cut says with the plan, one part at a time. `python tools/slice.py <name> A-B …` transcribes raw slices with padding.
  - Every intended word must be there.
  - No syllable of a cut word may leak in.
  - If Whisper on the joined cut disagrees with an isolated raw slice, trust the slice.
- **Hidden fillers:** Whisper often leaves "um" out of transcripts. `python tools/raw_windows.py <name> [t0 t1]` uses a prompt that writes fillers out.
- **Placing part edges:**
  - `python tools/strip.py <name> A-B` draws a level strip, one character per 20 ms. Add `--rough` to read the cut instead of the raw.
  - `python tools/runs.py <name> A-B` lists sound runs.
  - Put a part's end where the raw goes quiet, not where Whisper says the word ends.
- **Noise-reduced audio** (exports from apps that gate pauses to digital silence): the floor-relative checks in `edges.py`, `runs.py` and `check_cut.py` treat gated room noise as sound. Measure absolute level instead. Speech sits above about −50 dBFS, and gated silence is below −60 dBFS.
- **Quiet speech:** `--clean` can drop a quiet word or a soft ending. Check low-level words in the listen-back and hold them.
- **Joins:** make sure each part still starts on its first word. Whisper's start times for a line's first word are unreliable in both directions.

## Captions

- `render --small-captions`: small lowercase captions in TikTok Sans SemiBold with an even outline (3.5) and no shadow.
- **Chunk size:**
  - 2–3 words per chunk.
  - A 4th word only if the chunk stays 14 characters or fewer.
  - Never wider than 18 characters.
  - A long word can stand alone.
- **Line breaks follow meaning** (a piece you could say in one breath): sentence end, then punctuation, then before a new clause (and, but, because…), then before a phrase (in, with, from…). Never break right after a small connecting word (the, a, of, to, is…).
- **Fix the text after the last cut**, in `out/<name>/words_cut.json`:
  - names, numbers, product names, split words, doubled or wrong small words;
  - look up a name you don't recognise instead of guessing.

  A recut rewrites this file, so redo the fixes after any recut.
- **Position**: one fixed line for the whole video. Use layout.json:
  - `"captions_y"`: the bottom edge in pixels. Usually just under the chin, not on the face, hands or a prop.
  - Check `--preview <seconds>` frames across the video, since people lean in and out.
  - `"captions_y_at": [{"from": 8.6, "until": 9.6, "y": 960}]` moves the chunks that start in a stretch (seconds after speed).
  - `"captions_x"` moves them sideways to clear something held up in frame.
- Captions start on the first word unless the user asks otherwise (`"captions_from"`).
- `"keep_together": ["phrase"]` keeps a key phrase on screen as one caption, even past the width rule.
- `python tools/set_chunks.py <name> chunks.txt` lays out chunks by hand. Delete `words_cut.prechunk.json` after any recut.
- `python tools/verify_final.py <name>` checks caption timing against the speech in the final. Every caption should be within 100 ms.

## Orientation

The goal is the real-life view. Front cameras often save a mirror image.

- **Check a frame of every video:**
  - any writing (a book, a shirt, a screen) must read correctly;
  - a face mark or a hair parting should sit where it does in real life.
- If writing reads backwards, set `"mirror": true` in layout.json.
- Rear-camera shots of a screen are already right. Leave them unflipped with `"mirror_except": [[start, end]]` (rough-cut seconds). Find the camera switches from big frame-to-frame changes in `rough.mp4`.
- If the user's phone always saves mirrored, set `"mirror_phone_footage": true` in style.json. It flips iPhone footage automatically.
- Ask when unsure.

## Speed

Ask the user's preferred pace.

- `--speed 1.2` is a common pace for talking-head videos. Pitch stays natural.
- A file the user already sped up stays at its speed: render it with `--speed 1`. A 36 fps export of a 30 fps edit is already 1.2x.
- `"speed_map": [[0, 1.0], ["first_cut", 1.2]]` in layout.json gives a cold open at 1x and the rest at 1.2x. Render without `--speed` when using it.

## Hook text (optional)

By default, don't burn in a title: most people add their on-screen text in the app.

- If the user wants one: `render --hook "text"` (`--hook-at top|bottom|above-photo`).
- Offer 3–5 options built from the user's own sentences, each with the line it came from. Each option should be:
  - specific (a name, number, place, time or object);
  - clear to a stranger in 2 seconds;
  - 10 words or fewer;
  - free of jargon.
- Never change the user's meaning, never write generic template hooks, never promise views.

## Images and layouts

`render --photo <file>` puts an image above the hook. `out/<name>/layout.json` can place several images and text blocks at exact spots:

```json
{"images": [{"image": "screenshot.jpg", "x": 30, "y": 180, "w": 600, "until": "first_cut"}],
 "texts":  [{"text": "…", "x": 30, "y": 330, "w": 600, "size": 46, "until": "first_cut"}],
 "captions_y": 1180}
```

Images come from `photos/`. 

## Audio

- Thumps from hands bumping the phone: `"lowcut": [{"from": 24.4, "until": 28.4, "hz": 150}]` in layout.json (rough-cut seconds) high-passes just those pieces. Say so when you use it.
- `python tools/pieces.py <name>` lists rumble and loudness per piece.
- For noise, hum, or lines much quieter or louder than the rest, flag the line and its time rather than spending long on a fix. Many people prefer to adjust audio in their own app.

## Delivering

- Run `python reel.py open <name>`, and give the path as `out/<name>/final.mp4`.
- Report:
  - the length before and after;
  - the script with removed words ~~struck through~~;
  - flags: anything you weren't sure of, quiet or loud lines with their times, spelling choices you made, the orientation decision, and spoken notes you followed.
- If the user asks for a change, change only that and re-render.
- One video at a time unless asked. If a command is interrupted, check the output files' timestamps before saying nothing changed.

## Settings (style.json)

- `small_captions`: `size`, `outline`, `shadow`, `lowercase`, `min_words`/`max_words`, `max_chars`, `four_word_chars`.
- `captions` (the larger caption style, used without `--small-captions`): `size`, `outline`, `margin_bottom`.
- `hook`: `font`, `size`, `outline`, `shadow`, `margin_top`, `margin_bottom`, `seconds` (0 = the whole video).
- `whisper_model`: `small.en` (default) or `medium.en` (more accurate, slower). `whisper_device`: `cpu` (default) or `cuda` on Windows with an NVIDIA GPU and CUDA installed.
- `mirror_phone_footage`: see Orientation.
- `tight`: thresholds for the tight cutter (dB above the room's noise floor).

## Troubleshooting

- **"No such filter: 'subtitles'"**: this ffmpeg has no libass. On Mac use `brew install ffmpeg-full`; on Windows, the Gyan build from winget.
- **"Could not load library cudnn…"** (Windows): set `"whisper_device": "cpu"`.
- **"unexpected keyword argument 'metadata_errors'"**: `python -m pip install av==15.1.0`.
- **Captions in the wrong font**: run `python reel.py selftest`. It checks that ffmpeg uses the fonts in `fonts/`.
- **Anything else**: run `python reel.py doctor`.

## This user's preferences

(Add the user's answers here as you learn them: speed, caption position, hook text, naming.)
