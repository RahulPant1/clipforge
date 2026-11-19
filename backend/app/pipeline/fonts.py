"""Make CAPTION_FONT actually available to libass instead of silently substituting it.

libass finds fonts through fontconfig. On WSL the Windows fonts (Arial, Segoe, …) live in /mnt/c/Windows/Fonts,
which fontconfig doesn't index — so the default CAPTION_FONT=Arial quietly rendered as DejaVu Sans. Pointing
libass at that folder works but scans ~500 fonts over the slow /mnt/c mount on EVERY render (~10 s). Instead we
look the family up once, copy just its files into data/fonts/ and hand libass that small folder (`fontsdir=`).
"""
import functools
import logging
import shutil
import subprocess
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

WINDOWS_FONTS = Path("/mnt/c/Windows/Fonts")
SCAN_CACHE = "windows_fonts.tsv"  # family<TAB>file, so the 14 s scan of the Windows folder happens once


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
