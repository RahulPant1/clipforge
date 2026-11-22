"""Central settings. Override any value via environment variables or backend/.env."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Storage
    data_dir: Path = Path("data")

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
