"""Brand kit: your logo in a corner of every short, an end card after the last word, one brand colour.

Stored once for all jobs: data/brand_kit.json + data/brand/logo.png (any uploaded image → RGBA PNG, ≤ 1024 px).
render.py asks this module for the pieces:
  watermark(kit, opts)          → logo path or None (per-clip options.watermark=False turns it off)
  end_card_seconds(kit, opts, d) → how long the end card can be: never past max_clip_seconds (59 s), < 1 s → 0
  end_card_png(kit, path)       → transparent 1080x1920 PNG: logo, end_text, handle (in the accent)
  overlay_graph(...)            → the filter_complex tail that puts them on the rendered frames
Covers, quote cards and carousels take accent() / handle(), so one colour runs through everything you post.
`rev` goes up when something that is burned in changes; shorts remember the rev they were made with.
"""
import logging
from pathlib import Path

from ..config import settings
from ..models import BrandKit, RenderOptions

log = logging.getLogger(__name__)

W, H = 1080, 1920
MARGIN_X, TOP_Y = 48, 120  # clears the Shorts / Reels top bar
LOGO_MAX = 1024
BURNED = ("enabled", "logo", "position", "size", "opacity", "end_card", "end_seconds", "end_text", "handle",
          "accent", "accent_captions")


def _path() -> Path:
    return settings.data_dir / "brand_kit.json"


def logo_path() -> Path:
    return settings.data_dir / "brand" / "logo.png"


def load() -> BrandKit:
    p = _path()
    if not p.exists():
        return BrandKit(enabled=False)  # nothing set up yet: shorts look exactly as before
    try:
        kit = BrandKit.model_validate_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # a broken file must not stop renders
        log.warning("ignoring %s: %s", p, e)
        return BrandKit(enabled=False)
    if kit.logo and not logo_path().is_file():
        kit.logo = False
    return kit


