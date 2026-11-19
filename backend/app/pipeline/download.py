"""Step 1 — fetch the source video with yt-dlp (or accept a local file)."""
import json
from collections.abc import Callable
from pathlib import Path

from ..config import settings

ProgressFn = Callable[[float, str], None]


def download(url: str, job_dir: Path, on_progress: ProgressFn | None = None) -> Path:
    import yt_dlp  # lazy import keeps tests and API startup light

    out = job_dir / "source.mp4"
    if out.exists():
        return out

    def hook(d: dict) -> None:
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                on_progress(min(done / total, 1.0), "Downloading video")

    h = settings.max_height
    opts = {
        "format": f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(job_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "noprogress": True,
        "noplaylist": True,
    }
    if settings.ytdlp_cookies:
        opts["cookiefile"] = str(settings.ytdlp_cookies)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}

    meta = {"title": info.get("title", ""), "channel": info.get("channel") or info.get("uploader", ""),
            "url": info.get("webpage_url", url)}
    (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))

    if not out.exists():  # some formats merge to .mkv/.webm — normalise name
        candidates = sorted(job_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp finished but no video file was found")
        candidates[0].rename(out)
    return out
