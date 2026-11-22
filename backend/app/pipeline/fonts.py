"""Make CAPTION_FONT actually available to libass instead of silently substituting it.

libass finds fonts through fontconfig. On WSL the Windows fonts (Arial, Segoe, …) live in /mnt/c/Windows/Fonts,
which fontconfig doesn't index — so the default CAPTION_FONT=Arial quietly rendered as DejaVu Sans. Pointing
libass at that folder works but scans ~500 fonts over the slow /mnt/c mount on EVERY render (~10 s). Instead we
look the family up once, copy just its files into data/fonts/ and hand libass that small folder (`fontsdir=`).
"""
import functools
import logging
import re
import shutil
import subprocess
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

WINDOWS_FONTS = Path("/mnt/c/Windows/Fonts")
SCAN_CACHE = "windows_fonts.tsv"  # family<TAB>file, so the 14 s scan of the Windows folder happens once

# libass and Pillow draw colour emoji as empty boxes ("tofu") with normal fonts — strip them from anything we
# burn into a video or image (hook card, captions, carousels). Titles and post copy keep theirs.
EMOJI = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")


def strip_emoji(text: str) -> str:
    return re.sub(r"[ \t]+", " ", EMOJI.sub("", text or "")).strip()


def fonts_dir() -> Path:
    return (settings.data_dir / "fonts").resolve()


