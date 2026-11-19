"""Step 2 — speech-to-text with word timestamps (faster-whisper, GPU)."""
import gc
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..config import settings
from ..models import Segment, Word

ProgressFn = Callable[[float, str], None]
log = logging.getLogger(__name__)

# cuBLAS comes as a pip wheel (nvidia-cublas-cu12) so no system CUDA install is needed. CTranslate2 dlopen()s it
# by soname, which only works if its folder is on LD_LIBRARY_PATH — dev.sh sets it, but VS Code debugging and
# `python -m app.cli` don't. Loading it by full path first makes that later lookup succeed anywhere.
# cuDNN is left out on purpose: measured with ctranslate2 4.9 + faster-whisper 1.2 (word timestamps + VAD) it is
# never loaded, and preloading its sub-libs costs ~95 MB RSS for nothing. dev.sh still puts it on the path.
_CUDA_WHEELS = {"nvidia.cublas.lib": ("libcublasLt.so.*", "libcublas.so.*")}
_cuda_preloaded = False


def preload_cuda_wheels() -> list[str]:
    """Load cuBLAS from the pip wheel (RTLD_GLOBAL). Returns what was loaded; no-op without the wheel."""
    global _cuda_preloaded
    if _cuda_preloaded:
        return []
    _cuda_preloaded = True
    import ctypes
    import importlib

    pending: list[str] = []
    for pkg, patterns in _CUDA_WHEELS.items():
        try:
            folder = Path(list(importlib.import_module(pkg).__path__)[0])  # namespace pkg: __file__ is None
        except (ImportError, IndexError):
            continue
        pending += [str(f) for pat in patterns for f in sorted(folder.glob(pat))]
    loaded: list[str] = []
    while pending:  # wheel libs need each other by soname and have no RUNPATH: retry until no progress
        failed = []
        for f in pending:
            try:
                ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
                loaded.append(f)
            except OSError:
                failed.append(f)
        if len(failed) == len(pending):
            log.warning("could not preload CUDA libs %s; relying on LD_LIBRARY_PATH", [Path(f).name for f in failed])
            break
        pending = failed
    return loaded


def transcribe(video: Path, job_dir: Path, on_progress: ProgressFn | None = None) -> list[Segment]:
    cache = job_dir / "transcript.json"
    if cache.exists():
        return [Segment(**s) for s in json.loads(cache.read_text())]

    if settings.whisper_device == "cuda":
        preload_cuda_wheels()
    from faster_whisper import WhisperModel  # lazy: heavy import, GPU init

    if on_progress:
        on_progress(0.0, "Extracting audio")
    audio = load_audio(video)

    model = WhisperModel(
        settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    segments_iter, info = model.transcribe(
        audio,
        word_timestamps=True,
        vad_filter=True,
        language=settings.whisper_language,
        initial_prompt=settings.whisper_initial_prompt or None,
    )
    total = info.duration or 1.0
    segs: list[Segment] = []
    for s in segments_iter:  # generator: transcription happens while iterating
        segs.append(Segment(
            start=s.start, end=s.end, text=s.text.strip(),
            words=[Word(start=w.start, end=w.end, word=w.word) for w in (s.words or [])],
        ))
        if on_progress:
            on_progress(min(s.end / total, 1.0), f"Transcribing ({info.language})")

    # Release Whisper VRAM before Gemma loads
    del model
    gc.collect()

    cache.write_text(json.dumps([s.model_dump() for s in segs], ensure_ascii=False))
    return segs


def load_audio(video: Path, sr: int = 16000):
    """Decode to 16 kHz mono float32 with ffmpeg (avoids PyAV version issues, fast on long files)."""
    import numpy as np

    res = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {res.stderr.decode()[-400:]}")
    return np.frombuffer(res.stdout, dtype=np.float32)


def words_per_minute(segs: list[Segment]) -> float:
    if not segs:
        return 0.0
    span = max(segs[-1].end - segs[0].start, 1.0)
    return sum(len(s.text.split()) for s in segs) / (span / 60)
