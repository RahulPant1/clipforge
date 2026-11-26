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
| **1 Source** | Paste a YouTube link or upload — or **Find videos** on a topic, or queue a **Batch** (several links, a playlist, a channel). Pick *Review each step* (default) or *Fully automatic*. If it fails: fix the cause, then **↻ Retry** |
| **2 Transcript** | Read/search the transcript (and, for screen / demo videos, what's on screen scene by scene), click timestamps to play, mark start/end to **add your own clip** |
| **3 Pick clips** | Gemma gives 2× the clips you asked for, each with the **clip judge's verdict** (ready / trim / needs context / reject) and a one-click fix. Watch each, **trim with ±1s/±0.2s**, fix title/hook/keywords, **approve** → *Create shorts & posts* |
| **4 Shorts** | Watch the vertical shorts. Per short: jump cuts, punch-in zoom, hook title, framing (or drag the crop by hand), caption look, emoji, **stat cards**, **replace voice**, logo/end card, sound fx and music, 4:5 / 1:1 feed versions, cover and quote cards → **re-render just that one** |
| **5 Posts** | Per-platform tabs. Edit copy inline (X threads post-by-post with 280 counters), 🎲 **New angle**, mark **Approved** |
| **6 Publish** | Checklist of every short × platform: file links, one copy button per upload-form field, paste the live link to mark **posted**. Download the full pack |
| **📚 Learn** | Learn jobs (or **Make study notes** on any job): summary, chapters, key concepts, timestamped notes, a quiz, hands-on exercises — click any time to play that moment |

## Features
- Paste a YouTube link **or** upload a video
- **Find videos:** search YouTube for a topic without an API key — long-form only, optional Creative Commons
  filter. Each result is scored 0–100 from its captions (how much of it is about your topic, with the best
  moments' timestamps); videos you already did are flagged. Optional **smart search**: Gemma adds 3 more angles
  to the query (one in Hinglish) and rates the top results from their captions
- **Batch:** paste several links, a playlist or a channel → one job per video, queued in order (private/live
  skipped). Tick results in Find videos to queue them too
- **Watched folder** (`WATCH_DIR`, off by default): drop a video in and it becomes a job once the file stops
  growing. Your files are only copied, never moved or deleted
- **History:** every video gets a readable folder (shorts, covers, captions, posts) plus a spreadsheet of all
  videos and shorts (`data/library/ClipForge history.xlsx`); the New job form warns when you've done a video before
- **Hinglish:** Hindi speech is written in Latin letters (`kismat`, `samajhna`) for captions, `.srt` and posts;
  switch back to Devanagari any time. Hindi and other Indic text is drawn with a font that has the letters
- Whisper transcription with word-level timing. Jobs reuse cached source videos and raw transcripts, and keep
  caption edits and Hinglish conversions in their own folders. The History sidebar shows reclaimable source
  cache space. With ClipForge stopped, you can remove `data/cache` to clear the shared cache.
- **Per-job Whisper:** in **Create → Make shorts from a video → Advanced settings**, choose **App default**, **Whisper Small**, or **Whisper Turbo** for source
  transcription. Turbo is useful for difficult Hindi or Hinglish speech. Model choices have separate transcript
  caches. ClipForge looks for an installed Turbo snapshot in `backend/data/models/whisper/large-v3-turbo`,
  and faster-whisper downloads the weights on first use if it needs them. For the CLI, use
  `--whisper-model large-v3-turbo` with a fresh output directory. Other Python projects can reuse these
  weights by passing the absolute directory path to `faster_whisper.WhisperModel(path)`. This model
  transcribes audio. Voice Library uses a separate model for speech generation.
- Finished shorts appear as they render. Posts and covers follow automatically.
- Gemma ranks moments by hook strength / self-containedness, writes title + hook + virality score
- Cuts snapped to sentence boundaries, overlaps removed, clips capped at 59 s (Content ID safe zone)
- **Topic (optional):** "salary negotiation" → Gemma only picks moments about it, rates each 1–10 for how
  on-topic it is (shown on the Pick cards) and drops the unrelated ones. Nothing found → retry with a
  broader topic; the download and transcript are reused
- **No-speech videos** (music, gameplay, sport, nature): when hardly anything is said, clips come from the
  loudest, busiest moments (loudness, motion, scene cuts), and Gemma *looks* at keyframes to score and title
  them; credits and title screens are marked down. Those shorts render without captions or jump cuts
- **🗣 Voice Library:** several voice profiles — yours, or anyone who agreed (a consent tick when you create one).
  Record or import takes, fix their words, build. Each build is a new **version** with its own test sample, so you
  can add recordings later and **compare** v1 against v2 on the same sentence. It's reference-based cloning, not
  training: the voice is averaged over your takes (ones that don't sound like the rest are left out) and the
  clearest take sets the pace and tone; the model itself never changes. Runs only when you ask (Qwen3-TTS, Apache
  2.0, in its own environment — `bash scripts/setup_tts.sh`). English and 9 other languages; no Hindi yet
- **🎙 Replace voice:** on a short, rewrite any sentence of its dialogue and have a Voice Library voice say it —
  each new sentence is placed where the original was said (sped up a little if needed, flagged "too long —
  shorten" if it can't fit), captions follow the new words, and you can regenerate a single line. The whole clip
  audio is replaced (add music under it if you like); lips won't match on a speaker's face. Marked 🤖 AI voice
- **🔁 Re-voice a recording:** under **Create → Re-voice a recording**, record or import your narration (audio or video, up to 3 min)
  and have it spoken again in any library voice — every sentence starts where you said it, so the timing and pauses
  stay (your intonation doesn't: the voice reads the words its own way). A video keeps its picture
- **Record video:** choose **Create → Make shorts from a video → Record video** to capture the screen or webcam with your microphone,
  preview it, and use it as a source. New jobs can record up to ten minutes. **Re-voice a recording** also accepts
  short screen and webcam recordings. Capture requires browser device permissions and localhost
  or HTTPS. Screen recording captures microphone audio.
- **Sentence pickup:** in **Create → Re-voice a recording**, choose a voice, edit the sentence, then
  select **Replace only this sentence**. The download uses the original recording and replaces only that
  sentence's audio window. To combine fixes, use **Select sentences for pickups** and export up to 20
  selected sentences together. Each export starts from the original. If a replacement needs too much
  speedup, shorten the wording and try again.
- **Script → Short:** choose **Create → Make a short from a script**, paste a script, choose a saved voice version,
  and arrange up to six MP4s from your local B-roll library. Footage plays in order for equal parts of the
  narration and loops when needed. The export is a captioned 720×1280 MP4 with a separate SRT, up to 60
  seconds of narration. **Preview narration** lets you listen before rendering and reuses the same audio
  and timing in the export. Under **Scene timing**, choose **Start scenes at script lines** to place each visual
  change at a specific line of the preview. With footage selected, use **Suggest B-roll plan** to review a
  Gemma-proposed sequence. Gemma describes only your shortlist and reuses descriptions of unchanged footage.
  The workspace groups these controls into **Script & voice**, **Footage**, and **Preview & export**.
  **Save draft** keeps your script, voice, footage order, scene timing, and any current preview in local
  ClipForge data.
- **Audio and export:** after approving clips, choose **Keep original audio** or **Use a saved voice**.
  With a saved voice, select the shorts and voice version, then prepare scripts
  from the transcript or request a narration rewrite, then review the wording and preview the first line over
  the picture. Queue narration and follow each short's progress in the workspace. ClipForge continues with the
  tab closed while its backend is running. Use **Retry failed narrations** for failed or interrupted shorts,
  then choose **Create shorts & posts** to export.
- **Narration script check:** likely mid-sentence openings and incomplete lines are flagged during review.
  Use **Suggest a fix** beside a flagged line to review a rewrite based on the surrounding transcript. Changes
  apply only when you accept them. A batch shares one voice-model load and one word-timing model load for
  the lines that need generation.
- **Narration duration** is available in batch narration and under **Shorts → Replace voice**.
  **Preserve video duration** fits the script with pitch-preserving tempo adjustment.
  **Preserve voice naturalness** keeps the speech at its generated speed and extends the picture as needed.
  **Smart duration fitting** trims gaps between lines, caps speedup at 15%, and allows up to four extra
  seconds. Optionally set a maximum export duration in seconds. Trim a source clip that already exceeds
  the limit. ClipForge shortens or omits the end card if needed to respect the limit.
  Review the estimated speech duration, tempo and video duration before generating.
  Estimates use the saved voice version's pace after three distinct completed lines, and use source timing
  until then. Open a short's script review in a batch to override its duration mode or Smart limit.
  ClipForge keeps each override when retrying failed narration.
  **Fit before queueing** shows every prepared short's estimate and flags likely overruns. Review and
  accept a suggested shorter line directly from the summary. Use **Review issues** to jump to overruns,
  flagged lines, stale takes, or unlabeled speakers in clips with multiple assignments.
  Choose **Suggest shorter wording** in batch narration to review a more concise script before generating speech.
  **Match source cues** and **Scene-aware pacing** remain available for cue-based timing.
- **Pronunciation and speaker voices:** save all-voice or voice-specific spoken spellings under **Voice Library →
  Pronunciation dictionary**, then test them under **Try it**. Caption spelling stays intact. In a short's
  script, label the speakers and assign a saved voice to each, with an optional per-line override.
  Use **Select lines to label** to apply one speaker label to several script rows at once.
  **Generate pace sample** under **Voice Library → Try it** measures three lines with the selected version
  and lets you listen to them before starting a narration batch.
- **Generated take checks:** after generation, ClipForge compares the intended spoken text with what Whisper
  heard. Flagged lines show likely number changes, omissions, or wording differences. Listen to confirm and
  optionally ask Gemma for a suggested fix. The check uses spoken aliases where configured.
- **Narration line review:** expand **Compare source and generated audio** beside a flagged or generated
  line to replay **A · Original** and **B · Generated take** from the same line start. The panel shows the
  assigned voice, raw take length, and fitted playback speed when available. Source excerpts are capped at 30 seconds.
  **Suggest speaker labels** proposes left/right labels from clear mouth movement in two-person shots.
  Select the suggestions you want to apply. Ambiguous and off-camera speech can be labeled manually.
- **Preview export:** choose a start and up to 10 seconds on the output timeline to review current captions,
  narration and audio mix at lower resolution. Find the preview next to ready narration or on the Shorts step.
- **Manual scene anchors:** under **Shorts → Replace voice → Scene-aware pacing**, pair a point in the source
  video with a point in the generated voice track. Manual anchors take priority over nearby automatic cuts.
- **Preview a narration line:** use **Preview this line in export** beside a generated line in an active dub
  to open the final mix and captions around that line.
- **Local B-roll:** in **Shorts**, add up to 12 local MP4 cutaways per short and set their output start and end
  times. Cutaways use separate ranges. The footage loops if needed, fills the picture, and leaves the short's
  captions and audio mix in place.
  To get suggestions, put descriptively named MP4s in `backend/data/broll` or set `BROLL_DIR` in `backend/.env`.
  **Find matches in local B-roll** compares file and folder names with the short's title, keywords, narration,
  and available scene descriptions. Review a match and choose **Use cutaway** to attach it.
- **Check before creating:** the Pick step opens with the judge's verdict on the whole job — how many of the shorts
  you asked for can be complete within 59 s, how long the incomplete ideas would need, and what to do (create fewer,
  allow longer clips, or skip a video that has no self-contained moment). It only recommends: **Approve only the
  complete ones** and **Apply all fixes** are buttons, and creating shorts from flagged clips asks you to confirm
- **Clip judge:** every candidate is checked like an editor would — does it start mid-thought or lean on something
  said before it, does it stop before its payoff, does the cut change what the speaker meant — and gets a verdict
  (ready / trim / needs context / reject) with the evidence and a one-click **Apply fix** that moves the edges to
  the right sentences. Rejected picks give their place to the next best candidate; in automatic mode the fixes
  are applied for you. **Check again** after you trim
- **📚 Learn from a video:** choose **Create → Learn from a video** (or **Make study notes** on any job) for a
  summary, chapters (ready to paste into a YouTube description), key concepts, timestamped notes, a quiz,
  hands-on exercises and what to learn next — every point jumps to where it's said; download it all as Markdown
- **Sees what's on screen:** for videos that are more than a talking head (screen recordings, slides, demos,
  b-roll), Gemma looks at one frame per scene and describes it, then picks clips from what is said *and* what is
  shown — a demo or a result on screen counts too. The Transcript step shows those scenes (with thumbnails) between
  the lines and searches them ("spreadsheet", "calendar"). Plain talking-head videos skip it automatically (no extra Gemma time)
- **Face tracking:** crop follows the speaker like a camera operator — steady when they're still,
  smooth pans when they move, instant snap on camera cuts; leaves room in the direction they look.
  Shots with no face (slides, screen, b-roll) are shown whole over a blurred copy instead of a blind centre crop
- **Adjust the crop by hand:** ✋ drag where the crop sits at any moment of the short — it holds until your next
  key, or glides there
- **Two-speaker layouts:** podcast-style shots (two people side by side) become two stacked close-ups with
  captions on the seam — or a single close-up that **cuts to whoever is talking** (mouth movement per face;
  short "mm-hm"s don't steal the shot)
- **Jump cuts:** pauses > 0.4 s and filler words ("um", "uh") removed automatically using word timestamps, with a 10 ms audio crossfade at every cut so there are no clicks; optional 5% punch-in zoom on every other segment so cuts feel intentional
- **Animated captions:** active word pops in yellow, Gemma-picked keywords and numbers in green
- **Caption looks:** Classic, Punch (Impact, gold/cyan), Clean (sentence case, the spoken word lights up),
  Boxed (text on a dark box) and Neon (glow) — per short, with bottom/middle placement; add your own in
  `backend/data/caption_presets.json`
- **Hook title card:** Gemma's hook shown big at the top for the first 3 s (editable in the Pick step)
- **Colour emoji:** an emoji above a caption line when a word has one (money 💰, gym 🏋️ — or type one into the
  transcript), and the hook's emoji above the title card (open-source Noto Color Emoji, downloaded once)
- **Stat cards:** when a striking number is said ("40% of startups fail", "₹2.5 crore"), it counts up on a card
  with a short label at that moment. Numbers are read from the transcript — digits, words ("fifty percent"),
  Hinglish ("das hazaar"), lakh/crore — never made up by the model; Gemma only picks which ones are worth a card
  and labels them with words from the same sentence. Suggestions appear on the Shorts card: **Show** the ones
  you want, edit the label or timing, or add your own
- **Brand kit** (🎨): your logo in a corner, an end card with a call to action after the last word, and your
  brand colour on covers, quote cards, carousels and (optionally) the active caption word
- **Sound design:** optional whoosh on jump cuts and pop on keywords, plus background music from your own folder
  (`data/sounds/music`) that ducks automatically whenever someone speaks
- **Feed versions:** 4:5 (Instagram feed, LinkedIn) and 1:1 (X) renders next to each 9:16 short — their own
  wider face-tracked crop, captions and logo re-placed, same cuts
- Vertical 1080×1920 render + thumbnails + `.srt` export for YouTube upload
- **Content Studio:** for every short, Gemma writes platform-native posts —
  YouTube (title, description, tags, pinned comment), Instagram (caption + **carousel images**),
  X (single post or thread, 280-char safe), LinkedIn (hook-driven post)
- **Variety engine:** 5–6 creative angles per platform (hot take, thread, POV, myth vs fact, framework, …)
  rotated so consecutive posts never feel templated; recent openers are banned; **🎲 New angle** button
- **Brand voice:** paste a few of your own best posts per platform (or pick from posts you edited or
  published) and Gemma writes in *your* voice — tone, casing, emoji and hashtag habits — while the facts still
  come only from the clip
- **Export pack:** one zip with shorts, captions, post copy, carousels and a `titles.csv`
- **Cut by text:** in the same word view, switch to ✂ Cut words and click words (⇧-click for a run) to cut them
  from the short — the pause around them goes too, captions and posts skip them, the preview jumps over them.
  Works with jump cuts off as well
- **Hook variants:** ✨ More hooks on a Pick card — Gemma writes 5 title + hook pairs in different styles
  (question, bold claim, curiosity gap, POV, mistake…) from what's actually said; click one to use it
- **Choose who to follow:** on a Shorts card, pick a face found in the clip — the crop tracks only that person
  (podcasts where Auto follows the wrong one)
- **Reel cover:** every short gets a 1080x1920 cover — the speaker cut out with a white outline on a blurred,
  brand-tinted copy of the frame (or the plain frame), with the cover text kept inside Instagram's grid crop.
  Change the text, style or frame; included in the export pack
- **Quote cards:** a line actually said in the clip, set big next to the speaker — 1:1 for X, 4:5 for LinkedIn
  and Instagram. Picked automatically (never written by the model), or type your own
- **Fix captions:** click any word Whisper misheard to correct it, or replace a word or phrase everywhere
  ("open ai" → "OpenAI"); captions, `.srt` and posts all use the fix. **Names & terms** on the New job form
  (remembered for all jobs) help Whisper spell your name, brands and jargon right from the start
- **Pick step timeline:** trim each clip on its audio waveform — drag the edges (they snap to sentence
  boundaries), drag the whole clip, click to seek; keyboard: `A` approve, `[`/`]` pick edge, `J`/`K` nudge
  (Shift = 1 s), `I`/`O` set start/end at the playhead, `Space` play
- Web UI with live progress (pushed over Server-Sent Events — no polling), preview, download — plus a CLI.
  Watch a job from a terminal: `curl -N localhost:8000/api/jobs/<id>/events`
- One-GPU-friendly: models load sequentially and are freed between stages
- **Retry failed jobs:** picks up where it stopped — a finished download or transcript is reused
- Safe while you edit: busy jobs can't be deleted, and edits made while Gemma writes a new angle are kept

## Requirements
- WSL2 Ubuntu (or Linux) with an NVIDIA GPU (CPU works, slowly)
- Miniconda, Node 18+ (via NVM), FFmpeg with libass — the distro package is fine. Tested with FFmpeg 4.4
  (Ubuntu 22.04), 8.0/8.0.1 (Ubuntu 26.04), 8.1 and 9.0
- [Ollama](https://ollama.com) with a Gemma model (`gemma3:4b` default). Thinking models like `gemma4` work too —
  set `CONTENT_THINK=false` for ~2.5× faster post writing
- Downloaded once on first use (no account needed): the YuNet face model, the speaker cut-out model for covers
  (~170 MB, runs on the CPU) and, if you turn on emoji, the Noto Color Emoji font (~11 MB)

## Quick start
```bash
git clone <your-repo> clipforge && cd clipforge
bash scripts/setup_wsl.sh      # ffmpeg, deno, conda env, pip/npm deps, pulls Gemma
bash scripts/dev.sh            # API :8000 + UI :5173
bash scripts/setup_tts.sh      # optional: 🗣 Your voice (own env, ~3 GB + ~4 GB model on first use)
```
Open http://localhost:5173 (works from the Windows browser too).

CLI only:
```bash
cd backend && conda activate clipforge
python -m app.cli "https://www.youtube.com/watch?v=VIDEO" --clips 5
python -m app.cli "https://www.youtube.com/watch?v=VIDEO" --clips 3 --topic "salary negotiation"
```

## Configuration
Copy `backend/.env.example` → `backend/.env`. Key options:

| Variable | Default | Notes |
|---|---|---|
| `GEMMA_MODEL` | `gemma3:4b` | Any Gemma tag in `ollama list` |
| `OLLAMA_URL` | `http://localhost:11434` | See below if Ollama runs on Windows |
| `CONTENT_THINK` | unset | `false` = posts skip reasoning on thinking models (gemma4, qwen3): ~2.5× faster. `true` fails on gemma3 |
| `SELECT_THINK` | unset | Same for clip picking — leave unset: without reasoning gemma4 picks clips that are too short |
| `NO_SPEECH_MODE` | `true` | Videos under 30 words/min get clips from sound + picture peaks instead of failing |
| `NO_SPEECH_CLIP_SECONDS` | `30` | Length of no-speech clips (edges snap to nearby scene cuts) |
| `NO_SPEECH_VISION` | `true` | Gemma scores/titles keyframes (needs a vision model: gemma3 4b+, gemma4); skipped automatically otherwise |
| `VISION_THINK` | `false` | Reasoning while scoring keyframes (off ≈ 5 s per candidate on gemma4) |
| `WHISPER_MODEL` | `small` | `medium` is better for Hindi/Hinglish |
| `WHISPER_DEVICE` | `cuda` | `cpu` + `WHISPER_COMPUTE_TYPE=int8` without GPU |
| `ENCODER` | `libx264` | `h264_nvenc` for fast GPU encoding |
| `MAX_CLIP_SECONDS` | `59` | Keep < 60 to avoid Content ID blocking |
| `JUMPCUT_ENABLED` | `true` | Remove silences + filler words |
| `MAX_PAUSE` | `0.4` | Pauses longer than this (s) are cut |
| `JUMPCUT_AUDIO` | `true` | Also find pauses in the audio (Whisper's word timings often run over them) |
| `SILENCE_DROP_DB` | `25` | A pause is this many dB under the speech level; lower it for noisy rooms |
| `JUMPCUT_CROSSFADE_MS` | `10` | Audio crossfade at each cut (avoids clicks); `0` = hard cuts |
| `JUMPCUT_ZOOM` | `false` | Punch in on every other jump-cut segment (per-short toggle in the UI) |
| `JUMPCUT_ZOOM_SCALE` | `1.05` | How far to punch in (1.05 = 5%) |
| `HOOK_OVERLAY` | `true` | Show the clip's hook as a title card at the top (per-short toggle in the UI) |
| `HOOK_SECONDS` | `3` | How long the hook title card stays on screen |
| `REFRAME_MODE` | `auto` | `auto` (two speakers → `TWO_SPEAKER_LAYOUT`, else track the face), `face`, `split` (stack two people), `speaker` (cut to whoever is talking) or `center` |
| `TWO_SPEAKER_LAYOUT` | `stack` | What `auto` does with two speakers: `stack` both, or `speaker` = follow the talker |
| `FACE_SMOOTHING` | `0.35` | Higher = faster camera pans |
| `FACE_DEADZONE` | `0.05` | Ignore small head moves (fraction of width) |
| `REFRAME_LEAD` | `0.15` | Lead room: shift the crop this share of its width toward where the speaker looks or moves (`0` = off) |
| `LEAD_HOLD` | `2.0` | Seconds a new look direction must last before the crop shifts (glances don't pan) |
| `CONTENT_PLATFORMS` | all four | JSON list, e.g. `["x","linkedin"]` |
| `CONTENT_LANGUAGE` | `English` | e.g. `Hinglish`, `Hindi` |
| `CONTENT_VOICE` | friendly creator | Describe your tone |
| `BRAND_HANDLE` | – | e.g. `@rahul.builds` (CTAs + carousel footer) |
| `CREDIT_SOURCE` | `false` | Add "Clip from …" line (needed for CC-BY videos) |
| `CAPTION_STYLE` | `karaoke` | `karaoke` (word-by-word highlight), `simple` (whole line) or `none` |
| `CAPTION_PRESET` | `classic` | Caption look: `classic` (uses the `CAPTION_*` colours/font), `punch`, `clean`, `boxed`, `neon` or one of yours |
| `CAPTION_POSITION` | `bottom` | `bottom` or `middle` (two-speaker stacks always use the seam) |
| `CAPTION_FONT` | `Arial` | Any installed font, e.g. `Montserrat ExtraBold`. On WSL, Windows fonts are picked up automatically (copied once into `data/fonts`) |
| `TTS_MODEL` | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | Voice cloning model (`…0.6B-Base` for less GPU memory) |
| `TTS_PYTHON` | – | Python of the TTS env (default `~/miniconda3/envs/clipforge-tts/bin/python`) |
| `JUDGE_ENABLED` | `true` | Check every candidate clip for missing setup/payoff, meaning, hook (~12 s per clip) |
| `SCENE_INDEX` | `auto` | Describe what's on screen per scene for clip selection: `auto` (skip talking heads), `on`, `off` |
| `REFRAME_NO_FACE` | `fit` | Shots without a face: `fit` (whole frame over a blurred copy) or `crop` (centre crop) |
| `HINGLISH_DEFAULT` | `true` | New jobs: Hindi speech written in Latin letters (Hinglish); per job on the New job form |
| `CAPTION_EMOJI` | `false` | Colour emoji above caption lines (per-short toggle in the UI) |
| `INSERTS_SUGGEST` | `true` | Gemma suggests stat cards for spoken numbers while writing posts (not shown until you pick **Show**) |
| `INSERTS_AUTO` | `false` | Show suggested stat cards without asking |
| `INSERTS_MAX` | `2` | Stat cards per short (at least 3 s apart) |
| `EXTRA_ASPECTS` | – | Feed versions for new shorts, e.g. `4:5,1:1` (per-short in the UI) |
| `SFX_DEFAULT` | `false` | Whoosh on cuts + pop on keywords for new shorts |
| `MUSIC_DEFAULT` | – | A file in `data/sounds/music` for new shorts; ducked under the voice |
| `COVER_STYLE` | `cutout` | Reel cover: `cutout` (speaker cut out) or `frame` |
| `QUOTE_CARDS` | `true` | Make 1:1 and 4:5 quote card images next to each short |
| `WATCH_DIR` | – | Folder to watch for new videos (off when unset); `WATCH_CLIPS`, `WATCH_MODE`, `WATCH_TOPIC` set the job |
| `LIBRARY_DIR` | `data/library` | Where the history folders and spreadsheet go |
| `YTDLP_COOKIES` | – | cookies.txt path if YouTube asks for bot check |

### Voice Library (voice cloning)
One-time: `bash scripts/setup_tts.sh` (its own conda env, ~10 GB with PyTorch; the 1.7B model, ~4 GB, downloads on
the first build). Then **🗣 Voice Library** in the sidebar → **+ New voice** (name, description, consent):
1. **Record** 6–10 takes of 5–15 s in different moods (1–2 minutes in all): quiet room, mic 15–20 cm away, your
   normal voice. Read the suggested lines or upload audio; long takes are split at sentence ends.
2. **Check** each take: fix words Whisper misheard (the voice uses them), untick noisy ones. Quality is scored
   from clarity, Whisper's confidence and length.
3. **Build a version** (~30-50 s once the model is there): the voice averaged over every take; takes that don't
   sound like the rest are left out; the best take sets the pace and tone. Each build is kept (v1, v2, …) with a
   test sample.
4. **Try it / compare**: type anything and hear it in any version; **Compare** plays the same line in two versions.
   Sounds off → add or replace takes and build again.

**Cloning vs fine-tuning:** a build makes a *reference voice prompt* (an averaged speaker embedding plus one
reference take) that is cached and reused — the Qwen3-TTS model is not trained or modified. Real fine-tuning of the
1.7B model needs ~14 GB of GPU memory, which an 8 GB card doesn't have.

It runs only when you ask (one process per build or line, then the GPU is free again) and takes ~4.5 GB of GPU
memory. Measured on an RTX 4060 laptop: ~2 s to make 1 s of speech, plus ~20 s to load the model each time.
Clone only your own voice, and mark realistic AI voice-overs as altered/synthetic when you upload to YouTube.

### Optional source ambience

Install [Demucs](https://github.com/facebookresearch/demucs#installation) in a separate Python environment.
Set `DEMUCS_EXECUTABLE` in `backend/.env` to the full path to its `demucs` command if it is outside the backend's
`PATH`, then restart the backend. Enable **Keep source ambience** in the narration panel or the short's voice
settings. The first preview or export may download model weights. Separation runs locally on CPU and caches the
result for that clip range. Adjust **Ambience level** to control the mix and listen for residual dialogue.

### Ollama on Windows, app in WSL
Either enable mirrored networking (`%UserProfile%\.wslconfig` → `[wsl2]` `networkingMode=mirrored`, then `wsl --shutdown`),
or run Ollama with `OLLAMA_HOST=0.0.0.0` and set `OLLAMA_URL=http://<windows-ip>:11434`.

## Troubleshooting
- **YouTube download recovery** → open the failed job’s **Source** step. **Use a local file** resumes the same
  job with its settings. For downloader or format errors, **Update downloader & retry** stages an official
  yt-dlp release, tests it against the URL, and activates it after verification. Use **Restore previous downloader**
  from the recovery panel to roll back. A YouTube rate limit puts uncached remote downloads on a ten-minute cooldown.
- **"n challenge solving failed" / missing formats** → update the downloader in the ClipForge environment with
  `python -m pip install --upgrade "yt-dlp[default]>=2026.11.24.232653.dev0"` and check `deno --version`.
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
- **No-speech clips all get "Highlight at m:ss" titles** → the model can't see images: use a vision model
  (`gemma4`, `gemma3:4b` or bigger) — the clips themselves still come from the loudness/motion peaks.
- **Shorts show "✂ no pauses"** → the speaker never paused longer than `MAX_PAUSE` (0.4 s + padding). In a noisy
  room, lower `SILENCE_DROP_DB` (e.g. 18) so quieter gaps count as pauses.
- **CUDA out of memory** → `WHISPER_MODEL=small`, smaller Gemma, close other GPU apps.
- **🗣 Your voice says "Voice engine not ready" / the TTS worker exits with 139 (segfault)** → the TTS env got a
  PyTorch build for CUDA 13.x, which crashes on CUDA start with some WSL drivers. Run `bash scripts/setup_tts.sh`
  again: it pins PyTorch 2.9 for CUDA 12.8. "GPU is busy" means a job is running — try again when it's done.
- **Your voice sounds off in Hinglish** → Qwen3-TTS has no Hindi; its 10 languages are English, Chinese, Japanese,
  Korean, German, French, Russian, Portuguese, Spanish and Italian.

## Development
```bash
cd backend && python -m pytest -q && ruff check app tests tts
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
python -m app.eval <job_id> --topic "salary negotiation"   # default: the job's topic; --topic "" for none
python -m app.eval <job_id> --scenes off --scenes on --repeat 3  # with/without what's on screen (needs scenes.json)
```
The clip judge has its own: `python -m app.judge_eval <job> [<job> ...]` judges each clip as picked plus damaged
copies (opening sentences dropped, ending cut early) and reports how often the damage is caught and how close the
suggested fix lands to the real boundary.

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
