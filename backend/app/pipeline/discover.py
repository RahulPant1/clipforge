"""Find videos worth clipping for a topic: YouTube search (yt-dlp, no API key) → filter → score by the captions.

search(): one yt-dlp search (~2 s), long-form only (MIN..MAX minutes, no live streams), optionally only Creative
Commons videos (YouTube's own search filter — reusable with attribution; Content ID claims block Shorts).
score(): per video, its details + YouTube's captions (no video download, ~3-4 s): where and how much the topic is
discussed → 0-100, the best moments (with links to that second), language, licence. The UI scores the results in
parallel so cards fill in as they arrive. Hindi captions (Devanagari) are matched in Hinglish (hinglish.to_latin).
"""
import functools
import json
import logging
import math
import re
import urllib.parse

from ..config import settings
from .hinglish import DEVANAGARI, to_latin

log = logging.getLogger(__name__)

CC_FILTER = "EgIwAQ=="  # YouTube search "Creative Commons" filter (the sp= parameter)
VIDEO_ID = re.compile(r"^[\w-]{11}$")
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "how", "what", "why", "is", "are",
        "my", "your", "about", "from", "at", "by", "vs", "kaise", "kya", "aur", "ki", "ka", "ke", "hai"}
MOMENT_GAP = 90  # s between reported moments


