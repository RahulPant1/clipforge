"""Central settings. Override any value via environment variables or backend/.env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Storage
    data_dir: Path = Path("data")
    # History of every video + its shorts (library.py): readable folders + "ClipForge history.xlsx".
    # Default data/library; e.g. /mnt/c/Users/<you>/Videos/ClipForge to browse it in Windows (files copied there).
    library_dir: Path | None = None
    library_fetch_meta: bool = True  # look up full YouTube metadata (no download) for jobs that lack it
    hinglish_default: bool = True  # new jobs: Hindi speech → transcript/captions in Latin letters (hinglish.py)
    # Watched folder (watch.py): every video dropped here becomes a job. Off unless set, e.g.
    # WATCH_DIR=/mnt/c/Users/<you>/Videos/ClipForge inbox. Files are copied, never moved or deleted.
    watch_dir: Path | None = None
    watch_interval: float = 30.0  # seconds between scans
    watch_clips: int = 5
    watch_mode: str = "review"  # review | auto
    watch_topic: str = ""

    # LLM (Gemma via Ollama)
    ollama_url: str = "http://localhost:11434"
    gemma_model: str = "gemma3:4b"
    ollama_num_ctx: int = 8192
    ollama_temperature: float = 0.3
    ollama_timeout: int = 900
    # Reasoning ("think") for thinking models like gemma4/qwen3, per phase. None = not sent (model default).
    # false is accepted by every model; true makes Ollama reject models without thinking support (e.g. gemma3).
    # Measured on gemma4: post writing with think=false is ~2.5x faster with no visible quality loss, but clip
    # selection with think=false ignores the 20-59 s rule (picks 3-13 s single sentences) — keep it on there.
    select_think: bool | None = None
    content_think: bool | None = None
    unload_llm_after_job: bool = True  # free VRAM so Whisper has room on the next job

    # Speech-to-text (faster-whisper)
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3
    whisper_device: str = "cuda"  # cuda | cpu
    whisper_compute_type: str = "int8_float16"  # use "int8" on cpu
    whisper_language: str | None = None  # None = auto-detect; "en", "hi", ...
    # Whisper tends to silently drop "um/uh"; a filler-heavy prompt makes it transcribe them,
    # which is what lets jump cuts remove them. Set to "" to disable.
    whisper_initial_prompt: str = "Umm, uh, so, hmm, like, you know, I was, uh, thinking."

    # Highlight selection
    window_seconds: int = 600  # transcript chunk size sent to Gemma
    clips_per_window: int = 2  # minimum asked per chunk; raised automatically so you get the clips you asked for
    min_clip_seconds: float = 20
    # No-speech mode (transcript < 30 words/min: music, gameplay, sport): clips from loudness/motion/cut peaks
    no_speech_mode: bool = True  # false = such videos fail with "not enough speech"
    no_speech_clip_seconds: float = 30  # length of each no-speech clip (edges snap to scene cuts)
    no_speech_vision: bool = True  # keyframes → Gemma scores/titles (vision model: gemma3:4b+, gemma4)
    vision_think: bool | None = False  # reasoning while scoring keyframes (false = much faster)
    max_clip_seconds: float = 59  # keep < 60s: 1-3 min Shorts with any Content ID claim get blocked

    # Content Studio (per-platform posts)
    content_enabled: bool = True
    content_platforms: list[str] = ["x", "instagram", "youtube", "linkedin"]
    content_language: str = "English"  # e.g. "Hinglish", "Hindi"
    content_voice: str = "smart, friendly creator who explains things simply"
    content_temperature: float = 0.85  # more creative than clip selection
    brand_handle: str = ""  # e.g. "@rahul.builds" — used in CTAs and carousel footer
    credit_source: bool = False  # append "Clip from: <video> — <channel>" (needed for CC-BY content)
    carousel_enabled: bool = True
    carousel_font: str = ""  # path to a .ttf; auto-detects DejaVu/Liberation/Windows Arial if empty
    carousel_accent: str = "#ff4d6d"
    cover_style: str = "cutout"  # reel cover: cutout (speaker cut out on a brand-tinted blur) | frame (frame + text)
    quote_cards: bool = True  # quote card images (1:1 for X, 4:5 for LinkedIn/IG) next to each short

    # Download
    ytdlp_cookies: Path | None = None  # path to cookies.txt if YouTube asks "confirm you're not a bot"
    max_height: int = 1080

    # Jump cuts (silence + filler removal)
    jumpcut_enabled: bool = True
    max_pause: float = 0.4  # pauses longer than this get cut
    pause_padding: float = 0.08  # breathing room kept around each word
    # Whisper often stretches word timings over pauses, so pauses are also found in the audio itself:
    # 20 ms frames quieter than (speech level - SILENCE_DROP_DB); that threshold is never above SILENCE_MAX_DB.
    # Needs room noise ~25 dB under the voice (normal for a close mic); noisier rooms: lower SILENCE_DROP_DB
    jumpcut_audio: bool = True
    silence_drop_db: float = 25.0
    silence_max_db: float = -30.0
    jumpcut_crossfade_ms: float = 10.0  # audio crossfade at each cut (avoids clicks); 0 = hard cuts
    jumpcut_zoom: bool = False  # punch in on every other segment (per-clip override in the Shorts step)
    jumpcut_zoom_scale: float = 1.05  # 1.05 = 5% punch-in
    filler_words: set[str] = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}

    # Render
    encoder: str = "libx264"  # libx264 | h264_nvenc
    output_fps: int = 30

    # Reframe (speaker-following crop)
    reframe_mode: str = "auto"  # auto (two speakers -> two_speaker_layout, else face) | face | split | speaker | center
    two_speaker_layout: str = "stack"  # what auto does with two speakers: stack (split) | speaker (follow talker)
    face_sample_fps: float = 5  # detections per second
    face_deadzone: float = 0.05  # ignore face moves smaller than this (fraction of frame width)
    face_smoothing: float = 0.35  # 0..1 per sample; higher = faster pans
    reframe_lead: float = 0.15  # lead room: crop shift (share of its width) toward where the face looks/moves; 0 = off
    lead_window: float = 1.5  # s of head turn / movement read (median) per decision
    lead_hold: float = 2.0  # a new lead-room side must last this long (glances don't pan)
    face_min_coverage: float = 0.3  # a shot needs faces in >=30% of its samples to be cropped on a face
    reframe_no_face: str = "fit"  # shots without a face: fit (whole frame over a blurred copy) | crop (centre/hold)
    two_speaker_min_share: float = 0.6  # auto: two faces in >=60% of samples -> stacked two-speaker layout
    speaker_window: float = 1.0  # s of mouth activity averaged before deciding who talks
    speaker_min_shot: float = 2.0  # s on a speaker before the camera may cut away
    speaker_margin: float = 0.3  # the other speaker must be 30% more active to take the shot
    speaker_min_turn: float = 1.0  # s of clearly talking before it counts as a turn (ignores "mm-hm")
    split_face_height: float = 0.3  # stacked layout: face height as a share of its 960 px panel
    scene_cut_threshold: float = 0.5  # histogram correlation below this = camera cut (snap, no pan)

    # Captions
    caption_style: str = "karaoke"  # karaoke (word-by-word highlight) | simple (whole line, no animation) | none
    caption_preset: str = "classic"  # look: classic (the CAPTION_* settings below) | punch | clean | boxed | neon
    #   | your own in data/caption_presets.json (see pipeline/presets.py)
    caption_position: str = "bottom"  # bottom | middle (two-speaker stack always uses the seam)
    caption_words: int = 3
    caption_font: str = "Arial"
    caption_size: int = 78  # ASS units on a 1080x1920 canvas
    highlight_scale: int = 110  # active word size in %
    caption_margin_v: int = 420  # px from bottom; keeps text clear of the Shorts UI
    # Sound design (sfx.py): whoosh on jump cuts + pop on keywords, and background music from data/sounds/music,
    # ducked under the voice. Defaults for new shorts; each short can switch them in the Shorts step.
    sfx_default: bool = False
    music_default: str = ""  # a file name in data/sounds/music ("" = no music)
    sfx_whoosh_db: float = -14.0  # effect loudness (the starter sounds peak near -6 dBFS)
    sfx_pop_db: float = -12.0
    music_lufs: float = -30.0  # every track is loudness-normalised to this (speech sits ~-16), then ducked ~7-10 dB
                               # under speech — the file's own loudness doesn't matter
    # Colour emoji (emoji.py, Noto Color Emoji downloaded once to data/emoji): one above a caption line when a word
    # has one (or you typed one into the transcript) + the hook emoji above the hook card. Per short (Shorts step).
    caption_emoji: bool = False
    # Feed versions (render.ASPECTS): besides the 9:16 short, also render "4:5" (Instagram feed, LinkedIn) and/or
    # "1:1" (X, LinkedIn) — own face-tracked crop (wider: more of the scene), captions/hook/logo re-placed.
    # Comma list for new shorts, e.g. EXTRA_ASPECTS=4:5 ; each short can switch them in the Shorts step.
    extra_aspects: str = ""
    # Clip judge (judge.py): every candidate checked for a missing setup / payoff, meaning kept, hook, value →
    # ready | trim (with a one-click fix) | needs_context | reject. One Gemma call per candidate after selection.
    judge_enabled: bool = True
    judge_context_seconds: float = 30.0  # transcript shown before and after the clip
    judge_think: bool | None = None  # unset = model default (gemma4 reasons): measured, reasoning off flagged 88 %
                                     # of clean clips and caught little; on: damage caught 100 %, ~12 s per clip
    # Learning mode (learn.py): study notes instead of shorts — summary, chapters, concepts, notes, quiz
    learn_think: bool | None = None  # unset = model default
    # Scene index (scenes.py): what is on screen, one keyframe per scene → vision Gemma, fed to clip selection as
    # ON SCREEN lines. auto = only videos that are more than a talking head (cuts / few faces); on = always; off
    scene_index: str = "auto"
    scene_span: float = 30.0  # s: one keyframe per scene, long scenes split into spans this long
    scene_min: float = 4.0  # s: shorter scenes are merged into the one before
    scene_max: int = 160  # keyframes per video at most (longer spans instead)
    scene_skip_cuts_per_min: float = 2.0  # auto: fewer cuts a minute than this…
    scene_skip_face_share: float = 0.75  # …and a face in at least this share of frames → talking head, skipped
    # Your voice (tts.py): Qwen3-TTS voice cloning in its own env, started only when a voice is built / speaks
    tts_python: str = ""  # python of the TTS env ("" = ~/miniconda3/envs/clipforge-tts/bin/python)
    tts_model: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"  # or Qwen/Qwen3-TTS-12Hz-0.6B-Base (less VRAM)
    tts_attn: str = "sdpa"  # sdpa (built in) | flash_attention_2 (needs flash-attn in the TTS env)
    tts_language: str = "Auto"  # English, Chinese, ... (10 languages; no Hindi) or Auto
    tts_timeout: float = 900.0  # s per worker run (the first one downloads the model)
    tts_max_new_tokens: int = 2048  # ≈ 2.7 min of speech per line at 12.5 codes/s
    # Inserts (inserts.py): motion-graphics cards burned in when a number is spoken (stat_counter). Only approved
    # ones render; drawn by INSERT_RENDERER (pillow; a Blender renderer can plug into insert_render.py later).
    inserts_suggest: bool = True  # produce: Gemma picks which spoken numbers get a card (suggestions, not approved)
    inserts_auto: bool = False  # …and approves them, so they're burned in without a look
    insert_min_worth: int = 6  # Gemma's 1-10 "worth a card" below this → no suggestion
    inserts_max: int = 2  # per short, ≥ 3 s apart
    insert_renderer: str = "pillow"
    insert_count_seconds: float = 0.8  # the count-up; then it holds until the sentence ends
    insert_min_seconds: float = 1.8
    insert_max_seconds: float = 3.5
    # Hook title card: clip.hook as big text for the first seconds (top of frame; captions are at the bottom)
    hook_overlay: bool = True  # per-clip override in the Shorts step
    hook_seconds: float = 3.0
    hook_size: int = 84  # ASS units on a 1080x1920 canvas
    hook_margin_v: int = 260  # px from the top; clears the Shorts top bar
    hook_color: str = "&H00000000"  # ASS &HAABBGGRR — black text…
    hook_box_color: str = "&H0000FFFF"  # …on a yellow box
    caption_color: str = "&H00FFFFFF"  # ASS &HAABBGGRR — white
    highlight_color: str = "&H0000FFFF"  # active word — yellow
    keyword_color: str = "&H0055FF55"  # Gemma keywords — green


settings = Settings()
