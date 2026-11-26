"""Finalize MediaRecorder containers for seeking, duration probing and later FFmpeg work."""
import subprocess
from pathlib import Path

from ..files import atomic_output


def normalize(source: Path, destination: Path) -> None:
    with atomic_output(destination) as staged:
        base = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0"]
        video = (["-c:v", "copy"] if source.suffix == ".mp4" else
                 ["-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "2", "-pix_fmt", "yuv420p"])
        try:
            result = subprocess.run(
                [*base, *video, "-c:a", "aac", "-movflags", "+faststart", str(staged)],
                capture_output=True, timeout=240,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("Could not finish the recording. Try recording a shorter clip.") from error
        if result.returncode or not staged.is_file() or not staged.stat().st_size:
            raise ValueError("Could not read the recording. Check that it has both video and microphone audio.")
