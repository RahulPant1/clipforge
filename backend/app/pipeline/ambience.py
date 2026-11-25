"""Cache the non-vocal stem of a source clip for optional local dubbing."""
import hashlib
import shutil
import subprocess
from pathlib import Path

from ..config import settings
from ..files import atomic_output
from ..models import Clip

MODEL = "htdemucs"


def available() -> bool:
    return shutil.which(settings.demucs_executable) is not None


def _run(command: list[str], failure: str, timeout: int) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(failure) from e
    if result.returncode:
        raise RuntimeError(f"{failure}: {result.stderr.strip()[-220:]}")


def track_for(video: Path, job_dir: Path, clip: Clip) -> Path:
    meta = video.stat()
    stamp = f"{MODEL}:{video.resolve()}:{meta.st_size}:{meta.st_mtime_ns}:{clip.start:.3f}:{clip.end:.3f}"
    key = hashlib.sha256(stamp.encode()).hexdigest()[:16]
    folder = job_dir / "ambience"
    stem = folder / f"short_{clip.index}_{key}.wav"
    if stem.is_file():
        return stem
    executable = shutil.which(settings.demucs_executable)
    if not executable:
        raise RuntimeError("Keep source ambience needs Demucs. Install it or set DEMUCS_EXECUTABLE to its path.")

    folder.mkdir(exist_ok=True)
    with atomic_output(stem) as staged:
        source = staged.parent / "source.wav"
        _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}",
            "-i", str(video), "-map", "0:a:0", "-ac", "2", "-ar", "44100", str(source),
        ], "Could not extract source audio for ambience", 90)
        _run([
            executable, "-d", "cpu", "-n", MODEL, "--two-stems", "vocals", "--shifts", "0",
            "-o", str(staged.parent), str(source),
        ], "Could not separate source ambience with Demucs", 900)
        separated = staged.parent / MODEL / source.stem / "no_vocals.wav"
        if not separated.is_file() or not separated.stat().st_size:
            raise RuntimeError("Demucs did not produce an ambience track")
        separated.replace(staged)
    for old in folder.glob(f"short_{clip.index}_*.wav"):
        if old != stem:
            old.unlink(missing_ok=True)
    return stem
