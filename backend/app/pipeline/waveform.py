"""Audio waveform for the Pick step timeline: loudness levels (0-100) at RATE per second, cached per job.

One ffmpeg pass over the source's audio as 8 kHz mono PCM, read in chunks (a 1 h video never sits in RAM as
samples). RMS in dB, mapped FLOOR_DB..0 dBFS -> 0..100, so quiet speech still shows and pauses read as gaps.
"""
import json
import logging
import subprocess
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

RATE = 20  # levels per second (50 ms each)
SAMPLE_RATE = 8000
FLOOR_DB = -60.0
_lock = threading.Lock()  # two cards / tabs asking at once compute it once


def levels(video: Path) -> list[int]:
    """Loudness per 1/RATE s as 0..100 ([] if there's no audio track or ffmpeg fails)."""
    hop = SAMPLE_RATE // RATE
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out: list[int] = []
    rest = np.zeros(0, dtype=np.float32)
    assert proc.stdout
    while chunk := proc.stdout.read(hop * 2 * RATE * 30):  # ~30 s at a time
        pcm = np.concatenate([rest, np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768])
        n = len(pcm) // hop
        rest = pcm[n * hop:]
        if n:
            rms = np.sqrt(np.mean(pcm[:n * hop].reshape(n, hop) ** 2, axis=1))
            db = 20 * np.log10(rms + 1e-9)
            out.extend(np.clip((db - FLOOR_DB) / -FLOOR_DB * 100, 0, 100).round().astype(int).tolist())
    if proc.wait() != 0:
        log.warning("waveform: ffmpeg failed on %s", video)
        return []
    return out


def load(job_dir: Path) -> dict:
    """{"rate": RATE, "levels": [...]} for job_dir/source.mp4, computed once into waveform.json."""
    cache = job_dir / "waveform.json"
    with _lock:
        if cache.exists():
            try:
                data = json.loads(cache.read_text())
                if data.get("rate") == RATE:
                    return data
            except ValueError:
                pass
        data = {"rate": RATE, "levels": levels(job_dir / "source.mp4")}
        if data["levels"]:  # don't cache a failure
            cache.write_text(json.dumps(data, separators=(",", ":")))
        return data
