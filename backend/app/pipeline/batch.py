"""Batch: several links / playlists → a list of videos to queue as jobs (one job each, the worker runs them in turn).

expand() reads pasted text: any mix of video links, playlist / channel links (listed with yt-dlp's flat
extraction — titles + lengths, no download, ~2 s per playlist) and other http(s) links (kept as they are).
Duplicates (same video, any link form) are dropped.
"""
import logging
import re

from . import discover
from .discover import VIDEO_ID

log = logging.getLogger(__name__)

MAX_VIDEOS = 50  # per batch: ~1-2 h of GPU work per long video, so 50 is already days
_YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([\w-]{11})")
_LIST = re.compile(r"(?:youtube\.com/(?:playlist\?|@[\w.-]+|c/|channel/|user/)|[?&]list=)", re.I)
_GONE = ("[private video]", "[deleted video]")


def links(text: str) -> list[str]:
    """Every http(s) link in the text (one per line, or separated by spaces/commas), in order."""
    return [u.rstrip(".,;)") for u in re.findall(r"https?://[^\s,<>\"']+", text or "")]


def is_list(url: str) -> bool:
    """A playlist or a channel — not a single video that merely sits in a playlist (watch?v=…&list=… is the video)."""
    return bool(_LIST.search(url)) and not (_YT_ID.search(url) and "/playlist" not in url)


def video(vid: str, **kw) -> dict:
    return {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": "", "channel": "",
            "duration": None, **kw}


def playlist(url: str, limit: int = MAX_VIDEOS) -> tuple[str, list[dict]]:
    """(playlist title, its videos) — flat listing, no download. Channel links list the channel's uploads."""
    if re.search(r"youtube\.com/(?:@[\w.-]+|c/[\w.-]+|channel/[\w-]+|user/[\w.-]+)/?$", url):
        url = url.rstrip("/") + "/videos"  # the channel page itself is a list of tabs, not of videos
    info = discover._extract(url, flat=True, limit=limit)
    out = []
    for e in info.get("entries") or []:
        vid = e.get("id")
        if not vid or not VIDEO_ID.match(vid) or (e.get("title") or "").lower() in _GONE:
            continue
        if e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        out.append(video(vid, title=e.get("title") or "", channel=e.get("channel") or e.get("uploader") or "",
                         duration=e.get("duration")))
    return info.get("title") or "", out


def expand(text: str, limit: int = MAX_VIDEOS) -> dict:
    """{"videos": [...], "errors": [{"url", "error"}], "more": bool}; each video says which link it came from."""
    from ..library import video_key

    videos, errors, seen, more = [], [], set(), False
    for url in links(text):
        if len(videos) >= limit:
            more = True
            break
        if is_list(url):
            try:
                name, found = playlist(url, limit)
            except Exception as e:  # one bad playlist must not lose the other links
                log.warning("batch: can't list %s: %s", url, e)
                msg = re.sub(r"^ERROR:\s*", "", re.sub(r"\x1b\[[\d;]*m", "", str(e)).strip())  # yt-dlp colours it
                errors.append({"url": url, "error": msg[:200]})
                continue
            if not found:
                errors.append({"url": url, "error": "no videos found in this playlist"})
            items = [{**v, "from": name or url} for v in found]
        else:
            m = _YT_ID.search(url)
            items = [video(m[1], **{"from": ""})] if m else [
                {"id": "", "url": url, "title": "", "channel": "", "duration": None, "from": ""}]
        for v in items:
            key = video_key(v["url"])
            if key in seen:
                continue
            if len(videos) >= limit:
                more = True
                break
            seen.add(key)
            videos.append(v)
    return {"videos": videos, "errors": errors, "more": more}
