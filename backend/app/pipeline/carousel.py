"""Instagram carousel slides (1080x1350 PNG) rendered locally with Pillow."""
import re
from pathlib import Path

from ..config import settings
from . import brand
from .fonts import font_for, pil_font, strip_emoji

W, H = 1080, 1350
PAD = 96
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/mnt/c/Windows/Fonts/arialbd.ttf",  # Windows fonts visible from WSL
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _font(size: int, family: str | None = None):
    """family: a font for a script the default fonts lack (fonts.font_for: Hindi → Nirmala UI)."""
    from PIL import ImageFont

    if family and (f := pil_font(family, size)):
        return f
    for path in [settings.carousel_font, *FONT_CANDIDATES]:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", strip_emoji(text)).strip()


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _background(accent: tuple[int, int, int]):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top, bottom = (14, 15, 19), tuple(int(14 + (a - 14) * 0.28) for a in accent)
    for y in range(H):
        f = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))
    return img, d


def _block(d, lines: list[str], font, y: int, fill, gap: int) -> int:
    for ln in lines:
        d.text((PAD, y), ln, font=font, fill=fill)
        y += font.size + gap
    return y


def render_carousel(slides: list[dict], out_dir: Path, prefix: str, handle: str | None = None) -> list[str]:
    handle = brand.handle() if handle is None else handle
    accent = _hex(brand.accent())
    white, muted = (240, 241, 245), (160, 164, 178)
    files = []
    n = len(slides)
    script_font = font_for(" ".join(f"{s.get('title', '')} {s.get('text', '')}" for s in slides), None)
    font = lambda size: _font(size, script_font)  # noqa: E731
    for i, s in enumerate(slides):
        img, d = _background(accent)
        title, body = clean(s.get("title", "")), clean(s.get("text", ""))
        cover, last = i == 0, i == n - 1 and n > 1

        d.text((W - PAD, PAD - 20), f"{i + 1}/{n}", font=_font(34), fill=muted, anchor="ra")
        if cover:
            tf, bf = font(104), font(46)
            t_lines = wrap(d, title.upper(), tf, W - 2 * PAD)
            b_lines = wrap(d, body, bf, W - 2 * PAD)
            total = len(t_lines) * (tf.size + 14) + 50 + len(b_lines) * (bf.size + 14)
            y = max(PAD + 80, (H - total) // 2 - 40)
            d.rectangle([PAD, y - 50, PAD + 140, y - 36], fill=accent)
            y = _block(d, t_lines, tf, y, white, 14) + 50
            _block(d, b_lines, bf, y, muted, 14)
        else:
            tf, bf = font(84 if not last else 92), font(56)
            t_lines = wrap(d, title, tf, W - 2 * PAD)
            b_lines = wrap(d, body, bf, W - 2 * PAD)
            total = len(t_lines) * (tf.size + 12) + 48 + len(b_lines) * (bf.size + 20)
            y = max(PAD + 120, (H - total) // 2 - 30)
            # big faded slide number behind the content
            d.text((W - PAD + 10, H - PAD - 70), f"{i + 1:02}", font=font(260), anchor="rs",
                   fill=tuple(int(c * 0.3) for c in accent))
            d.rectangle([PAD, y - 40, PAD + 90, y - 28], fill=accent)
            y = _block(d, t_lines, tf, y, accent if last else white, 12) + 48
            _block(d, b_lines, bf, y, white if last else (215, 217, 225), 20)

        foot = _font(36)  # fixed Latin text ("swipe →"): Indic fonts have no arrow
        if handle:
            d.text((PAD, H - PAD), handle, font=foot, fill=muted, anchor="ls")
        if not last:
            d.text((W - PAD, H - PAD), "swipe →", font=foot, fill=accent, anchor="rs")

        name = f"{prefix}_{i + 1}.png"
        img.save(out_dir / name, optimize=True)
        files.append(name)
    return files
