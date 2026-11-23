"""Colour emoji on shorts — libass draws emoji monochrome (or as boxes), so they go on as PNG overlays instead.

Emoji images come from Noto Color Emoji (Google, SIL Open Font License 1.1): one 11 MB font, downloaded once to
data/emoji/ (or the system's fonts-noto-color-emoji), drawn to PNG with Pillow per emoji and cached
(data/emoji/png/<codepoints>-<px>.png). No font → no emoji, the short renders exactly as before.

Where they go (render.py asks):
  caption_events(chunks, keywords, …) → one emoji above a caption line, for as long as that line shows: an emoji
                                       you typed into the transcript, else a word from MAP / data/emoji_map.json
                                       (Gemma keywords first); ≥ SPACING s apart, ≤ MAX per short
  hook_emoji(hook)                    → the first emoji in the hook (Gemma writes them; the card itself can't show
                                       them), as a sticker above the hook card
  layout()/graph()                    → canvas positions (estimated from the caption font's measured width, same
                                       rules as libass: bottom/middle, margins, wrap) → ffmpeg overlays
"""
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Word
from .jumpcut import norm

log = logging.getLogger(__name__)

FONT_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/fonts/NotoColorEmoji.ttf"
LICENSE_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/fonts/LICENSE"
SYSTEM_FONTS = [Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
                Path("/usr/share/fonts/noto/NotoColorEmoji.ttf")]
BITMAP_SIZE = 109  # the only size the CBDT bitmaps come in; scaled after
SPACING = 2.5  # s between caption emoji
MAX = 8  # caption emoji per short
SIZE = 1.45  # emoji height × caption font size
GAP = 14  # px between the emoji and the text under/next to it

# one emoji as people see it: a flag (2 regional letters), a keycap, or a base with modifiers/ZWJ joins
_BASE = "[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿〰〽㊗㊙]"
_MOD = "(?:️|[\U0001F3FB-\U0001F3FF]|[\U000E0020-\U000E007F])*"
CLUSTER = re.compile(f"[\U0001F1E6-\U0001F1FF]{{2}}|[0-9#*]️?⃣|{_BASE}{_MOD}(?:‍{_BASE}{_MOD})*")