def _fc(*args: str) -> str:
    try:
        res = subprocess.run(["fc-" + args[0], *args[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


def _families_in(folder: Path) -> list[tuple[str, str]]:
    """[(family, file)] for every face in folder (all of a face's family names, e.g. localized ones)."""
    if not folder.is_dir():
        return []
    out = _fc("scan", "--format", "%{family}\t%{file}\n", str(folder))
    pairs = []
    for line in out.splitlines():
        fams, _, file = line.rpartition("\t")
        pairs += [(f.strip(), file) for f in fams.split(",") if f.strip()]
    return pairs


def _has(pairs: list[tuple[str, str]], family: str) -> list[str]:
    want = family.casefold()
    return sorted({file for fam, file in pairs if fam.casefold() == want})


def _windows_index(cache_dir: Path, windows: Path) -> list[tuple[str, str]]:
    cache = cache_dir / SCAN_CACHE
    if cache.exists():
        return [tuple(line.split("\t", 1)) for line in cache.read_text().splitlines() if "\t" in line]
    if not windows.is_dir():
        return []
    log.info("indexing Windows fonts once (takes ~15 s on WSL)…")
    pairs = _families_in(windows)
    if pairs:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text("".join(f"{fam}\t{file}\n" for fam, file in pairs))
    return pairs


def caption_fonts_dir(family: str) -> Path | None:
    """Folder to pass to libass as fontsdir= so `family` renders for real; None if fontconfig already has it
    (or it can't be found anywhere — then libass substitutes and we warn once)."""
    return _resolve(family, fonts_dir(), WINDOWS_FONTS)


@functools.lru_cache(maxsize=16)  # keyed on the folders too, so tests (tmp data dirs) never see a stale answer
def _resolve(family: str, local: Path, windows: Path) -> Path | None:
    if _has(_families_in(local), family):
        return local
    matched = _fc("match", "-f", "%{family}", family)
    if family.casefold() in (f.strip().casefold() for f in matched.split(",")):
        return None  # installed system-wide
    files = _has(_windows_index(local, windows), family)
    if files:
        local.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, local / Path(f).name)
        log.info("caption font %r copied from Windows fonts into %s", family, local)
        return local
    log.warning("caption font %r is not installed — libass will substitute %r. Install it, put its .ttf in %s, "
                "or set CAPTION_FONT to an installed font.", family, matched.split(",")[0] or "a default", local)
    return None


def font_available(family: str) -> bool:
    """True if libass can render `family` for real: in data/fonts, installed system-wide, or copyable from Windows."""
    return _available(family, fonts_dir(), WINDOWS_FONTS)


@functools.lru_cache(maxsize=32)
def _available(family: str, local: Path, windows: Path) -> bool:
    if _has(_families_in(local), family):
        return True
    matched = _fc("match", "-f", "%{family}", family)
    if family.casefold() in (f.strip().casefold() for f in matched.split(",")):
        return True
    return bool(_has(_windows_index(local, windows), family))


def fontsdir_option(family: str) -> str:
    """`:fontsdir='…'` suffix for ffmpeg's ass/subtitles filters, or '' when not needed."""
    d = caption_fonts_dir(family)
    if not d:
        return ""
    path = str(d).replace("\\", "/").replace("'", r"'\''").replace(":", r"\:")
    return f":fontsdir='{path}'"


BOLD = re.compile(r"(bold|black|heavy|bd\.|bd$|-b\.)", re.I)


def font_file(family: str) -> str | None:
    """A font FILE for `family` (Pillow needs a path, not a family name), bold face preferred; None if not found.

    Same places as for libass: data/fonts, then fontconfig, then the Windows fonts (copied into data/fonts once).
    """
    files = _has(_families_in(fonts_dir()), family)
    if not files:
        caption_fonts_dir(family)  # copies it from Windows into data/fonts when that's where it lives
        files = _has(_families_in(fonts_dir()), family)
    if not files:
        matched = _fc("match", "-f", "%{family}\t%{file}", f"{family}:weight=bold")
        fams, _, file = matched.partition("\t")
        if family.casefold() in (f.strip().casefold() for f in fams.split(",")) and file:
            files = [file]
    if not files:
        return None
    return next((f for f in files if BOLD.search(Path(f).name)), files[0])


# ------------------------------------------------------------------ scripts the caption fonts don't cover

# Caption/hook/cover fonts (Arial, Montserrat, Impact …) have no glyphs for Indic scripts: Hindi captions burned in
# as rows of empty boxes. Text in one of these scripts switches to a font that has them — Nirmala UI ships with
# Windows 10/11 and covers every Indic script (and Latin); Noto is the Linux package (`apt install fonts-noto-core`).
INDIC_FONT = "Nirmala UI"
SCRIPTS = {  # script -> (unicode ranges, fonts after INDIC_FONT, first found wins)
    "Devanagari": ("\u0900-\u097F\uA8E0-\uA8FF", ["Noto Sans Devanagari", "Mangal", "Lohit Devanagari"]),
    "Bengali": ("\u0980-\u09FF", ["Noto Sans Bengali", "Vrinda"]),
    "Gurmukhi": ("\u0A00-\u0A7F", ["Noto Sans Gurmukhi", "Raavi"]),
    "Gujarati": ("\u0A80-\u0AFF", ["Noto Sans Gujarati", "Shruti"]),
    "Odia": ("\u0B00-\u0B7F", ["Noto Sans Oriya", "Kalinga"]),
    "Tamil": ("\u0B80-\u0BFF", ["Noto Sans Tamil", "Latha"]),
    "Telugu": ("\u0C00-\u0C7F", ["Noto Sans Telugu", "Gautami"]),
    "Kannada": ("\u0C80-\u0CFF", ["Noto Sans Kannada", "Tunga"]),
    "Malayalam": ("\u0D00-\u0D7F", ["Noto Sans Malayalam", "Kartika"]),
}
_SCRIPT_RE = {name: re.compile(f"[{ranges}]") for name, (ranges, _) in SCRIPTS.items()}


def script_of(text: str) -> str | None:
    """The Indic script used most in `text`, or None (Latin, incl. Hinglish written in Latin letters)."""
    counts = {name: len(rx.findall(text or "")) for name, rx in _SCRIPT_RE.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def font_for(text: str, family: str | None) -> str | None:
    """`family`, unless `text` is in a script it can't draw — then the first installed font that can."""
    script = script_of(text)
    if not script:
        return family
    choices = [INDIC_FONT, *SCRIPTS[script][1]]
    if family in choices:
        return family
    found = _script_font(script)
    return found or family


@functools.lru_cache(maxsize=16)
def _script_font(script: str) -> str | None:
    for fam in [INDIC_FONT, *SCRIPTS[script][1]]:
        if font_available(fam):
            log.info("%s text: using font %r (the caption font has no %s letters)", script, fam, script)
            return fam
    log.warning("no %s font found — %s text will show as empty boxes. On WSL Windows' Nirmala UI is used "
                "automatically; otherwise `sudo apt install fonts-noto-core`.", script, script)
    return None


@functools.lru_cache(maxsize=32)
def _face_index(file: str, family: str) -> int:
    """Index of the boldest face of `family` inside a .ttc collection (0 for plain .ttf/.otf)."""
    if not file.lower().endswith((".ttc", ".otc")):
        return 0
    best, best_w = 0, -1
    for line in _fc("scan", "--format", "%{index}\t%{family}\t%{weight}\n", file).splitlines():
        idx, fams, weight = (line.split("\t") + ["", "", ""])[:3]
        try:
            w = float(weight.split()[0]) if weight else 0
        except ValueError:
            w = 0
        if family.casefold() in (f.strip().casefold() for f in fams.split(",")) and w > best_w:
            best, best_w = int(idx), w
    return best


def pil_font(family: str, size: int):
    """Pillow font for `family` (bold face, the right face inside .ttc collections); None if not found."""
    from PIL import ImageFont

    file = font_file(family)
    if not file:
        return None
    try:
        return ImageFont.truetype(file, size, index=_face_index(file, family))
    except OSError:
        return None
