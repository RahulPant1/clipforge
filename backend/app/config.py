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
    jumpcut_crossfade_ms: float = 10.0  # audio crossfade at each cut (avoids clicks); 0 = hard cuts
    filler_words: set[str] = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}

    # Render
    encoder: str = "libx264"  # libx264 | h264_nvenc
    output_fps: int = 30

    # Reframe (speaker-following crop)
    reframe_mode: str = "face"  # face | center
    face_sample_fps: float = 5  # detections per second
    face_deadzone: float = 0.05  # ignore face moves smaller than this (fraction of frame width)
    face_smoothing: float = 0.35  # 0..1 per sample; higher = faster pans
    face_min_coverage: float = 0.3  # need faces in >=30% of samples, else centre crop
    scene_cut_threshold: float = 0.5  # histogram correlation below this = camera cut (snap, no pan)

    # Captions
    caption_style: str = "karaoke"  # karaoke (animated ASS) | simple (plain SRT burn-in)
    caption_words: int = 3
    caption_font: str = "Arial"
    caption_size: int = 78  # ASS units on a 1080x1920 canvas
    highlight_scale: int = 110  # active word size in %
    caption_margin_v: int = 420  # px from bottom; keeps text clear of the Shorts UI
    caption_color: str = "&H00FFFFFF"  # ASS &HAABBGGRR — white
    highlight_color: str = "&H0000FFFF"  # active word — yellow
    keyword_color: str = "&H0055FF55"  # Gemma keywords — green


settings = Settings()