# word (normalised, see _stems) → emoji. English + common Hinglish. Yours in data/emoji_map.json win.
MAP: dict[str, str] = {  # no everyday words ("no", "time", "right"): they'd fire on every line
    # money / work
    "money": "💰", "cash": "💵", "rich": "🤑", "salary": "💸", "paid": "💸", "price": "🏷️",
    "invest": "📈", "investment": "📈", "investing": "📈", "stock": "📈", "market": "📊", "profit": "📈",
    "loss": "📉", "debt": "💳", "loan": "🏦", "bank": "🏦", "saving": "🐷", "budget": "🧾",
    "business": "💼", "job": "💼", "career": "💼", "office": "🏢", "boss": "👔", "startup": "🚀",
    "sell": "🛒", "buy": "🛒", "deal": "🤝", "growth": "📈", "grow": "🌱", "goal": "🎯", "target": "🎯",
    "paisa": "💰", "paise": "💰", "rupee": "💰", "rupaye": "💰", "lakh": "💰", "crore": "💰", "kaam": "💼",
    "naukri": "💼", "dhanda": "💼",
    # mind / learning
    "brain": "🧠", "idea": "💡", "learn": "📚", "study": "📚", "book": "📖",
    "school": "🏫", "college": "🎓", "exam": "📝", "question": "❓", "secret": "🤫", "truth": "💯",
    "focus": "🎯", "habit": "🔁", "mistake": "⚠️", "problem": "⚠️", "warning": "⚠️", "danger": "⚠️",
    "smart": "🧠", "dimag": "🧠", "soch": "🤔", "padhai": "📚", "sach": "💯",
    # time
    "morning": "🌅", "night": "🌙", "sleep": "😴", "tomorrow": "📅",
    "minute": "⏱️", "fast": "⚡", "quick": "⚡", "slow": "🐢", "late": "⏰",
    "subah": "🌅", "raat": "🌙", "neend": "😴", "waqt": "⏰", "jaldi": "⚡",
    # feelings
    "love": "❤️", "heart": "❤️", "happy": "😊", "sad": "😢", "angry": "😡", "fear": "😨", "scared": "😨",
    "stress": "😫", "anxiety": "😰", "crazy": "🤯", "shock": "😱", "shocking": "😱", "funny": "😂",
    "laugh": "😂", "cry": "😭", "wow": "🤩", "amazing": "🤩", "beautiful": "😍", "confidence": "😎",
    "pyaar": "❤️", "pyar": "❤️", "dil": "❤️", "khush": "😊", "khushi": "😊", "dukh": "😢", "gussa": "😡",
    "darr": "😨",
    # body / health
    "health": "💪", "gym": "🏋️", "workout": "🏋️", "exercise": "🏃", "running": "🏃",
    "walk": "🚶", "strong": "💪", "strength": "💪", "muscle": "💪", "food": "🍽️",
    "diet": "🥗", "water": "💧", "coffee": "☕", "tea": "🍵", "chai": "🍵", "doctor": "🩺", "medicine": "💊",
    "sugar": "🍬", "weight": "⚖️", "khana": "🍛", "paani": "💧", "sehat": "💪",
    # people / life
    "family": "👨‍👩‍👧", "friend": "🤝", "friends": "🤝", "team": "🤝", "kid": "🧒", "child": "🧒", "baby": "👶",
    "mom": "👩", "mother": "👩", "dad": "👨", "father": "👨", "home": "🏠", "house": "🏠",
    "relationship": "💑", "marriage": "💍", "wedding": "💍", "dost": "🤝", "ghar": "🏠", "maa": "👩",
    "papa": "👨", "shaadi": "💍",
    # things / tech
    "phone": "📱", "mobile": "📱", "app": "📱", "computer": "💻", "laptop": "💻", "internet": "🌐",
    "ai": "🤖", "robot": "🤖", "code": "💻", "camera": "📷", "video": "🎬", "youtube": "▶️", "music": "🎵",
    "song": "🎵", "game": "🎮", "car": "🚗", "travel": "✈️", "flight": "✈️", "world": "🌍", "india": "🇮🇳",
    "fire": "🔥", "hot": "🔥", "cold": "🥶", "rain": "🌧️",
    "energy": "⚡", "lock": "🔒", "gift": "🎁", "party": "🎉", "win": "🏆", "winner": "🏆",
    "success": "🏆", "champion": "🏆",
    "rocket": "🚀", "star": "⭐", "magic": "✨", "free": "🆓", "stop": "🛑",
    "wrong": "❌", "kill": "💀", "dead": "💀", "die": "💀",
}


def emoji_dir() -> Path:
    return settings.data_dir / "emoji"


def clusters(text: str) -> list[str]:
    return CLUSTER.findall(text or "")


def _stems(word: str) -> list[str]:
    w = norm(word)
    out = [w]
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            out.append(w[: -len(suf)])
    if w.endswith("ies") and len(w) > 4:
        out.append(w[:-3] + "y")
    return out


def emoji_map() -> dict[str, str]:
    """MAP + data/emoji_map.json ({"word": "🔥", …}; yours win, "" switches a word off)."""
    m = dict(MAP)
    p = settings.data_dir / "emoji_map.json"
    if p.is_file():
        try:
            m.update({norm(k): v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if norm(k)})
        except (ValueError, AttributeError) as e:
            log.warning("emoji_map.json ignored: %s", e)
    return m


def for_word(word: str, mapping: dict[str, str] | None = None) -> str | None:
    """The emoji a word stands for: one typed into it, else the map; None if neither."""
    typed = clusters(word)
    if typed:
        return typed[0]
    mapping = emoji_map() if mapping is None else mapping
    return next((mapping[s] for s in _stems(word) if mapping.get(s)), None)


# ------------------------------------------------------------------ font + PNGs

_lock = threading.Lock()


def font_path(download: bool = True) -> Path | None:
    """Noto Color Emoji: data/emoji, else the system's, else downloaded once into data/emoji (None if it fails)."""
    local = emoji_dir() / "NotoColorEmoji.ttf"
    if local.is_file():
        return local
    for p in SYSTEM_FONTS:
        if p.is_file():
            return p
    if not download:
        return None
    with _lock:
        if local.is_file():
            return local
        try:
            _download(local)
        except Exception as e:  # offline etc. → shorts without emoji, never a failed render
            log.warning("emoji font download failed (%s): emoji are left out", e)
            return None
    return local


