"""Caption builders: animated karaoke ASS (burned in) and plain SRT (export / simple mode).

All functions take words already timed on the OUTPUT timeline (after jump cuts).
"""
import re

from ..config import settings
from ..models import Word
from .jumpcut import norm

Chunk = list[Word]


def ts_srt(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def ts_ass(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def chunk_words(words: list[Word], n: int | None = None) -> list[Chunk]:
    """Groups of up to n words; a new group always starts after sentence-ending punctuation."""
    n = n or settings.caption_words
    chunks: list[Chunk] = []
    cur: Chunk = []
    for w in words:
        cur.append(w)
        if len(cur) == n or w.word.strip().endswith((".", "?", "!")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _text(w: Word) -> str:
    return re.sub(r"[{}\\]", "", w.word).strip().upper()


def build_srt(words: list[Word], n: int | None = None) -> str:
    chunks = [(c[0].start, c[-1].end, " ".join(_text(w) for w in c)) for c in chunk_words(words, n)]
    chunks = [[s, e, t] for s, e, t in chunks if t.strip()]
    for cur, nxt in zip(chunks, chunks[1:], strict=False):  # bridge short pauses -> no flicker
        if 0 < nxt[0] - cur[1] < 0.6:
            cur[1] = nxt[0]
    return "\n".join(f"{i}\n{ts_srt(s)} --> {ts_srt(e)}\n{t}\n" for i, (s, e, t) in enumerate(chunks, 1))


# ---------------------------------------------------------------- karaoke ASS

def keyword_set(keywords: list[str], words: list[Word]) -> set[str]:
    ks = {norm(p) for k in keywords for p in k.split() if norm(p)}
    # numbers / money / percentages always pop
    return ks | {norm(w.word) for w in words if re.search(r"\d|%|₹|\$", w.word)}


def _ass_header(middle: bool = False) -> str:
    """middle=True centres captions on the canvas (two-speaker stack: the seam between the panels)."""
    s = settings
    align, margin_v = (5, 0) if middle else (2, s.caption_margin_v)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s.caption_font},{s.caption_size},{s.caption_color},{s.caption_color},&H00000000,&H96000000,\
-1,0,0,0,100,100,0,0,1,7,3,{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _line(chunk: Chunk, active: int, keywords: set[str], pop: bool) -> str:
    s = settings
    parts = []
    for i, w in enumerate(chunk):
        is_active = i == active
        if is_active:
            color = s.highlight_color
        elif norm(w.word) in keywords:
            color = s.keyword_color
        else:
            color = s.caption_color
        scale = settings.highlight_scale if is_active else 100
        tag = rf"\c{color}&"
        if pop:  # line pops in: every word scales 80% -> target over 90 ms
            tag += rf"\fscx80\fscy80\t(0,90,\fscx{scale}\fscy{scale})"
        else:
            tag += rf"\fscx{scale}\fscy{scale}"
        parts.append("{" + tag + "}" + _text(w))
    return " ".join(parts)


def build_ass(words: list[Word], keywords: list[str], n: int | None = None, middle: bool = False) -> str:
    ks = keyword_set(keywords, words)
    chunks = chunk_words(words, n)
    events: list[list] = []  # [start, end, text]
    for c in chunks:
        for i, w in enumerate(c):
            start = c[0].start if i == 0 else w.start
            end = c[i + 1].start if i + 1 < len(c) else c[-1].end
            if end > start:
                events.append([start, end, _line(c, i, ks, pop=(i == 0))])
    for cur, nxt in zip(events, events[1:], strict=False):
        if 0 < nxt[0] - cur[1] < 0.6:
            cur[1] = nxt[0]
    body = "\n".join(f"Dialogue: 0,{ts_ass(s)},{ts_ass(e)},Default,,0,0,0,,{t}" for s, e, t in events)
    return _ass_header(middle) + body + "\n"


# ---------------------------------------------------------------- hook title (first seconds)

def hook_text(hook: str) -> str:
    """ASS-safe one-liner: no override braces/backslashes, collapsed whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[{}\\]", "", hook)).strip()


SEAM_HOOK_MARGIN_V = 1040  # card's bottom edge just above seam captions (centred at y=960)


def build_hook_ass(hook: str, duration: float, seam: bool = False) -> str:
    """Big title card for the first `hook_seconds` of the short (own file, so it works with either caption style).

    Top of the frame (captions live at the bottom), boxed text that pops in and fades out. Empty hook -> "".
    seam=True (two-speaker stack): the top would cover the upper speaker's face, so the card sits on that
    speaker's chest instead, bottom edge just above the captions on the seam.
    """
    s = settings
    align, margin_v = (2, SEAM_HOOK_MARGIN_V) if seam else (8, s.hook_margin_v)
    text = hook_text(hook)
    end = min(s.hook_seconds, duration)
    if not text or end <= 0.3:
        return ""
    # BorderStyle 3 = opaque box per line; its colour is OutlineColour, Outline is the padding
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, \
Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{s.caption_font},{s.hook_size},{s.hook_color},{s.hook_color},{s.hook_box_color},&H00000000,\
-1,0,0,0,100,100,0,0,3,18,0,{align},110,110,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,{ts_ass(0)},{ts_ass(end)},Hook,,0,0,0,,{{\\fad(0,250)\\fscx70\\fscy70\\t(0,150,\\fscx100\\fscy100)}}{text}
"""
