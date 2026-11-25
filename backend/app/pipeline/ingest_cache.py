"""Share source media and unedited transcripts while keeping job files independent."""
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from ..config import settings
from ..files import atomic_output
from ..library import video_key
from .vocabulary import hotwords


def _key(*values) -> str:
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def _cached(kind: str, key: str, build: Callable[[Path], None]) -> Path:
    root = settings.data_dir / "cache" / kind
    target = root / key
    if not target.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".build-", dir=root) as temp:
            staged = Path(temp) / "entry"
            staged.mkdir()
            build(staged)
            try:
                staged.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
    return target


def _digest(source: Path) -> str:
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy(source: Path, destination: Path, *, link: bool = False) -> None:
    if destination.exists() and source.samefile(destination):
        return
    with atomic_output(destination) as staged:
        if link:
            try:
                os.link(source, staged)
                return
            except OSError:
                pass
        shutil.copyfile(source, staged)


def source_for(source: str, job_dir: Path, fetch: Callable[[str, Path], Path]) -> Path:
    local = Path(source)
    video = job_dir / "source.mp4"
    meta = job_dir / "meta.json"
    if local.is_file() or (video.is_file() and not source.startswith(("http://", "https://"))):
        original = video if video.is_file() else local
        entry = _cached("sources", _key("local-v1", _digest(original)),
                        lambda staged: _copy(original, staged / "source.mp4", link=original == video))
        _copy(entry / "source.mp4", video, link=True)
        if not meta.exists():
            meta.write_text(json.dumps({"title": local.stem, "channel": "", "url": ""}), encoding="utf-8")
    elif not video.is_file():
        host = urlsplit(source).hostname
        identity = video_key(source) if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"} else ""
        if not identity.startswith("yt:"):
            identity = source
        cookies = settings.ytdlp_cookies
        cookie_profile = _digest(cookies) if cookies and cookies.is_file() else None

        def download(staged: Path) -> None:
            result = fetch(source, staged)
            if result.stat().st_size == 0:
                raise RuntimeError("The downloaded source video is empty")

        entry = _cached("sources", _key("download-v1", identity, settings.max_height, cookie_profile), download)
        if (entry / meta.name).is_file():
            _copy(entry / meta.name, meta)
        _copy(entry / video.name, video, link=True)
    return video


def restore_transcript(video: Path, job_dir: Path, build: Callable[[Path], None], *,
                       whisper_model: str | None = None) -> None:
    """Copy raw transcript data into the job before any romanization or manual edits."""
    if (job_dir / "transcript.json").is_file():
        return
    key = _key("whisper-v1", _digest(video), whisper_model or settings.whisper_model, settings.whisper_device,
               settings.whisper_compute_type, settings.whisper_language, settings.whisper_initial_prompt, hotwords())
    entry = _cached("transcripts", key, build)
    for name in ("transcript_info.json", "transcript.json"):
        if (entry / name).is_file():
            _copy(entry / name, job_dir / name)


def source_cache_usage() -> dict:
    root = settings.data_dir / "cache" / "sources"
    total = reclaimable = 0
    for source in root.glob("*/source.mp4"):
        try:
            info = source.stat()
        except FileNotFoundError:
            continue
        total += info.st_size
        if info.st_nlink == 1:
            reclaimable += info.st_size
    return {"dir": str(root.resolve()), "bytes": total, "reclaimable_bytes": reclaimable}
