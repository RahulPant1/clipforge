"""Manage local MP4 cutaways and suggest matches from a local folder."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import BinaryIO

from ..config import settings
from ..files import atomic_output
from ..models import BrollPlacement

MAX_BYTES = 250 * 2**20
_STOP = set("a an and are for from how into that the their then this they was were what when where which will with "
            "would you your have just like about some than there short clip video footage broll b roll".split())


def path(directory: Path, index: int, asset: str | None = None) -> Path:
    suffix = f"_{asset}" if asset else ""
    return directory / f"broll_{index}{suffix}.mp4"


def next_slot(placements: list[BrollPlacement], duration: float) -> tuple[float, float]:
    if len(placements) >= 12:
        raise ValueError("This short already has 12 B-roll placements")
    bounds = [(0.0, 0.0), *((item.start, item.end) for item in placements), (duration, duration)]
    gaps = [(left[1], right[0]) for left, right in zip(bounds, bounds[1:], strict=False)]
    for start, end in reversed(gaps):
        end = min(end, duration)
        if end - start >= 0.2:
            return start, min(end, start + 4)
    raise ValueError("There is no free space for another cutaway. Shorten or move an existing placement.")


def library_root() -> Path:
    return (settings.broll_dir or settings.data_dir / "broll").resolve()


def library_assets() -> dict[str, tuple[Path, Path]]:
    root = library_root()
    assets = {}
    for candidate in root.rglob("*"):
        if candidate.suffix.lower() != ".mp4":
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file() or resolved.stat().st_size > MAX_BYTES:
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        relative = candidate.relative_to(root)
        ident = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:24]
        assets[ident] = (resolved, relative)
    return assets


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[^\W_]+", text.casefold())
            if len(term) >= 3 and not term.isdecimal() and term not in _STOP}


def suggest(primary: str, context: str) -> list[dict]:
    """Match file and folder names against prominent clip terms and nearby speech/scene descriptions."""
    keywords, related = _terms(primary), _terms(context)
    ranked = []
    for ident, (_, name) in library_assets().items():
        terms = _terms(str(name.with_suffix("")))
        strong = terms & keywords
        weak = (terms & related) - strong
        score = 3 * len(strong) + len(weak)
        if score:
            ranked.append((score, {"id": ident, "name": name.as_posix(), "matches": sorted(strong | weak)}))
    ranked.sort(key=lambda item: (-item[0], item[1]["name"].casefold(), item[1]["id"]))
    return [item for _, item in ranked[:6]]


def save(directory: Path, index: int, stream: BinaryIO, asset: str | None = None) -> Path:
    destination = path(directory, index, asset)
    with atomic_output(destination) as staged:
        size = 0
        with staged.open("wb") as output:
            while chunk := stream.read(2**20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("B-roll files must be 250 MiB or smaller")
                output.write(chunk)
        try:
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
                "-select_streams", "V:0", "-show_entries",
                "stream=codec_type:format=format_name", "-of", "json", str(staged),
            ], capture_output=True, text=True, timeout=20, check=True)
            metadata = json.loads(probe.stdout)
        except (subprocess.SubprocessError, ValueError) as e:
            raise ValueError("Select a readable MP4 video") from e
        formats = metadata.get("format", {}).get("format_name", "").split(",")
        if "mp4" not in formats or not metadata.get("streams"):
            raise ValueError("Select an MP4 with a video track")
    return destination