def save(kit: BrandKit) -> BrandKit:
    """Saves the kit; bumps rev when anything burned into shorts changed."""
    old = load() if _path().exists() else None
    kit = kit.model_copy(update={"handle": kit.handle.strip(), "end_text": kit.end_text.strip(),
                                 "logo": logo_path().is_file() and kit.logo,
                                 "rev": old.rev if old else 0})
    if old is None or any(getattr(old, f) != getattr(kit, f) for f in BURNED):
        kit.rev += 1
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(kit.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    return kit


def save_logo(data: bytes) -> BrandKit:
    """Any image Pillow reads → RGBA PNG (keeps transparency), longest side ≤ LOGO_MAX. ValueError if not an image."""
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("not an image Pillow can read (use PNG, JPG or WebP)") from e
    img = img.convert("RGBA")
    img.thumbnail((LOGO_MAX, LOGO_MAX))
    box = img.getchannel("A").getbbox()  # trim transparent margins so the size slider means the visible logo
    if box:
        img = img.crop(box)
    logo_path().parent.mkdir(parents=True, exist_ok=True)
    img.save(logo_path())
    return save(load().model_copy(update={"logo": True, "enabled": True}))


def remove_logo() -> BrandKit:
    logo_path().unlink(missing_ok=True)
    return save(load().model_copy(update={"logo": False}))


def accent(kit: BrandKit | None = None) -> str:
    kit = kit or load()
    return kit.accent if kit.enabled and kit.accent else settings.carousel_accent


def pop_colour(kit: BrandKit | None = None) -> tuple[int, int, int]:
    """Highlight colour for cover text: the brand colour when the kit sets one, else the classic yellow."""
    kit = kit or load()
    if kit.enabled and kit.accent:
        c = kit.accent.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return 255, 214, 10


def handle(kit: BrandKit | None = None) -> str:
    kit = kit or load()
    return kit.handle if kit.enabled and kit.handle else settings.brand_handle


def ass_colour(hex_rgb: str) -> str:
    """#RRGGBB → ASS &H00BBGGRR (BGR order!)."""
    c = hex_rgb.lstrip("#")
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def in_use(kit: BrandKit) -> bool:
    return kit.enabled and (kit.logo or kit.end_card or bool(kit.accent))


# ------------------------------------------------------------------ render pieces

def watermark(kit: BrandKit, opts: RenderOptions) -> Path | None:
    if not (kit.enabled and kit.logo) or opts.watermark is False:
        return None
    p = logo_path()
    return p if p.is_file() else None


def end_card_seconds(kit: BrandKit, opts: RenderOptions, duration: float) -> float:
    on = kit.end_card if opts.end_card is None else opts.end_card
    if not (kit.enabled and on) or not (kit.logo or kit.end_text or handle(kit)):
        return 0.0
    room = settings.max_clip_seconds - duration
    sec = round(min(kit.end_seconds, room), 2)
    return sec if sec >= 1.0 else 0.0  # a short that's already ~59 s gets no end card (Shorts length rules)


def logo_width(kit: BrandKit) -> int:
    return max(32, round(W * kit.size / 100)) // 2 * 2


def end_card_png(kit: BrandKit, out: Path) -> Path:
    """Transparent overlay for the end card: logo centred, end text below it, handle in the brand colour."""
    from PIL import Image, ImageDraw

    from .carousel import _font, _hex, wrap
    from .fonts import font_for, strip_emoji

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = 640
    if kit.logo and logo_path().is_file():
        logo = Image.open(logo_path()).convert("RGBA")
        logo.thumbnail((440, 440))
        img.alpha_composite(logo, ((W - logo.width) // 2, y - logo.height // 2))
        y += logo.height // 2 + 80
    else:
        y = 820
    text = strip_emoji(kit.end_text)
    if text:
        f = _font(84, font_for(text, None))
        for line in wrap(d, text, f, W - 180):
            d.text((W // 2, y), line, font=f, fill=(255, 255, 255, 255), anchor="mt", stroke_width=3,
                   stroke_fill=(0, 0, 0, 160))
            y += 104
        y += 20
    h = strip_emoji(handle(kit))
    if h:
        d.text((W // 2, y), h, font=_font(58), fill=(*_hex(accent(kit)), 255), anchor="mt", stroke_width=2,
               stroke_fill=(0, 0, 0, 160))
    img.save(out)
    return out


def overlay_graph(base: str, *, logo_input: int | None, kit: BrandKit, card_input: int | None, start: float,
                  end: float, card_from: float) -> str:
    """filter_complex tail: [base] → logo in its corner from `start` until `end` → end card from `card_from` → [v].

    start = after the hook card when the logo sits at the top (they'd overlap); end = where the end card begins.
    """
    parts, cur = [], base
    if logo_input is not None:
        x = MARGIN_X if kit.position == "top-left" else f"W-w-{MARGIN_X}"
        parts.append(f"[{logo_input}:v]scale={logo_width(kit)}:-1,format=rgba,"
                     f"colorchannelmixer=aa={kit.opacity:.2f}[lg]")
        parts.append(f"[{cur}][lg]overlay={x}:{TOP_Y}:enable='between(t,{start:.3f},{end:.3f})'[wm]")
        cur = "wm"
    if card_input is not None:
        parts.append(f"[{card_input}:v]format=rgba[ec]")
        parts.append(f"[{cur}][ec]overlay=0:0:enable='gte(t,{card_from:.3f})'[ecv]")
        cur = "ecv"
    parts.append(f"[{cur}]null[v]")
    return ";".join(parts)


def freeze_tail(duration: float, seconds: float) -> list[str]:
    """-vf pieces (output time, after scale/pad): hold the last frame `seconds` longer, blurred and darkened."""
    t = f"gte(t,{duration:.3f})"
    return [f"tpad=stop_mode=clone:stop_duration={seconds:.3f}", f"boxblur=20:2:enable='{t}'",
            f"eq=brightness=-0.3:saturation=0.6:enable='{t}'"]
