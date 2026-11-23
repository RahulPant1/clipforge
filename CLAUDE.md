# CLAUDE.md — ClipForge

Context for Claude Code. Read this before making changes.

## What this is
Local AI "long video → YouTube Shorts" generator. Everything runs on one laptop
(HP Omen, RTX GPU, 16 GB RAM, WSL2 Ubuntu). No cloud APIs.

Pipeline: **yt-dlp** (download) → **faster-whisper** (GPU transcript, word timestamps)
→ **Gemma via Ollama** (picks best moments, JSON-schema output) → **Gemma** again (Content Studio:
platform-native posts for YouTube / Instagram / X / LinkedIn) → **FFmpeg** (jump cuts, face-tracked 9:16, captions).

## Layout
```
backend/                FastAPI + pipeline (Python 3.11, conda env "clipforge")
  app/config.py         All settings (pydantic-settings, overridable via backend/.env)
  app/models.py         Pydantic models: Segment, Word, Clip, Job, JobStatus, Stage
  app/jobs.py           File-backed JobStore (data/jobs/<id>/job.json, update_clip for atomic edits;
                        every save/delete bumps `version` → changes(since, timeout) feeds SSE)
                        + single Worker thread with task kinds: run | produce | render
  app/main.py           API routes under /api; create_app(pipeline=...) for tests
  app/events.py         SSE: job_events() → snapshot, then job/deleted events (coalesced, MIN_GAP), : ping
                        (GET /api/events = all jobs, /api/jobs/{id}/events = one). App.jsx: EventSource;
                        polls only while the stream is down
  app/library.py        history outside the job folders (LIBRARY_DIR, default data/library): per video a readable
                        "<date> <channel> - <title> [<id>]" folder (shorts/covers/srt/posts as hard links, info.md) +
                        "ClipForge history.xlsx" (Videos + Shorts sheets) from history.json. Debounced thread fed by
                        JobStore.listeners (Library.touch); busy jobs wait; deleted jobs stay (marked); old jobs get
                        full metadata once via download.fetch_meta (no download). `python -m app.library` rebuilds
                        Library.lookup(url) / GET /api/library/lookup: earlier jobs for the same video (video_key = YouTube
                        id, any link form) → "worked on this before" notice on the New job form
  app/watch.py          WATCH_DIR (off by default): Watcher scans every WATCH_INTERVAL s; a video whose size+mtime held
                        still over two scans is copied into a new job (WATCH_CLIPS/MODE/TOPIC); data/watched.json
                        remembers what was taken (a changed file is taken again). Never moves/deletes your files
  app/cli.py            `python -m app.cli <url|file> --clips N`
  app/eval.py           `python -m app.eval <job> --model A --model B --prompt f.txt --think on/off` → data/evals/
                        (compares selection variants on one transcript; summarize() is pure + tested)
  app/pipeline/
    discover.py         New job → Find videos: search() = yt-dlp search (no API key, ~2 s), long-form only
                        (min/max minutes, no live), cc=True → YouTube's Creative Commons filter (sp=EgIwAQ==).
                        score(id, topic) = video details + own-language captions as json3 (no download, ~3 s; UI runs 4
                        at once): topic hits (word stems; Hindi captions via to_latin + consonant skeleton()) → 0-100
                        (density/peak minute/coverage), top moments with timestamps, licence, language. Results flag
                        videos already done (library.lookup). Tests fake `_extract`/`_fetch`: never hit YouTube
                        Optional smart search (Gemma, UI checkbox): POST /discover/expand → expand_queries() (topic +
                        3 angles, one Hinglish) → search_many() round-robin merge (found_by); POST /discover/judge →
                        judge() rates the top 6 by keyword score 1-10 from ~1 min of captions per moment (best moment,
                        why). Both take GPU_LOCK like hooks (409 while a job runs → UI falls back to plain search)
    batch.py            New job → Batch: expand(text) = links → videos (playlists/channels listed flat via
                        discover._extract, channel → /videos; private/live skipped; same video once; MAX_VIDEOS=50).
                        POST /batch/expand (+ done count) → tick → POST /jobs/batch = one job each, queued in order.
                        Find videos: tick "batch" on results → "Queue N as a batch" hands them to this tab
    download.py         yt-dlp, cookies support, progress hook; meta.json = video_meta() (channel, upload date,
                        views, tags, …; meta_version 2); transcribe writes transcript_info.json (spoken language)
    transcribe.py       faster-whisper, caches transcript.json, frees VRAM after; preloads cuBLAS from the pip wheel
    transcript_edit.py  caption fixes: set_word(seg, i), replace_all(find, replace) (phrases merge into one word,
                        each occurrence keeps its own trailing punctuation), rebuild seg.text; transcript.json atomic
    hinglish.py         Devanagari → Hinglish (Latin) per word, timings kept: rule-based map + schwa deletion
                        (jab, kismat, samajhna), long vowels doubled only in one-syllable words (baat, aap). Job.hinglish
                        (NewJob checkbox, HINGLISH_DEFAULT) → prepare(romanize=True) right after transcribe, so selection,
                        captions, .srt and posts all read Latin. Original kept in transcript.native.json;
                        POST /jobs/{id}/script {hinglish} switches either way (marks shorts stale)
    vocabulary.py       names/terms (data/vocabulary.json, all jobs) → faster-whisper `hotwords` for new transcripts
    llm.py              OllamaClient (chat_json with schema, unload, health) + LLM Protocol
    highlights.py       windows → prompt → snap → fit_duration → dedupe  (prompt + keywords live here)
                        snap/fit_duration prefer sentence ends from word punctuation (sentence_bounds, ±SNAP_REACH);
                        Whisper segment edges are only the fallback — they end mid-sentence on real footage
                        optional job.topic → FOCUS block + TOPIC_SCHEMA (relevance 1-10): < MIN_RELEVANCE dropped
                        (off_topic), rest ranked by score + relevance (`rank()`, also used by nospeech + Pick sort)
    nospeech.py         < 30 wpm over the whole video (words_per_minute(segs, duration)): analyse() (one ffmpeg pass of 64x36 grey frames → motion + scene cuts, mono PCM →
                        loudness; cached signals.json) → excitement() → peak_windows() (payoff 65% in, snap to
                        cuts) → keyframes → vision Gemma (score/title/hook/description, mostly_text → capped)
    scenes.py           scene index for SPEECH videos (prepare, after transcribe, before selection; same Gemma load):
                        frame_signals cuts + face_share (16 frames) → SCENE_INDEX auto skips talking heads
                        (< SCENE_SKIP_CUTS_PER_MIN and ≥ SCENE_SKIP_FACE_SHARE; calibrated on 6 real jobs: talking heads
                        0.2-1.15 cuts/min with faces in 14-16/16, the screen tutorial 11.7/min with 6/16) → spans() (merge
                        < SCENE_MIN, split > SCENE_SPAN, ≤ SCENE_MAX) → one keyframe each → vision Gemma {description,
                        kind} (< 1 s/scene warm on gemma4; GIVE_UP after 4 straight failures) → scenes.json (cached).
                        highlights.build_prompt(scenes=) puts "  ↳ on screen (kind): …" under the transcript line being
                        spoken (not talking_head) + SCENE_NOTE. NO [start-end] on scene lines: with them Gemma used scene
                        edges as clip edges (7/18 picks too short). windows() counts their chars; a tail window shorter
                        than TAIL_MIN × max clip is merged into / balanced with the one before (a 12 s tail gave only
                        5-11 s picks). Eval on the screen tutorial (3 runs each): too-short 3/27 vs 2/24, clips cover
                        2.4 vs 2.0 on-screen scenes — a small lean to visual moments, not a proven win yet.
                        Trace: candidates.json scene_index + scenes. Never fails prepare.
                        GET /jobs/{id}/scenes → Transcript step rows (search covers them). eval: --scenes on/off
    jumpcut.py          silence/filler removal: plan_cuts() → keep intervals + words remapped to output time;
                        `removed=` (clip.removed, struck words) is cut too, even with jump cuts off
    captions.py         karaoke ASS (per-word colours from a preset, pop-in) or one plain line per chunk (simple)
                        + SRT export (still written for YouTube upload; no longer used for burn-in)
    presets.py          CaptionPreset looks: font fallback list (first one fonts.font_available() finds),
                        colours, outline/box, case, words/line; placement(position); data/caption_presets.json
    fonts.py            makes CAPTION_FONT real for libass: system font, else copied once from Windows fonts
                        into data/fonts (passed as fontsdir=); warns instead of silently substituting
    reframe.py          face tracking: sample faces (YuNet, Haar fallback) → smooth (deadzone, snap on cuts)
                        + lead room (head turn from landmarks / movement) → per-frame crop x → ffmpeg sendcmd
                        file driving `crop@track`.
                        two_speakers() → split_panels(): static 9:8 crop per speaker, split+vstack ("split" mode)
                        or speaker_activity() (aligned mouth-minus-eye thumbnail change) → active_speaker_path()
                        (offline turns, cut at turn start) → same sendcmd crop ("speaker" mode)
                        no_face_spans(): shots (split at scene cuts) with faces in < FACE_MIN_COVERAGE of samples are shown
                        whole over a blurred copy (fit_graph, overlaid on the crop branch with enable=between(t,…));
                        no face anywhere → mode "fit". REFRAME_NO_FACE=crop restores centre crop. Covers do the same.
                        options.follow (x of a face from people()) → pick_face tracks only that person (FOLLOW_RADIUS,
                        holds when they're out of frame) in "face" mode, whatever the framing says except "center"
                        reframe="manual" + options.crop_keys (CropKey: SOURCE t, centre x, pan) → manual_path() (a key
                        holds until the next = cut; pan=True glides from the previous) → same sendcmd crop, no face
                        detection. No key inside the clip → auto. GET /clips/{i}/crop: sizes, crop width per format,
                        tracked_path() (last render's .crop.cmd) → CropEditor.jsx (modal; cropAt/seedKeys/upsertKey
                        in util.js mirror manual_path)
    content.py          Content Studio: ANGLES (creative styles per platform), RULES, SCHEMAS, PROMPT,
                        plain() strips markdown/LaTeX Gemma writes (**bold**, $\rightarrow$) from every field,
                        assemblers enforcing platform limits, generate_post/generate_all, posts.md writer
    brand.py            Brand kit (data/brand_kit.json + data/brand/logo.png, 🎨 screen): render asks watermark() (logo
                        top-left/right at TOP_Y=120, shown from the end of the hook card to the last word),
                        end_card_seconds() (≤ end_seconds, never past max_clip_seconds; < 1 s → none) → freeze_tail()
                        (tpad clone + blur/darken, before captions) + end_card_png() overlay + apad; overlay_graph()
                        switches render to -filter_complex (-map [v] -map 0:a?). accent()/handle()/pop_colour() feed covers, quote
                        cards, carousels, post CTAs (fallback CAROUSEL_ACCENT/BRAND_HANDLE); accent_captions → preset
                        active colour. `rev` bumps on burned-in changes; clip.brand_rev → "re-render" hint (never stale).
                        No kit file = off: shorts render exactly as before. Per short: options.watermark / end_card
    sfx.py              Sound design: events(plan, keywords) = whoosh on cuts (removed gap ≥ WHOOSH_MIN_GAP, ≥ 2.5 s
                        apart) + pop on keyword words (≥ 2 s apart, ≤ 6), all OUTPUT time → write_track() pre-mixes
                        them into short_N.sfx.wav (one extra input). Music = data/sounds/music/<file> (-stream_loop,
                        trimmed/faded, loudnorm to MUSIC_LUFS -30, sidechaincompress keyed by the voice ≈ 7-10 dB duck). audio_graph()
                        moves audio into -filter_complex → [aout]; every branch aformat'ed to 48 kHz stereo fltp
                        (sidechaincompress refuses mixed formats). Starter whoosh/pop.wav made by numpy; drop your own
                        (same names, any format) to replace. options.sfx / options.music; SFX_DEFAULT / MUSIC_DEFAULT
    voice.py            brand voice: your posts per platform (data/brand_voice.json, all jobs) → voice_block()
                        few-shot examples in PROMPT (≤5 per platform, borrows 2 from others if none); notes → Voice:
    history.py          variety engine: least-recently-used angle rotation + recent openers to avoid
                        (persisted in data/content_history.json across jobs)
    carousel.py         Instagram carousel PNGs (1080x1350, Pillow)
    hooks.py            title/hook variants on demand (STYLES: question, bold_claim, number, …) → clip.variants
    cover.py            reel cover short_N.cover.jpg: candidates() scores sampled frames (sharpness × face size ×
                        frontal, never in struck words) → crop 9:16 on the face → text in the IG grid band (240-1680).
                        COVER_STYLE / clip.cover_style "cutout" (default): best frame WITH a face → speaker cut out
                        (white stroke + accent glow) on a blurred brand-tinted frame, text above; no person → frame style
    cutout.py           rembg u2net_human_seg on CPU (~0.6 s, 168 MB in ~/.rembg once; birefnet was killed for memory,
                        isnet kept mic stands/text). Needs a face (face-less frames cut out text cards), keeps ±0.45
                        frame heights around it (touching podcast panels), rejects boxy masks (MAX_FILL). Cached per
                        frame so cover + quote cards cut once. Tests stub `_mask` (conftest): never run rembg in tests
    quote.py            quote cards short_N.quote.png (1:1, X) + .quote45.png (4:5, LinkedIn/IG): pick_quote() = a
                        sentence said in the clip (keywords/hook/number, 6-24 words, struck words excluded — never model
                        text); quote_custom keeps your own line. Credits the source video, not a person
    emoji.py            colour emoji as PNG overlays (libass is monochrome): Noto Color Emoji (OFL) in data/emoji
                        (system font, else downloaded once; no font → no emoji, render unchanged) → png(e, px) cached.
                        caption_events(): one per caption line — typed into the transcript first, else MAP / data/
                        emoji_map.json word (keywords first), ≥ SPACING apart, ≤ MAX; hook_emoji() above the hook card
                        (top layout only). render.place_emoji() mirrors the ASS placement (margins, measured wrap);
                        overlays go after captions/hook, before logo/end card. options.emoji / CAPTION_EMOJI. Tests:
                        conftest blocks _download; test_emoji_crop fakes font_path/_render
    numbers.py          spoken numbers → Num (value, decimals, prefix ₹$€£, suffix % x K M " lakh" " crore", grouping
                        western|indian): digits, English/Hinglish number words, Devanagari digits, magnitudes; skips
                        years, ranges (both halves), fractions, clock times, ordinals, bare numbers < 10. Display keeps
                        how it was said ("2.5 lakh"); Num.text(v) formats count-up frames the same way. find() glues
                        Whisper's space-less pieces first (" 99" ".99" "%" → 99.99%); indices stay on the real words
    inserts.py          motion-graphics cards (template stat_counter). clip.inserts = Insert (SOURCE t, value/prefix/
                        suffix from numbers.py — never model text, label, position auto|upper|lower, approved, custom).
                        candidates() (struck words excluded, unit/size/keyword score) + rule_label(); schedule() =
                        approved only → timing() in OUTPUT time via plan.remap (LEAD before the number, holds to the
                        sentence end, INSERT_MIN/MAX_SECONDS; trimmed-out / struck → none), ≤ INSERTS_MAX, ≥ GAP apart.
                        placement(): above the bottom captions (under middle ones), top in split; box scales with h.
                        suggest(): Gemma sees candidates by id → {id, worth 1-10, label}; < INSERT_MIN_WORTH dropped,
                        number stripped from the label (without_number), label_ok() = every word said in that sentence
                        else rule_label; Gemma failing → rule picks (score ≥ 3). merge() keeps approved/custom cards.
                        produce writing stage (same Gemma load as posts) for clips without inserts_checked
                        (INSERTS_SUGGEST; INSERTS_AUTO approves + marks a rendered short stale); POST
                        /clips/{i}/inserts/suggest (GPU_LOCK, 409, never approves). Transcript word edits drop
                        suggestions on the changed words (forget_changed in mark_stale)
    insert_render.py    InsertSpec (JSON contract, also for a future Blender renderer) → InsertRenderer protocol →
                        RGBA PNG frames cached in inserts/short_N/<hash>/ (hash = spec + renderer name/version; pruned
                        per render). PillowRenderer.stat_counter: count-up (ease-out) + label on a dark card, brand
                        accent, pop-in/fade, held frames hard-linked. render.py: frames input (-framerate) → setpts
                        shift → overlay enable=between (after captions/hook, before emoji/logo); caption emoji hidden
                        while a card shows; pop sfx at its start; a failing card → short without it + render_error.
                        Approved inserts are burned in (edit → stale). Tests fake the renderer (RENDERERS["pillow"])
: loudness 0-100 at 20/s from one chunked ffmpeg PCM pass → waveform.json
    export.py           export pack zip (shorts, srt, posts, carousels, titles.csv)
    render.py           plan_cuts + plan_reframe → captions → ffmpeg
                        (fps → sendcmd+crop@track → select → scale/pad → ass; audio: asplit/atrim/concat) + thumbnail
                        Feed versions (ASPECTS): options.aspects / EXTRA_ASPECTS "4:5" → short_N.45.mp4, "1:1" → .11.mp4,
                        same cut plan + sfx track, own _encode(): plan_reframe(size=) (wider crop, 2:1 split panels;
                        face samples cached so detection runs once), captions/hook/end card/logo get height= (PlayResY,
                        margins scaled, text_scale √(h/1920)). A failed feed version never fails the short: left out
                        of clip.formats + render_error. Export pack feed/, library "(4x5)", Publish links, Shorts tabs
    orchestrator.py     prepare() → [review in UI] → produce(); render_one(); run_pipeline() = auto mode.
                        `Steps` dataclass makes phases injectable (tests use fakes)
  tests/                pytest; FakeLLM in conftest; real-ffmpeg render test
frontend/               React 18 + Vite (JSX, no TS). /api proxied to :8000
  src/components/Workspace.jsx   6-step stepper: Source → Transcript → Pick clips → Shorts → Posts → Publish
  src/components/steps/*.jsx     one file per step; PostEditor.jsx = per-platform editor (used by Posts)
  src/components/InsertEditor.jsx  stat cards per short (Shorts step): PATCH clip {inserts} with the whole list
  src/util.js                    stepStates()/defaultStep() decide which steps unlock and where a job opens;
                                 timeline maths (timelineWindow/snapTime/moveEdge/waveBars) for Timeline.jsx
scripts/setup_wsl.sh    one-time setup;  scripts/dev.sh  runs API + UI
```