def _opts(**kw) -> dict:
    o = {"quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True, **kw}
    if settings.ytdlp_cookies:
        o["cookiefile"] = str(settings.ytdlp_cookies)
    return o


def _extract(url: str, flat: bool = False, limit: int | None = None) -> dict:
    """yt-dlp info for a search/video URL (replaced in tests — never hit YouTube there)."""
    import yt_dlp

    opts = _opts(**({"extract_flat": "in_playlist"} if flat else {}), **({"playlistend": limit} if limit else {}))
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _fetch(url: str) -> bytes:
    import yt_dlp

    with yt_dlp.YoutubeDL(_opts()) as ydl:
        return ydl.urlopen(url).read()


def search(topic: str, n: int = 20, cc: bool = False, min_minutes: float = 8, max_minutes: float = 90) -> list[dict]:
    """Long-form videos about `topic`, YouTube's order, filtered by length; cc=True: Creative Commons only."""
    topic = " ".join(topic.split())[:120]
    if not topic:
        return []
    want = n * 2  # room for what the length filter drops
    if cc:
        url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": topic, "sp": CC_FILTER})
    else:
        url = f"ytsearch{want}:{topic}"
    info = _extract(url, flat=True, limit=want)
    out = []
    for e in info.get("entries") or []:
        vid, dur = e.get("id"), e.get("duration")
        if not vid or not VIDEO_ID.match(vid) or not dur or e.get("live_status") in ("is_live", "is_upcoming"):
            continue
        if not min_minutes * 60 <= dur <= max_minutes * 60:
            continue
        out.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": e.get("title", ""),
                    "channel": e.get("channel") or e.get("uploader") or "", "duration": dur,
                    "views": e.get("view_count"), "description": (e.get("description") or "")[:200],
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", "cc_search": cc})
        if len(out) == n:
            break
    return out


# ------------------------------------------------------------------ topic scoring from captions

def terms(topic: str) -> list[re.Pattern]:
    """One pattern per topic word: whole word, or its stem for long words (negotiation → negotia…)."""
    words = [w for w in re.findall(r"\w+", to_latin(topic).lower()) if w not in STOP and len(w) > 1]
    pats = []
    for w in dict.fromkeys(words):
        stem = w[: max(5, len(w) - 3)] if len(w) > 6 else w
        pats.append(re.compile(rf"\b{re.escape(stem)}\w*" if stem != w else rf"\b{re.escape(w)}\b", re.I))
    return pats


def skeleton(word: str) -> str:
    """Consonants only, a few spellings merged: English words written in Devanagari come back from to_latin as
    "startaap" / "phandarejing" — same skeleton start as "startup" / "fundraising"."""
    w = word.lower().replace("ph", "f").replace("ck", "k").replace("q", "k").replace("w", "v").replace("c", "k")
    w = re.sub(r"[aeiouy\W\d_]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def _skel_terms(topic: str) -> list[str]:
    return [sk[:4] for w in re.findall(r"\w+", to_latin(topic).lower()) if w not in STOP and (sk := skeleton(w))
            and len(sk) >= 3]


def caption_lines(info: dict) -> list[tuple[float, str]]:
    """[(second, text)] from the video's own-language captions (manual first, else automatic), via json3."""
    lang = info.get("language") or ""
    subs, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    tracks = [subs.get(lang), subs.get(lang.split("-")[0]),
              next((v for k, v in auto.items() if k.endswith("-orig")), None),
              auto.get(lang), auto.get(lang.split("-")[0]), subs.get("en"), auto.get("en")]
    fmts = next((t for t in tracks if t), None)
    j = next((f for f in fmts or [] if f.get("ext") == "json3" and f.get("url")), None)
    if not j:
        return []
    data = json.loads(_fetch(j["url"]))
    lines = []
    for ev in data.get("events") or []:
        text = "".join(s.get("utf8", "") for s in ev.get("segs") or []).replace("\n", " ").strip()
        if text:
            lines.append((ev.get("tStartMs", 0) / 1000, text))
    return lines


@functools.lru_cache(maxsize=64)
def _video(vid: str) -> tuple[dict, tuple]:
    info = _extract(f"https://www.youtube.com/watch?v={vid}")
    meta = {k: info.get(k) for k in ("title", "channel", "duration", "view_count", "like_count", "upload_date",
                                     "language", "license")}
    return meta, tuple(caption_lines(info))


def score_lines(lines, topic: str, duration: float | None) -> dict:
    """Hits per line → how much of the video is about the topic, and its densest moments."""
    pats, skels = terms(topic), _skel_terms(topic)
    phrase = " ".join(re.findall(r"\w+", to_latin(topic).lower()))
    if not pats or not lines:
        return {"score": None, "hits": 0, "moments": [], "coverage": 0.0}
    duration = duration or (lines[-1][0] + 5)
    per_min: dict[int, int] = {}
    first: dict[int, tuple[float, str]] = {}
    hits = 0
    for t, text in lines:
        hindi = bool(DEVANAGARI.search(text))
        latin = to_latin(text) if hindi else text
        low = latin.lower()
        n = sum(len(p.findall(low)) for p in pats) + (2 if len(pats) > 1 and phrase in low else 0)
        if hindi:  # loanwords in Devanagari: consonant skeletons catch what spellings miss
            n = max(n, sum(skeleton(w)[:4] == sk for w in re.findall(r"\w+", low) for sk in skels))
        if n:
            m = int(t // 60)
            hits += n
            per_min[m] = per_min.get(m, 0) + n
            first.setdefault(m, (t, latin))
    minutes = max(1, math.ceil(duration / 60))
    coverage = len(per_min) / minutes
    density = hits / (duration / 600)  # hits per 10 minutes
    peak = max(per_min.values(), default=0)
    # tuned on real searches: looser limits put most relevant videos at 100 and stopped ranking them
    score = round(100 * (0.4 * min(1, density / 15) + 0.4 * min(1, peak / 10) + 0.2 * min(1, coverage / 0.7)))
    moments = []
    for m in sorted(per_min, key=lambda k: -per_min[k]):
        t, text = first[m]
        if per_min[m] >= 2 and all(abs(t - x["t"]) >= MOMENT_GAP for x in moments):
            moments.append({"t": round(t), "hits": per_min[m], "text": text[:120]})
        if len(moments) == 3:
            break
    return {"score": score, "hits": hits, "moments": sorted(moments, key=lambda x: x["t"]),
            "coverage": round(coverage, 2)}


def score(vid: str, topic: str) -> dict:
    """How well one video covers `topic`, from its captions. score None = no captions to judge by."""
    if not VIDEO_ID.match(vid or ""):
        raise ValueError("not a YouTube video id")
    meta, lines = _video(vid)
    out = score_lines(lines, topic, meta.get("duration"))
    up = str(meta.get("upload_date") or "")
    lic = meta.get("license") or ""
    return {"id": vid, **out, "has_captions": bool(lines), "language": meta.get("language") or "",
            "license": lic, "cc": "creative commons" in lic.lower(), "likes": meta.get("like_count"),
            "views": meta.get("view_count"), "upload_date": f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else ""}
