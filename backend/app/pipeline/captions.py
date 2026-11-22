"""Caption builders: animated karaoke ASS (burned in) and plain SRT (export / simple mode).

All functions take words already timed on the OUTPUT timeline (after jump cuts).
"""
import re

from ..config import settings
from ..models import Word
from .fonts import strip_emoji
from .jumpcut import norm
from .presets import CaptionPreset, get_preset, placement

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


def _text(w: Word, preset: CaptionPreset | None = None) -> str:
    t = strip_emoji(re.sub(r"[{}\\]", "", w.word))
    return preset.transform(t) if preset else t.upper()


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


def _ass_header(preset: CaptionPreset, position: str | None = None, middle: bool = False,
                font: str | None = None) -> str:
    """Style from the preset; middle=True (two-speaker stack) puts captions on the seam whatever the position."""
    align, margin_v = placement("middle" if middle else position)
    p = preset
    border = 3 if p.box else 1
    back = "&H00000000" if p.box else "&H96000000"  # shadow colour (unused with a box)
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
Style: Default,{font or p.font},{p.size},{p.text},{p.text},{p.outline_color},{back},\
{-1 if p.bold else 0},0,0,0,100,100,0,0,{border},{p.outline:g},{p.shadow:g},{align},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _colour(c: str) -> str:
    """ASS override for a &HAABBGGRR colour: alpha first (\\c ignores it, and overrides carry to later words)."""
    return rf"\1a&H{c[2:4]}&\c{c}&"


def _line(chunk: Chunk, active: int, keywords: set[str], pop: bool, preset: CaptionPreset) -> str:
    p = preset
    parts = []
    for i, w in enumerate(chunk):
        is_active = i == active
        if is_active:
            color = p.active
        elif norm(w.word) in keywords:
            color = p.keyword
        else:
            color = p.text
        scale = p.active_scale if is_active else 100
        tag = (rf"\blur{p.blur:g}" if i == 0 and p.blur else "") + _colour(color)
        if pop and p.pop:  # line pops in: every word scales 80% -> target over 90 ms
            tag += rf"\fscx80\fscy80\t(0,90,\fscx{scale}\fscy{scale})"
        else:
            tag += rf"\fscx{scale}\fscy{scale}"
        parts.append("{" + tag + "}" + _text(w, p))
    return " ".join(parts)


def build_ass(words: list[Word], keywords: list[str], n: int | None = None, middle: bool = False,
              preset: CaptionPreset | None = None, position: str | None = None, animate: bool = True,
              font: str | None = None) -> str:
    """Karaoke captions (animate=True: one event per word, active word + keywords coloured) or, animate=False,
    one plain line per chunk in the preset's look (the "simple" caption style)."""
    p = preset or get_preset()
    ks = keyword_set(keywords, words)
    chunks = chunk_words(words, n or p.words)
    events: list[list] = []  # [start, end, text]
    for c in chunks:
        if not animate:
            text = " ".join(t for t in (_text(w, p) for w in c) if t)
            plain = "&H00" + p.text[4:]  # simple: one opaque colour for the whole line
            if text and c[-1].end > c[0].start:
                events.append([c[0].start, c[-1].end, "{" + (rf"\blur{p.blur:g}" if p.blur else "")
                               + _colour(plain) + "}" + text])
            continue
        for i, w in enumerate(c):
            start = c[0].start if i == 0 else w.start
            end = c[i + 1].start if i + 1 < len(c) else c[-1].end
            if end > start:
                events.append([start, end, _line(c, i, ks, pop=(i == 0), preset=p)])
    for cur, nxt in zip(events, events[1:], strict=False):
        if 0 < nxt[0] - cur[1] < 0.6:
            cur[1] = nxt[0]
    body = "\n".join(f"Dialogue: 0,{ts_ass(s)},{ts_ass(e)},Default,,0,0,0,,{t}" for s, e, t in events)
    return _ass_header(p, position, middle, font) + body + "\n"


# ---------------------------------------------------------------- hook title (first seconds)

def hook_text(hook: str) -> str:
    """ASS-safe one-liner: no override braces/backslashes, collapsed whitespace."""
    return re.sub(r"\s+", " ", strip_emoji(re.sub(r"[{}\\]", "", hook))).strip()


SEAM_HOOK_MARGIN_V = 1040  # card's bottom edge just above seam captions (centred at y=960)


def build_hook_ass(hook: str, duration: float, seam: bool = False, font: str | None = None) -> str:
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
Style: Hook,{font or s.caption_font},{s.hook_size},{s.hook_color},{s.hook_color},{s.hook_box_color},&H00000000,\
-1,0,0,0,100,100,0,0,3,18,0,{align},110,110,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,{ts_ass(0)},{ts_ass(end)},Hook,,0,0,0,,{{\\fad(0,250)\\fscx70\\fscy70\\t(0,150,\\fscx100\\fscy100)}}{text}
"""