## Commands
- Run both: `bash scripts/dev.sh` → UI http://localhost:5173, API docs http://localhost:8000/docs
- Tests: `cd backend && python -m pytest -q`
- Lint: `cd backend && ruff check app tests` (`--fix` to autofix)
- CLI: `cd backend && python -m app.cli "<url>" --clips 3`
- Frontend build check: `cd frontend && npm run build`
- CI: `.github/workflows/ci.yml` runs ruff + pytest (with ffmpeg/fontconfig, no GPU/Ollama) and the frontend build on push/PR

## Job lifecycle (review mode = default)
```
queued → running(prepare: download, transcribe, select 2×N candidates) → review
review: PATCH clips (trim/title/keywords/approve), POST clips (custom from transcript)
POST /produce → running(writing posts for approved clips w/o posts, rendering missing/stale) → done
done: PATCH clip options → stale → POST /clips/{i}/render ; PATCH/POST posts ; status draft→approved→posted
publish: PATCH post {status, url}: posted_at set on → posted (cleared when it goes back); pasting a live url marks it
         posted. History links each posted status to its url. Publish step: per-short file links, one copy button per
         upload-form field (YouTube title/description/tags/pinned comment, one per X thread post)
```
- Edits are refused (409) while queued/running — the worker writes clips at the end and would overwrite.
- Never save a job snapshot taken before slow work (Gemma calls): write through `store.update_clip()` on the
  job as it is now. "New angle" saves while still holding `GPU_LOCK`, and the worker reads the job only
  after acquiring `GPU_LOCK`, so neither can overwrite the other. Deleting a busy job → 409.
