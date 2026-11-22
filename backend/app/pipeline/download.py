"""Step 1 — fetch the source video with yt-dlp (or accept a local file)."""
import json
from collections.abc import Callable
from pathlib import Path

from ..config import settings

ProgressFn = Callable[[float, str], None]


META_VERSION = 2  # meta.json written with the full field set below (older jobs: title/channel/url only)


def video_meta(info: dict, url: str) -> dict:
    """What the history (library.py) records about a video, from yt-dlp's info dict. Missing fields stay empty."""
    up = str(info.get("upload_date") or "")
    return {
        "meta_version": META_VERSION,
        "title": info.get("title", ""),
        "channel": info.get("channel") or info.get("uploader", ""),
        "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
        "url": info.get("webpage_url", url),
        "video_id": info.get("id", ""),
        "upload_date": f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else "",
        "duration": info.get("duration"),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "subscribers": info.get("channel_follower_count"),
        "language": info.get("language") or "",
        "categories": list(info.get("categories") or [])[:5],
        "tags": list(info.get("tags") or [])[:20],
        "description": (info.get("description") or "")[:1000],
    }


def fetch_meta(url: str) -> dict:
    """Metadata only, no download (fills in jobs made before META_VERSION 2)."""
    import yt_dlp

    opts = {"quiet": True, "noprogress": True, "noplaylist": True, "skip_download": True}
    if settings.ytdlp_cookies:
        opts["cookiefile"] = str(settings.ytdlp_cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return video_meta(ydl.extract_info(url, download=False) or {}, url)


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

    (job_dir / "meta.json").write_text(json.dumps(video_meta(info, url), ensure_ascii=False))

    if not out.exists():  # some formats merge to .mkv/.webm — normalise name
        candidates = sorted(job_dir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp finished but no video file was found")
        candidates[0].rename(out)
    return out
