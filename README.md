# ✂ ClipForge — Local AI Shorts Generator

[![CI](https://github.com/RahulPant1/clipforge/actions/workflows/ci.yml/badge.svg)](https://github.com/RahulPant1/clipforge/actions/workflows/ci.yml)

Turn long videos into captioned 9:16 YouTube Shorts — **fully offline** on a gaming laptop.
No API keys, no per-minute fees, your videos never leave your machine.

```
YouTube URL / file
      │
      ▼
 yt-dlp ──► faster-whisper (GPU) ──► Gemma via Ollama ──► FFmpeg
 download    transcript + word        picks best 20–59s     9:16 crop +
             timestamps               moments (JSON schema)  bold captions
```

## How you work with it (6 steps)
| Step | What you do |
|---|---|
| **1 Source** | Paste a YouTube link or upload. Pick *Review each step* (default) or *Fully automatic*. If it fails: fix the cause, then **↻ Retry** |
| **2 Transcript** | Read/search the transcript, click timestamps to play, mark start/end to **add your own clip** |
| **3 Pick clips** | Gemma gives 2× the clips you asked for. Watch each, **trim with ±1s/±0.2s**, fix title/hook/keywords, **approve** → *Create shorts & posts* |
| **4 Shorts** | Watch the vertical shorts. Toggle jump cuts / punch-in zoom / hook title / framing (auto, face, 2 speakers, centre) / caption style per short → **re-render just that one** |
| **5 Posts** | Per-platform tabs. Edit copy inline (X threads post-by-post with 280 counters), 🎲 **New angle**, mark **Approved** |
| **6 Publish** | Checklist of every short × platform: copy, post, click to mark **posted**. Download the full pack |

## Features
- Paste a YouTube link **or** upload a video
- Whisper transcription with word-level timing, cached per job
- Gemma ranks moments by hook strength / self-containedness, writes title + hook + virality score
- Cuts snapped to sentence boundaries, overlaps removed, clips capped at 59 s (Content ID safe zone)
- **Face tracking:** crop follows the speaker like a camera operator — steady when they're still,
  smooth pans when they move, instant snap on camera cuts; centre crop fallback
- **Two-speaker layout:** podcast-style shots (two people side by side for most of the clip) become two
  stacked close-ups, captions on the seam
- **Jump cuts:** pauses > 0.4 s and filler words ("um", "uh") removed automatically using word timestamps, with a 10 ms audio crossfade at every cut so there are no clicks; optional 5% punch-in zoom on every other segment so cuts feel intentional
- **Animated captions:** active word pops in yellow, Gemma-picked keywords and numbers in green
- **Hook title card:** Gemma's hook shown big at the top for the first 3 s (editable in the Pick step)
- Vertical 1080×1920 render + thumbnails + `.srt` export for YouTube upload
- **Content Studio:** for every short, Gemma writes platform-native posts —
  YouTube (title, description, tags, pinned comment), Instagram (caption + **carousel images**),
  X (single post or thread, 280-char safe), LinkedIn (hook-driven post)
- **Variety engine:** 5–6 creative angles per platform (hot take, thread, POV, myth vs fact, framework, …)
  rotated so consecutive posts never feel templated; recent openers are banned; **🎲 New angle** button
- **Export pack:** one zip with shorts, captions, post copy, carousels and a `titles.csv`
- Web UI with live progress, preview, download — plus a CLI
- One-GPU-friendly: models load sequentially and are freed between stages
- **Retry failed jobs:** picks up where it stopped — a finished download or transcript is reused
- Safe while you edit: busy jobs can't be deleted, and edits made while Gemma writes a new angle are kept

## Requirements
- WSL2 Ubuntu (or Linux) with an NVIDIA GPU (CPU works, slowly)
- Miniconda, Node 18+ (via NVM), FFmpeg with libass — the distro package is fine. Tested with FFmpeg 4.4
  (Ubuntu 22.04), 8.0/8.0.1 (Ubuntu 26.04), 8.1 and 9.0
- [Ollama](https://ollama.com) with a Gemma model (`gemma3:4b` default). Thinking models like `gemma4` work too —
  set `CONTENT_THINK=false` for ~2.5× faster post writing

## Quick start
```bash
git clone <your-repo> clipforge && cd clipforge
bash scripts/setup_wsl.sh      # ffmpeg, deno, conda env, pip/npm deps, pulls Gemma
bash scripts/dev.sh            # API :8000 + UI :5173
```
Open http://localhost:5173 (works from the Windows browser too).

CLI only:
```bash
cd backend && conda activate clipforge
python -m app.cli "https://www.youtube.com/watch?v=VIDEO" --clips 5
```

## Configuration
Copy `backend/.env.example` → `backend/.env`. Key options:

| Variable | Default | Notes |
|---|---|---|
| `GEMMA_MODEL` | `gemma3:4b` | Any Gemma tag in `ollama list` |
| `OLLAMA_URL` | `http://localhost:11434` | See below if Ollama runs on Windows |
| `CONTENT_THINK` | unset | `false` = posts skip reasoning on thinking models (gemma4, qwen3): ~2.5× faster. `true` fails on gemma3 |
| `SELECT_THINK` | unset | Same for clip picking — leave unset: without reasoning gemma4 picks clips that are too short |
| `WHISPER_MODEL` | `small` | `medium` is better for Hindi/Hinglish |
| `WHISPER_DEVICE` | `cuda` | `cpu` + `WHISPER_COMPUTE_TYPE=int8` without GPU |
| `ENCODER` | `libx264` | `h264_nvenc` for fast GPU encoding |
| `MAX_CLIP_SECONDS` | `59` | Keep < 60 to avoid Content ID blocking |
| `JUMPCUT_ENABLED` | `true` | Remove silences + filler words |
| `MAX_PAUSE` | `0.4` | Pauses longer than this (s) are cut |
| `JUMPCUT_CROSSFADE_MS` | `10` | Audio crossfade at each cut (avoids clicks); `0` = hard cuts |
| `JUMPCUT_ZOOM` | `false` | Punch in on every other jump-cut segment (per-short toggle in the UI) |
| `JUMPCUT_ZOOM_SCALE` | `1.05` | How far to punch in (1.05 = 5%) |
| `HOOK_OVERLAY` | `true` | Show the clip's hook as a title card at the top (per-short toggle in the UI) |
| `HOOK_SECONDS` | `3` | How long the hook title card stays on screen |
| `REFRAME_MODE` | `auto` | `auto` (stack two speakers, else track the face), `face`, `split` (stack whenever two people appear) or `center` |
| `FACE_SMOOTHING` | `0.35` | Higher = faster camera pans |
| `FACE_DEADZONE` | `0.05` | Ignore small head moves (fraction of width) |
| `CONTENT_PLATFORMS` | all four | JSON list, e.g. `["x","linkedin"]` |
| `CONTENT_LANGUAGE` | `English` | e.g. `Hinglish`, `Hindi` |
| `CONTENT_VOICE` | friendly creator | Describe your tone |
| `BRAND_HANDLE` | – | e.g. `@rahul.builds` (CTAs + carousel footer) |
| `CREDIT_SOURCE` | `false` | Add "Clip from …" line (needed for CC-BY videos) |
| `CAPTION_STYLE` | `karaoke` | `karaoke` (animated) or `simple` |
| `CAPTION_FONT` | `Arial` | Any installed font, e.g. `Montserrat ExtraBold`. On WSL, Windows fonts are picked up automatically (copied once into `data/fonts`) |
| `YTDLP_COOKIES` | – | cookies.txt path if YouTube asks for bot check |

### Ollama on Windows, app in WSL
Either enable mirrored networking (`%UserProfile%\.wslconfig` → `[wsl2]` `networkingMode=mirrored`, then `wsl --shutdown`),
or run Ollama with `OLLAMA_HOST=0.0.0.0` and set `OLLAMA_URL=http://<windows-ip>:11434`.

## Troubleshooting
- **"n challenge solving failed" / missing formats** → `pip install -U "yt-dlp[default]"` (brings `yt-dlp-ejs`) and make sure `deno --version` works.
- **"Sign in to confirm you're not a bot"** → `pip install -U "yt-dlp[default]"`, then export browser cookies to `cookies.txt` and set `YTDLP_COOKIES`.
- **`libcudnn` / `libcublas` not found** → reinstall the wheels (`pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`),
  start via `scripts/dev.sh`, or use `WHISPER_DEVICE=cpu`.
- **Transcribing fails with `401 … Repository Not Found` for `Systran/faster-whisper-*`** → your saved Hugging Face
  token is expired: `hf auth logout` (or log in again). Only happens when a Whisper model is downloaded for the first time.
- **Gemma returns no clips** → try a bigger model (`gemma3:12b`), or tune the prompt in `backend/app/pipeline/highlights.py`.
  With a thinking model, keep `SELECT_THINK` unset — without reasoning it picks clips that are too short.
- **"Gemma ran out of context"** → raise `OLLAMA_NUM_CTX` (needs more VRAM) or lower `WINDOW_SECONDS`.
- **Log says "caption font … is not installed"** → install the font, drop its `.ttf` into `backend/data/fonts/`,
  or set `CAPTION_FONT` to one you have.
- **CUDA out of memory** → `WHISPER_MODEL=small`, smaller Gemma, close other GPU apps.

## Development
```bash
cd backend && python -m pytest -q && ruff check app tests
cd frontend && npm run build
```
VS Code: open the folder via **Remote - WSL**; launch configs for API and CLI debugging are included (GPU Whisper
works there too — the app loads cuBLAS from the pip wheel itself).
See `CLAUDE.md` for architecture, conventions and the roadmap.

### Tuning clip selection (prompt eval)
Every job saves `candidates.json`: each pick Gemma made and what happened to it (kept, too short, invalid,
lost to an overlapping better pick), plus per-chunk timings. To compare models, prompts or thinking on the
**same transcript**:
```bash
cd backend
python -m app.eval --print-prompt > my_prompt.txt          # edit it; keep {n} {min_s} {max_s} {transcript}
python -m app.eval <job_id> --prompt default --prompt my_prompt.txt
python -m app.eval <job_id> --model gemma4:e4b-it-q4_K_M --model qwen3.5:4b --think on --think off --repeat 2
```
Every combination runs and `data/evals/<timestamp>/report.md` gets a table: time, usable picks, too-short /
invalid counts, median length, average score, how many of **your approved clips** each variant found, and
agreement with the first (baseline) variant — then each variant's clips. Your picks came from the original
run's candidates, so they favour that setup; use `--repeat` to see run-to-run noise. Needs Ollama; don't run
it while a job is selecting clips.

### Continuous integration
Every push and pull request runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on GitHub's Ubuntu runner:
`ruff` + `pytest` for the backend and `npm run build` for the frontend. It needs no GPU or Ollama — the LLM is
faked in tests — but it does install FFmpeg, fontconfig and DejaVu fonts, so the tests that render real video
(vertical crop, captions, jump cuts, A/V sync) actually run instead of skipping. Skipped tests are listed in the log.

### FFmpeg compatibility note
FFmpeg 8.0 and 8.0.1 have a bug where the `aselect` audio filter does nothing (fixed upstream after 8.0.1).
ClipForge therefore cuts audio with `atrim` + `concat` instead, with every cut snapped to the video frame grid,
so jump cuts stay in sync on every FFmpeg version. If you change the jump-cut filters, don't reintroduce
`aselect` — CI would catch it on its FFmpeg 8.0.1.

## License
[Apache License 2.0](LICENSE) for ClipForge's own code (copyright notice in [NOTICE](NOTICE)). Models and tools it runs keep their own terms — notably Gemma
(Gemma Terms of Use), Whisper/faster-whisper (MIT), yt-dlp (Unlicense) and FFmpeg (LGPL/GPL, used as a separate program).

## Responsible use
Only process videos you own or have rights to (e.g. your own channel, client content, Creative Commons).
Downloading from YouTube may be restricted by its Terms of Service.
