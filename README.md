# Tyler Choice's Claude Video Editor

Turns a raw talking-head video into a finished 9:16 video for TikTok, Reels or Shorts:

- every pause, breath and filler is cut;
- small captions are timed to each word;
- the result is rendered at 1080x1920.

You run it by talking to Claude Code. Drop a video in, say "edit myvideo.mov", answer a question or two, and get `final.mp4`. Codex works too: it reads `AGENTS.md`, which has the same instructions.

- **Systems:** macOS (Apple Silicon or Intel) and Windows 10/11 (64-bit, Intel or AMD). Windows on ARM isn't supported.
- **Language:** English speech.
- **Privacy:** everything runs on your own computer. Your videos are never uploaded anywhere.

## What you need

- Claude Code: the Code tab in the Claude desktop app, or `claude` in a terminal.
- About 2 GB of free disk space for the tools and speech model, plus room for your videos.
- An internet connection for the first setup.

## Setup (once, 10–20 minutes, mostly waiting)

1. Download this folder: the green **Code** button → **Download ZIP**, then unzip it somewhere easy, like Documents. Or run `git clone https://github.com/tylerchoice/claude-video-editor`.
2. Open Claude Code **in that folder**.
3. Say: **"Set up this folder."**

Claude follows the setup steps in `CLAUDE.md`:
- It installs what's missing (Python 3.12 and ffmpeg) and asks before installing system software.
- It downloads the speech model.
- It runs a self-test that edits a short spoken test clip end to end.

### Setting it up by hand

macOS (with [Homebrew](https://brew.sh)):

```bash
brew install python@3.12 ffmpeg-full
cd path/to/claude-video-editor
$(brew --prefix)/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python reel.py doctor --get-model
.venv/bin/python reel.py selftest
```

Windows (PowerShell):

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Gyan.FFmpeg
# open a new PowerShell window so the new programs are found, then:
cd path\to\claude-video-editor
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe reel.py doctor --get-model
.venv\Scripts\python.exe reel.py selftest
```

`doctor` checks every piece and says how to fix anything missing. `selftest` should end with "Self-test passed".

## Editing a video

1. Put your raw video in `raw/`. It can be .mov, .mp4 or .m4v, at any length.
2. Tell Claude: **"edit myvideo.mov"**.
3. The first time, Claude asks how you like things (speed, where captions sit, whether you want title text) and remembers your answers in `CLAUDE.md`.
4. Your video opens when it's done. Everything for it is in `out/myvideo/`:
   - `final.mp4`: the finished video.
   - `cover.jpg`: a still from the start.
   - `rough.mp4`: the cut before captions.

Want a change? Just say it:
- "keep the first sentence"
- "captions a bit higher"
- "1.2x speed"
- "cut the line about the weather"

**Scripted videos:** if you filmed from a script with several takes of each line, say so. Claude keeps the whole script and uses the cleanest take of each line.

## Changing the look

`style.json` holds the defaults: caption font, size, outline, words per caption, and so on. Ask Claude to change them, or edit the file. Settings for a single video go in `out/<name>/layout.json`, for example:
- caption height;
- images on screen;
- per-section speed.

## What's in the folder

| | |
|---|---|
| `reel.py` | the editor: transcribe, cut, render, plus doctor, selftest and open |
| `CLAUDE.md`, `AGENTS.md` | the editing instructions Claude (or Codex) follows |
| `style.json` | the default look |
| `tools/` | checks Claude runs on each cut (sync, clipped words, caption timing, audio levels) |
| `fonts/` | TikTok Sans, the caption font (open license, see `fonts/OFL.txt`) |
| `raw/` | your raw videos |
| `out/` | one folder per video (created as you go) |
| `photos/` | images for on-screen layouts (yours or ones you have rights to) |

Setup also creates these:
- `.venv/`: Python and its packages;
- `models/`: the speech model;
- `cache/`: audio prepared for analysis. You can delete it anytime.

## Troubleshooting

Run `doctor` first: it names the problem and the fix.
- macOS: `.venv/bin/python reel.py doctor`
- Windows: `.venv\Scripts\python.exe reel.py doctor`

- **Captions missing or "No such filter: 'subtitles'"**: your ffmpeg can't draw captions. On a Mac, install `ffmpeg-full`. On Windows, install the Gyan build (`winget install -e --id Gyan.FFmpeg`).
- **ffmpeg not found**: open a new terminal, or restart Claude Code, after installing it. You can also set the `REEL_FFMPEG` environment variable to the folder that has ffmpeg in it.
- **Windows: "Could not load library cudnn…"**: keep `"whisper_device": "cpu"` in `style.json`. That's the default.
- **Transcription is slow**: it runs on the CPU. On an older computer the first pass can take about as long as the video itself. `small.en` is the balance of speed and accuracy. `medium.en` is more accurate but slower.

## Credits and licenses

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT) with the Whisper `small.en` model by OpenAI (MIT), converted by SYSTRAN.
- [TikTok Sans](https://github.com/tiktok/TikTokSans) font © 2024 TikTok Inc., SIL Open Font License 1.1 (`fonts/OFL.txt`).
- [FFmpeg](https://ffmpeg.org) is installed separately during setup and isn't included in this folder.
- Claude is a trademark of Anthropic. This project isn't made or endorsed by Anthropic.