def _download(dest: Path) -> None:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(FONT_URL, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if tmp.stat().st_size < 1_000_000 or tmp.read_bytes()[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("not a font")
    tmp.replace(dest)
    try:
        with urllib.request.urlopen(LICENSE_URL, timeout=30) as r:
            (dest.parent / "LICENSE-NotoColorEmoji.txt").write_bytes(r.read())
    except Exception:  # the font is what matters; the licence text is a courtesy copy
        pass
    log.info("emoji font downloaded to %s", dest)


def _render(e: str, font: Path):
    """One emoji → tightly cropped RGBA image (Pillow draws the font's colour bitmaps)."""
    from PIL import Image, ImageDraw, ImageFont

    f = ImageFont.truetype(str(font), BITMAP_SIZE, layout_engine=ImageFont.Layout.RAQM)
    img = Image.new("RGBA", (BITMAP_SIZE * 3, BITMAP_SIZE * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((BITMAP_SIZE // 2, BITMAP_SIZE // 4), e, font=f, embedded_color=True)
    box = img.getbbox()
    return img.crop(box) if box else None


def png(e: str, px: int) -> Path | None:
    """data/emoji/png/<codepoints>-<px>.png, px tall; None when there's no font or nothing drawn."""
    name = "-".join(f"{ord(c):x}" for c in e if c != "️") + f"-{px}.png"
    out = emoji_dir() / "png" / name
    if out.is_file():
        return out
    font = font_path()
    if not font:
        return None
    try:
        img = _render(e, font)
    except Exception as ex:
        log.warning("emoji %r not drawn: %s", e, ex)
        return None
    if img is None:
        return None
    from PIL import Image

    img = img.resize((max(1, round(img.width * px / img.height)), px), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def ready() -> bool:
    return font_path(download=False) is not None


# ------------------------------------------------------------------ where they go

@dataclass
class Placed:
    start: float  # output seconds
    end: float
    emoji: str
    x: int  # top-left on the canvas
    y: int
    px: int  # height


def caption_events(chunks: list[list[Word]], keywords: list[str]) -> list[tuple[float, float, str, int]]:
    """(start, end, emoji, chunk index): at most one per caption line, typed ones first, then keyword words."""
    mapping = emoji_map()
    ks = {norm(p) for k in keywords for p in k.split()}
    out: list[tuple[float, float, str, int]] = []
    last = -99.0
    for i, c in enumerate(chunks):
        if len(out) >= MAX or not c:
            break
        typed = next((cl for w in c for cl in clusters(w.word)), None)
        found = typed or next((for_word(w.word, mapping) for w in sorted(c, key=lambda w: norm(w.word) not in ks)
                               if for_word(w.word, mapping)), None)
        start = c[0].start
        if not found or (not typed and start - last < SPACING):
            continue
        end = chunks[i + 1][0].start if i + 1 < len(chunks) and chunks[i + 1][0].start - c[-1].end < 0.6 else c[-1].end
        out.append((round(start, 3), round(end, 3), found, i))
        last = start
    return out


def hook_emoji(hook: str) -> str | None:
    found = clusters(hook)
    return found[0] if found else None


def text_lines(text: str, font_family: str, size: int, width: int = 960) -> int:
    """How many lines libass will wrap `text` to (measured with the same font; 1-3)."""
    from .fonts import pil_font

    f = pil_font(font_family, size)
    if not f or not text:
        return 1
    try:
        w = f.getlength(text)
    except Exception:
        return 1
    return max(1, min(3, -(-int(w * 1.05) // width)))


def graph(base: str, placed: list[Placed], first_input: int) -> tuple[str, str]:
    """filter_complex pieces overlaying each emoji PNG (input first_input + k) on [base] → (graph, out label)."""
    parts, cur = [], base
    for k, p in enumerate(placed):
        parts.append(f"[{first_input + k}:v]format=rgba[em{k}]")
        parts.append(f"[{cur}][em{k}]overlay={p.x}:{p.y}:enable='between(t,{p.start:.3f},{p.end - 0.001:.3f})'"
                     f"[emv{k}]")
        cur = f"emv{k}"
    return ";".join(parts), cur
