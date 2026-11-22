"""Quote cards: a line actually said in the clip, set big next to the speaker cutout — square (1:1) for X,
portrait (4:5) for LinkedIn and Instagram. short_N.quote.png / short_N.quote45.png.

The quote is picked from the clip's own words (never written by a model, so it can't be made up): the sentence that
carries the clip's keywords / hook, 6–30 words. You can replace it with any line you like (QuoteEdit). The card
credits the source video, not a person — in a podcast the channel isn't necessarily who said it.
"""
import logging
import re
from pathlib import Path

from ..models import Clip, Segment
from . import brand
from .fonts import font_for, pil_font, strip_emoji
from .highlights import _ends_sentence
from .jumpcut import clean_removed, clip_words, is_struck, norm

log = logging.getLogger(__name__)

SIZES = {"quote.png": (1080, 1080), "quote45.png": (1080, 1350)}  # X · LinkedIn / Instagram
PAD = 80
MIN_WORDS, MAX_WORDS = 6, 24  # a card is read in a glance; real picks at 27+ words filled the card


def sentences(segs: list[Segment], clip: Clip) -> list[str]:
    removed = clean_removed(clip.removed)
    words = [w for w in clip_words(segs, clip.start, clip.end) if not is_struck(w, removed)]
    out, cur = [], []
    for w in words:
        cur.append(w.word)
        if _ends_sentence(w.word):
            out.append("".join(cur).strip())
            cur = []
    if cur:
        out.append("".join(cur).strip())
    return [s for s in out if s]


def pick_quote(segs: list[Segment], clip: Clip) -> str:
    """The most quotable sentence: keywords + hook words, a number, ~15 words, not starting mid-thought."""
    keys = {norm(k) for k in clip.keywords} | {norm(w) for w in clip.hook.split() if len(norm(w)) > 3}
    best, best_score = "", None
    for s in sentences(segs, clip):
        words = s.split()
        if not MIN_WORDS <= len(words) <= MAX_WORDS:
            continue
        score = 3 * sum(norm(w) in keys for w in words) + (2 if re.search(r"\d", s) else 0)
        score -= 0.15 * abs(len(words) - 15)
        if norm(words[0]) in {"and", "so", "but", "because", "or", "then"}:
            score -= 2
        if best_score is None or score > best_score:
            best, best_score = s, score
    if not best:  # no clean sentence of the right length: the start of what's said
        words = " ".join(sentences(segs, clip)).split()
        best = " ".join(words[:MAX_WORDS - 6]) + ("…" if len(words) > MAX_WORDS - 6 else "")
    return tidy(best)


def tidy(text: str) -> str:
    text = " ".join(strip_emoji(text).split()).strip(" -–—\"“”'")
    return text[:1].upper() + text[1:] if text else ""


def in_clip(quote: str, segs: list[Segment], clip: Clip) -> bool:
    """Still something said in this clip (after a trim the old quote may have fallen outside it)?"""
    said = " ".join(norm(w) for s in sentences(segs, clip) for w in s.split())
    return " ".join(norm(w) for w in quote.split()) in said


def _fit(draw, text: str, family: str | None, max_w: int, max_h: int):
    from .carousel import _font, wrap

    for size in range(84, 39, -4):
        font = (pil_font(family, size) if family else None) or _font(size)
        lines = wrap(draw, text, font, max_w)
        lh = int(size * 1.22)
        if len(lines) * lh <= max_h and all(draw.textlength(ln, font=font) <= max_w for ln in lines):
            return font, lines, lh
    return font, lines, lh


def draw_card(text: str, size: tuple[int, int], out: Path, person=None, source: str = "",
              font_family: str | None = None) -> None:
    """Gradient card in the brand accent; big quote mark, the quote, a source line; the speaker cutout
    bottom-right when there is one (text keeps to the left column so it never sits on a face)."""
    from PIL import Image, ImageDraw

    from .carousel import _font
    from .cover import _hex
    from .cutout import outlined

    w, h = size
    accent = _hex(brand.accent())
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    top, bottom = (14, 15, 19), tuple(int(14 + (a - 14) * 0.35) for a in accent)
    for y in range(h):
        f = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))
    img = img.convert("RGBA")

    col_w = w - 2 * PAD
    if person is not None:
        canvas, pad = outlined(person, int(h * 0.72), int(w * 0.5), stroke=8, glow=accent)
        x = w - canvas.width + pad + 20
        y = h - canvas.height + pad if person.info.get("touches_bottom") else h - canvas.height - 20
        img.alpha_composite(canvas, (x, y))
        col_w = min(int(w * 0.56), x + pad) - PAD - 24  # text stops where the speaker starts

    d = ImageDraw.Draw(img)
    family = font_for(text, font_family)
    foot_h = 90
    t_top = PAD + 110
    room = h - t_top - PAD - foot_h
    font, lines, lh = _fit(d, text, family, col_w, room)
    if person is None:  # nothing beside it: centre the quote instead of leaving the bottom empty
        t_top += max(0, (room - lh * len(lines)) // 2)
    d.text((PAD - 8, t_top - 180), "“", font=_font(220), fill=(*accent, 255))
    for i, line in enumerate(lines):
        d.text((PAD, t_top + i * lh), line, font=font, fill=(244, 245, 248, 255))
    if source:
        foot = (pil_font(family, 30) if family else None) or _font(30)
        src = source
        if d.textlength(src, font=foot) > col_w:
            while src and d.textlength(src + "…", font=foot) > col_w:
                src = src[:-1]
            src = src.rstrip() + "…"
        d.text((PAD, h - PAD), src, font=foot, fill=(170, 174, 188, 255), anchor="ls")
    if brand.handle() and person is None:
        d.text((w - PAD, h - PAD), brand.handle(), font=_font(30), fill=(*accent, 255), anchor="rs")
    img.convert("RGB").save(out, "PNG", optimize=True)


def make_cards(video: Path, clip: Clip, job_dir: Path, segs: list[Segment], meta: dict | None = None,
               font_family: str | None = None, detector=None) -> list[str]:
    """Writes both quote cards for the clip; fills clip.quote (the line used), quote_cards, quote_version.
    No speech (no line to quote) → no cards."""
    from .cover import best_shot
    from .cutout import cutout

    keep = clip.quote and (clip.quote_custom or in_clip(clip.quote, segs, clip))
    quote = clip.quote if keep else pick_quote(segs, clip)
    if not quote:
        clip.quote_cards = []
        return []
    person = None
    try:
        frame, t, x = best_shot(video, clip, detector, need_face=True)
        person = cutout(frame, x, key=(str(video), t))  # same frame as the cover: cut once, reused
    except Exception as e:
        log.warning("quote card for clip %s without the speaker (%s)", clip.index, e)
    meta = meta or {}
    title, channel = meta.get("title", ""), meta.get("channel", "")
    source = " · ".join(x for x in [f"From “{title}”" if title else "", channel] if x)
    names = []
    for suffix, size in SIZES.items():
        name = f"short_{clip.index}.{suffix}"
        draw_card(quote, size, job_dir / name, person, source, font_family)
        names.append(name)
    clip.quote, clip.quote_cards = quote, names
    clip.quote_version += 1
    return names