- produce() never overwrites existing posts (hand edits survive); only renders clips with no file or `stale`.
- A failed produce/render restores the previous status (review/done) with `error` — never loses edits.
- Server restart mid-produce → job returns to `review` (clips kept); mid-prepare → `failed`. Jobs still waiting
  (queued, no clips — a batch) stay queued: create_app re-submits `store.waiting()` oldest first.
- A waiting job (queued, no clips) can be deleted = removed from the queue; the worker skips a job that's gone.
- `failed` → POST /retry → queued: same folder, so a finished download/transcript is reused (uploads re-run
  from their saved source.mp4; 409 if it's gone). Body `{"topic": ...}` changes the topic first ("" = none).
- Jobs carry `title`/`channel` (batch/Find list → create; else meta.json on the first progress report after
  download; older jobs read meta.json on load, in memory). The Jobs list and workspace header show them.
- UI cache-busting: rendered files are requested with `?v=<clip.version>`.
- Live updates: always save through JobStore (update/update_clip/delete) — that's what wakes the SSE streams;
  a job.json written any other way never reaches the UI. With the stream open, actions don't refetch the list
  (an older fetch could overwrite a newer event).

## Rules / conventions
- **One GPU job at a time.** Worker is single-threaded on purpose; don't add parallel workers.
  Whisper is deleted after transcription and Gemma is unloaded (`keep_alive: 0`) after selection.
- **Clips must be ≤ 59 s** (`max_clip_seconds`). Shorts 1–3 min with any Content ID claim are blocked globally.
- Heavy imports (`faster_whisper`, `yt_dlp`) stay lazy inside functions so tests/API start without GPU.
- Every pipeline step takes an `on_progress(p: float 0..1, msg: str)` callback.
- LLM calls go through the `LLM` protocol so tests use `FakeLLM` — never call Ollama in tests.
- One bad Gemma window must not fail the job (log + skip). Validate every model field.
- Job artifacts live in `backend/data/jobs/<id>/` : source.mp4, transcript.json, candidates.json,
  signals.json (no-speech jobs only — also how main.py knows a job is no-speech), waveform.json (Pick timeline), short_N.{mp4,srt,jpg}, job.json.
- File serving only allows names matching `^[\w.\-]+$` (path traversal guard) — keep it.
- Add/adjust tests with every pipeline change; keep `pytest` and `ruff` green.
- **Timelines:** clip.start/end are SOURCE seconds. After jump cuts, captions use OUTPUT time —
  always go through `CutPlan.remap()`; never time captions from source words directly.
- Pauses: Whisper word timings often run straight over pauses (cut_seconds was 0 on real footage), so
  render passes `audio_silences()` (clip-relative quiet runs: speech p90 − SILENCE_DROP_DB, capped at SILENCE_MAX_DB)
  into `plan_cuts(silences=)`, which subtracts them minus pause_padding each side. Needs ~25 dB voice-to-room SNR.
- Jump cuts: `plan_cuts` snaps every cut to the 1/fps frame grid (`snap_to_frames`). Video: `select` with
  half-frame-offset bounds (`video_select_expr`, exactly N frames per segment) + `setpts=N/FPS/TB` after `fps=`.
  Audio: `asplit` → `atrim` → `acrossfade` (or `concat`) on the SAME keeps (`audio_cut_graph`). Crossfades borrow
  `d` from the removed gap after each keep, so output length and A/V sync are unchanged. **Never use `aselect`**: it is a
  silent no-op in FFmpeg 8.0/8.0.1 (Ubuntu 26.04), leaving audio uncut and out of sync. Guarded by
  test_jumpcut_shortens_output_and_keeps_av_in_sync — verified on FFmpeg 4.4, 8.0, 8.0.1, 8.1 and 9.0.
- Anything burned into the mp4 (timing, options, hook, keywords) marks a rendered clip `stale` when edited
  (`burned_in()` in edit_clip); title doesn't.
- Selection changes: run `python -m app.eval <job>` before/after and compare (select_highlights takes
  `prompt=`, `think=` and fills `trace=`; prepare() writes the trace to candidates.json).
- Two-speaker "split": captions (`build_ass(middle=)`, both styles) sit on the seam (y=960) and the
  hook card is bottom-anchored just above it (`build_hook_ass(seam=)`), so neither covers a face.
- Active speaker: compare thumbnails only after alignment (`patch_change`, ±PATCH_MARGIN px) — raw diffs made a
  swaying silent face look like talking. Tune with SPEAKER_* settings; turns < SPEAKER_MIN_TURN are ignored.
- Lead room: `head_turn()` = (nose x − eye midpoint) / eye distance, + = turned toward image-right (checked on real
  footage both ways). `lead_directions()` is −1/0/+1 per sample with a hold; `smooth()` eases the lead offset
  separately from the face position, so a head turn pans even inside the deadzone. Haar has no landmarks → no
  look lead (movement only). Split panels never lead to within 0.6 face heights of the other face's centre.
- Punch-in zoom (`zoom_graph`) runs AFTER select+setpts and scale/pad (output time, enable bounds from each keep's
  frame count) and BEFORE captions, so text never zooms. Only applied when there are cuts.
- **Reframe runs BEFORE jump-cut select** in the filter chain, so its keyframes are source-relative
  (clip.start = 0). If you move it after select, keyframes must be remapped with CutPlan.remap().
- YuNet model downloads once to data/models from media.githubusercontent.com (github.com/raw gives an
  LFS pointer). Any failure → Haar cascade → if <30% of samples have faces → centre crop. Never crash a job.
- **Content Studio:** stage order is selecting → writing → (unload Gemma) → rendering, so Gemma loads once.
  `GPU_LOCK` (jobs.py) serialises jobs and the "new angle" endpoint (409 if busy). Each platform is
  independent: one failing must not drop the others. Never let prompts invent facts — keep the guardrail line. Brand-voice examples are
  about other topics: voice_block() tells the model to copy the voice only, never their facts/phrases — keep that too.
- To add a creative angle: add one entry to `ANGLES[platform]` (label, instruction). To add a platform:
  ANGLES + RULES + SCHEMAS + an `assemble_<platform>` + NAMES + UI tab in PostsPanel.jsx.
- No-speech clips get `options=RenderOptions(jumpcut=False, caption_style="none")` (Whisper hallucinates words on
  music) and `clip.visual` (vision description), which `clip_text()` hands to Content Studio instead of speech.
  Vision is per candidate and may fail (no vision model, bad JSON) → signal score + "Highlight at m:ss". Numbering
  matches dedupe(): sorted by start, index 1..n. LLM.chat_json takes `images=` (bytes; base64 in OllamaClient);
  `capabilities()` (Ollama /api/show) skips vision for text-only models.
- Whisper drops "um/uh" unless prompted; `whisper_initial_prompt` contains fillers on purpose.
- ASS colours are `&HAABBGGRR` (BGR order!). Canvas is PlayRes 1080x1920. `\c` ignores the AA byte and every
  override carries to later words in the line, so `_colour()` emits `\1a&HAA&\c…&` for EVERY word.
- Anything burned in (hook card, captions, carousel PNGs) goes through `fonts.strip_emoji()`: libass/Pillow draw
  colour emoji as empty boxes. Titles and post copy keep emoji.
- Indic scripts (Hindi etc.): caption fonts have no Devanagari → boxes. Every burned-in text picks its font through
  `fonts.font_for(text, family)` (Nirmala UI from Windows, else Noto Sans <Script>, Mangal…); Pillow loads it with
  `fonts.pil_font()` (right face inside .ttc). Fixed Latin UI text ("swipe →") keeps the default font.
- transcript.json is the one source for captions, .srt, posts and the UI: caption fixes edit it (never words copied
  elsewhere). Word edits mark rendered shorts covering the change `stale` (main.mark_stale). Words keep Whisper's
  leading space. Edits need the job editable (409 while busy).
- Text-based cutting: `clip.removed` = struck word ranges in SOURCE seconds (survive trims; ranges outside the clip
  are ignored). A word is struck when its MIDDLE is inside a range (`is_struck`, same rule in CaptionEditor.jsx).
  `_struck_gaps` widens each range over the pause up to the neighbouring kept words (± pause_padding), so a strike
  joins like a jump cut. Struck words are dropped from captions/.srt and from `clip_text()` (posts). Burned in → stale.
- Hook variants (`POST /clips/{i}/hooks`) take GPU_LOCK like "new angle" (409 if busy) and save onto the job as it
  is now; first call after Gemma unloaded waits for the model load (~2 min measured cold, ~3 s warm).
- Covers are made after every render (`orchestrator.cover_for`, never fails a render) and on `POST /clips/{i}/cover`
  {text, t, auto}; text = clip.cover_text → Instagram cover_text → hook. `?v=<clip.cover_version>` busts the cache.
- Tests never download YuNet: conftest stubs `FaceDetector._ensure_model` (Haar) and resets `reframe._detector`.
- Instagram allows 5 hashtags per caption (since Dec 2025): `assemble_instagram` caps at `IG_MAX_HASHTAGS`, counting
  any the model wrote inline.
- candidates.json outcomes: kept | overlap (lost to a higher-scored clip on the same moment) | ranked_out (fine, but
  more candidates than asked for) | too_short | off_topic (topic jobs: relevance < MIN_RELEVANCE) | invalid.
- Sources that fit in a Short (≤ max_clip_seconds, e.g. a 30 s ad): if every Gemma pick is too short, the whole video
  is the clip (`whole_video_clip`, best pick's title/hook; candidates.json "whole_video": true).
- Topics: the Worker passes `topic=` (and `romanize=True`) to prepare/run ONLY when set, so step callables written without
  it still work. A topic with no matches fails the job with "No moments about …" (retry with another topic).
  No-speech jobs only rank by relevance (vision), never drop: their pool comes from signal peaks.
- Captions have no "top" position on purpose: tracked faces sit in the upper half (tested: covered the eyes) and
  the hook card owns the top. The hook card uses the preset's font.

## Environment notes
- Ollama may run in WSL or on Windows. If on Windows, WSL reaches it at `localhost` only with
  mirrored networking (`.wslconfig: networkingMode=mirrored`); otherwise set `OLLAMA_URL=http://<windows-ip>:11434`
  and start Ollama with `OLLAMA_HOST=0.0.0.0`.
- faster-whisper GPU needs cuBLAS (+ cuDNN 9, currently unused by ctranslate2 4.9) — pip wheels. `dev.sh` sets
  LD_LIBRARY_PATH; `transcribe.preload_cuda_wheels()` loads cuBLAS by path so VS Code / CLI work without it.
- Caption fonts: WSL's fontconfig doesn't see Windows fonts. Scanning /mnt/c/Windows/Fonts per render costs
  ~10 s, so fonts.py indexes it once (cache: data/fonts/windows_fonts.tsv) and copies just the needed family.
- yt-dlp needs Deno (JS runtime) + `yt-dlp-ejs` (challenge solver, comes with `yt-dlp[default]` in requirements)
  and is updated often: `pip install -U "yt-dlp[default]"` first when downloads break.
  Bot check → export cookies.txt from browser, set `YTDLP_COOKIES` in backend/.env.
- NVENC: `ENCODER=h264_nvenc` only if `ffmpeg -encoders | grep nvenc` shows it.
- Thinking models (gemma4): `think` is per phase — `CONTENT_THINK=false` (~2.5x faster posts, no quality loss
  measured), `SELECT_THINK` unset (gemma4 without reasoning ignores the 20-59 s rule). Only sent when set;
  Ollama returns 400 "does not support thinking" if `true` is sent to e.g. gemma3.
- Highlights context budget: one 10-min window ≈ 6.6k prompt tokens (timestamps tokenise at ~2.6 chars/token),
  and thinking needs ~1.2-1.8k more, so `windows()` also caps chunks at half of `num_ctx`
  (`transcript_char_budget()`). Asked-per-window scales with the request (`clips_per_window()`) so review
  mode really gets 2×N candidates. `done_reason=length` from Ollama → clear "ran out of context" error.

## Done
- [x] v0.1 pipeline, API, UI, CLI
- [x] v0.2 jump cuts (silence + filler removal) and animated karaoke captions with Gemma keywords
- [x] v0.3 face-tracking reframe (virtual cameraman: deadzone, smooth pan, snap on scene cuts)
- [x] v0.4 Content Studio: per-platform posts with rotating creative angles, IG carousels, "new angle",
      export pack
- [x] v0.5 Review pipeline UI: 6-step workspace, approve/trim/add clips, per-clip render options +
      single re-render, inline post editing, draft/approved/posted publish checklist
- [x] v0.6 First real end-to-end run (gemma4 + Whisper on RTX 4060) and hardening:
      - Jobs: DELETE of a busy job → 409; worker thread survives any task error; "new angle" no longer
        overwrites concurrent edits or gets overwritten by a queued produce; failed jobs → POST /retry
        (reuses download/transcript) + Retry button
      - Gemma: candidates scale with the request (real 2×N in review mode); transcript windows capped to half
        of num_ctx so thinking fits; "ran out of context" error; per-phase think (CONTENT_THINK/SELECT_THINK);
        Ollama error text surfaced
      - Content: X hashtags no longer duplicated when the model already wrote them
      - Setup/dev: dev.sh fixed (nvm under `set -u`, no-LTS Node, CUDA lib path from namespace wheels,
        `--timeout-graceful-shutdown` so reload can't hang on stalled video streams); cuBLAS preloaded in-app
        (VS Code / CLI); `yt-dlp[default]` for the YouTube challenge solver; caption fonts from Windows on WSL
      - UI: delete disabled while busy, long errors wrap/clamp, failed stage marked ✕, Publish fits phone width

## Roadmap (pick up here)
0. [x] Jump-cut polish: 10 ms audio crossfades at cut points (`JUMPCUT_CROSSFADE_MS`); optional 5% punch-in
       on alternate segments (`JUMPCUT_ZOOM` / per-clip `options.zoom`, `zoom_graph`).
1. [x] **Two-speaker layouts** — stacked (`split`; auto when 2 far-apart faces in ≥60% of samples; captions +
       hook on the seam) and follow-the-talker (`speaker`, or auto with TWO_SPEAKER_LAYOUT=speaker).
       [ ] still open: per-speaker tracking for moving cameras / edits that alternate two-shot and close-ups
       (those fall back to face); audio-based tie-break when both mouths move (crosstalk).
1b.[x] Reframe "lead room": crop shifts `REFRAME_LEAD` × crop width toward where the speaker looks (YuNet landmarks)
       or moves (track slope); held `LEAD_HOLD` s so glances don't pan. Also per speaker in split/speaker modes.
2. [x] **Hook overlay** — `clip.hook` as a boxed title card for the first `HOOK_SECONDS` (own `short_N.hook.ass`,
       burned in after captions; per-clip `options.hook`).
3. [x] **No-speech mode** — < 30 wpm → `nospeech.py`: ffmpeg + numpy signals (no librosa/PySceneDetect),
       peak windows snapped to scene cuts, 4 keyframes per candidate to vision Gemma (`images=`).
       [ ] still open: audio-event cues (cheering/laughter/explosions) and beat-synced cut points for music.
4. [x] Caption presets (`presets.py`: classic = CAPTION_* settings, punch/clean/boxed/neon, custom JSON; per-clip
       `options.caption_preset` / `caption_position` bottom|middle; chips with live CSS previews in ShortsStep).
   [x] Emoji per line: PNG overlays from Noto Color Emoji (emoji.py, = item 18).
5. [x] **Re-render / edit** — UI to nudge start/end ±seconds and re-render a single clip (shipped in v0.5).
6. [x] **SSE progress** instead of polling (`/api/events` for the UI, `/api/jobs/{id}/events` per job).
6b.[x] Pick step timeline: waveform (`waveform.py`, cached waveform.json) with drag handles (snap to sentence edges +
       playhead, Alt = free), drag the clip to move it, click to seek; keys A / [ ] / J K (⇧ 1 s) / I O / Space.
7. [x] **Prompt eval** — every job saves `candidates.json` (each pick + outcome: kept/overlap/too_short/invalid);
       `python -m app.eval` compares models × prompts × think on one transcript → report.md + results.json.
8. [x] Content: brand-voice profile (🎙 Brand voice screen: paste up to 5 posts per platform, or take your hand-edited /
       posted ones → few-shot examples in PROMPT; voice notes replace CONTENT_VOICE).
9. [ ] Content: weekly posting calendar view (spread shorts + posts across days/platforms, .ics export).
10. [ ] Content: Hinglish mode tuned prompt + language detection from transcript.
11. [ ] Auto-publish to own channels (YouTube Data API upload + scheduled publish).

From the review against OpusClip / Vizard / Submagic / Descript / CapCut (Nov 2026). Suggested order: A → C → B → E → K.
Keep it local: no cloud stock/B-roll/TTS APIs.
12. [x] A. Caption editor + custom vocabulary: fix mis-heard words (flows into captions + .srt, marks shorts stale),
        replace-all across the transcript, names/terms passed to Whisper (`hotwords`) for future jobs.
13. [x] C. Prompt-based clipping: optional "topic" on a new job ("salary negotiation") steers the selection prompt.
14. [x] B. Text-based cutting (Descript-style): strike words in the transcript to remove them — extra removed
        ranges into `plan_cuts` (same keep-interval engine as jump cuts).
15. [x] E. Sound design: whoosh on cuts, pop on keywords, background music auto-ducked under speech
        (ffmpeg sidechaincompress) from a local sound folder.
16. [x] K. Reel/Shorts cover image: `cover_text` (already generated) on the best frame (Pillow).
17. [x] D. Reframe override: pick which face to follow (Shorts step); crop keyframes dragged by hand (✋ Adjust crop).
18. [x] F. Colour emoji as PNG overlays (libass is monochrome) + optional auto-emoji per caption line (= roadmap 4).
19. [ ] G. B-roll from your own folder, matched to the clip by Gemma keywords.
20. [x] H. Brand kit: logo watermark, end card with CTA, brand colour (covers/quotes/carousels/captions).
21. [x] I. Multi-aspect export: 1:1 and 4:5 alongside 9:16 (own face-tracked crop, captions re-placed).
22. [x] L. Several title/hook variants to choose from.
23. [x] N. Batch: several links / a playlist / a channel / Find-videos picks as a queue of jobs; watched folder.
24. [ ] Motion-graphics inserts (stat_counter). [x] phases 1-2: numbers.py, inserts.py, insert_render.py (Pillow),
        render overlay, Clip.inserts via PATCH. [x] 3: Gemma picks + labels (checked on real jobs with gemma4: skips
        ages / "ten years later", keeps 70% / 99%; ~1 s per clip warm). [x] 4: InsertEditor.jsx in the Shorts step
        (count-up preview, label, Show/Suggested, Auto/Top/Low, ±0.2 s, your own card; statText() in util.js mirrors
        numbers.fmt). [ ] later: Blender renderer (Windows
        blender.exe via wslpath) behind the same InsertRenderer protocol.
(M = Hinglish/bilingual captions → item 10; O = calendar + auto-publish → items 9 and 11.)

For a later discussion (not planned yet):
25. [ ] Motion graphics with Blender: a BlenderRenderer for 3D / richer card templates behind the same InsertRenderer
        protocol (Windows blender.exe via wslpath; check headless GPU first; templates pinned to one Blender version).
26. [ ] Desktop app. Cheap version first: FastAPI serves frontend/dist (no Vite) + a Windows shortcut that starts the
        backend in WSL and opens `msedge --app=http://localhost:8000`. Installable app for others = Windows-native
        backend (no WSL), installer with GPU check, first-run model downloads, yt-dlp self-update, then a Tauri shell.
27. [ ] ComfyUI as an optional local generator over its HTTP API (workflow JSON in, frames out), behind GPU_LOCK and
        unloaded after use like Gemma (8 GB VRAM: SDXL-class images fine, video models slow): B-roll stills/loops when
        your folder has no match (item 19 / G), stylised cover backgrounds, upscaling. Not ClipForge-as-ComfyUI-nodes.
28. [x] Scene index (from the "Video Intelligence v1" review, Nov 2026) — built, see scenes.py. Slim scope only: scene cuts for every video
        (nospeech.analyse already finds them) → ≤ 1 keyframe per scene / ~20-30 s → vision Gemma description →
        scenes.json (start, end, frame, description, transcript text). Main use: scene lines in the SELECTION prompt
        so visual payoffs (demos, screens, before/after) get picked; then descriptions in transcript search, B-roll
        matching (G), cover / shown-whole hints. Auto-skip vision for single-camera talking heads (few cuts/min, face
        in most samples) so it costs nothing there. ~5 s per frame on gemma4 → ~10-20 min per hour of video
        (estimate). Prove with app.eval on a tutorial-style video before keeping it. Dropped: embedding-based search
        (another model in 8 GB VRAM), video Q&A (different product). Contact sheet = optional debug view.
