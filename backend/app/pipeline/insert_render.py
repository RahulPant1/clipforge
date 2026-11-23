"""Draws insert cards as transparent PNG frame sequences that render.py overlays.

A renderer gets an InsertSpec (plain JSON: the same contract a Blender renderer — `blender -b <template>.blend
--python … -- spec.json` — would get later) and writes frames 0001.png… into a folder. Frames are cached per spec:
re-rendering a short with the same card reuses them; the hash includes the renderer's name and version, so a
changed template draws again.

PillowRenderer.stat_counter: rounded dark card with a brand-accent outline; the number counts up (ease-out) over
INSERT_COUNT_SECONDS in the format it was said, the label sits under it; pops in, fades out. ~1 s for 3 s at 30 fps;
identical frames are hard links.
"""
import hashlib
import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from ..config import settings
from ..models import Insert
from .fonts import font_file, font_for, pil_font, strip_emoji
from .numbers import Num

log = logging.getLogger(__name__)

POP_SECONDS = 0.2
FADE_IN, FADE_OUT = 0.12, 0.25
DONE = ".done"


@dataclass
class InsertSpec:
    template: str
    value: float
    decimals: int
    prefix: str
    suffix: str
    grouping: str
    label: str
    duration: float  # seconds of frames to draw
    fps: int
    width: int  # canvas px (the card is drawn inside it, centred)
    height: int
    count_seconds: float
    accent: str  # "#RRGGBB"
    text_colour: str
    font: str | None  # family (fonts.py names)
    font_path: str | None  # the file, for renderers outside Pillow / fontconfig
    version: int = 1  # bump when the spec's meaning changes

    def text(self, value: float | None = None) -> str:
        return Num(0, 0, self.value, self.decimals, self.prefix, self.suffix, self.grouping).text(value)

    def key(self, renderer: "InsertRenderer") -> str:
        blob = json.dumps({"spec": asdict(self), "r": renderer.name, "v": renderer.version}, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:16]


@dataclass
class Frames:
    folder: Path
    count: int
    fps: int
    width: int
    height: int

    @property
    def pattern(self) -> Path:
        return self.folder / "%04d.png"


class InsertRenderer(Protocol):
    name: str
    version: int
    templates: set[str]

    def render(self, spec: InsertSpec, out: Path) -> int:
        """Write frames 0001.png… (RGBA, spec.width × spec.height) into `out`; return how many."""


def make_spec(ins: Insert, duration: float, size: tuple[int, int], accent: str, font: str | None) -> InsertSpec:
    label = strip_emoji(ins.label).strip()
    family = font_for(f"{ins.prefix}{label}", font)  # Hindi label → a font with Devanagari
    return InsertSpec(template=ins.template, value=ins.value, decimals=ins.decimals, prefix=ins.prefix,
                      suffix=ins.suffix, grouping=ins.grouping, label=label, duration=round(duration, 3),
                      fps=settings.output_fps, width=size[0], height=size[1],
                      count_seconds=settings.insert_count_seconds, accent=accent, text_colour="#FFFFFF",
                      font=family, font_path=font_file(family) if family else None)


def ease_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _rgb(hex_rgb: str) -> tuple[int, int, int]:
    c = hex_rgb.lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return 255, 214, 10


class PillowRenderer:
    name = "pillow"
    version = 1
    templates = {"stat_counter"}

    def render(self, spec: InsertSpec, out: Path) -> int:
        if spec.template not in self.templates:
            raise ValueError(f"pillow renderer has no template {spec.template!r}")
        return self.stat_counter(spec, out)

    @staticmethod
    def _font(family: str | None, px: int):
        from PIL import ImageFont

        return (pil_font(family, px) if family else None) or ImageFont.load_default(px)

    def _fit(self, draw, text: str, family: str | None, px: int, max_w: int, min_px: int):
        while True:
            f = self._font(family, px)
            box = draw.textbbox((0, 0), text, font=f)
            if box[2] - box[0] <= max_w or px <= min_px:
                return f, box
            px = max(min_px, int(px * 0.9))

    def stat_counter(self, spec: InsertSpec, out: Path) -> int:
        from PIL import Image, ImageDraw

        W, H = spec.width, spec.height
        pad = round(H * 0.1)
        probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        # sizes from the FINAL text, so the card doesn't grow while the number counts
        num_font, nb = self._fit(probe, spec.text(), spec.font, round(H * 0.46), W - 2 * pad, round(H * 0.2))
        lab_font, lb = (self._fit(probe, spec.label, spec.font, round(H * 0.15), W - 2 * pad, round(H * 0.09))
                        if spec.label else (None, (0, 0, 0, 0)))
        num_h, lab_h = nb[3] - nb[1], lb[3] - lb[1]
        gap = round(H * 0.05) if spec.label else 0
        card_w = min(W, max(nb[2] - nb[0], lb[2] - lb[0]) + 2 * pad)
        card_h = min(H, num_h + gap + lab_h + 2 * pad)
        x0, y0 = (W - card_w) // 2, (H - card_h) // 2
        accent, white = _rgb(spec.accent), _rgb(spec.text_colour)
        outline = max(3, round(H * 0.016))

        def card(text: str) -> "Image.Image":
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((x0, y0, x0 + card_w - 1, y0 + card_h - 1), radius=round(card_h * 0.18),
                                fill=(12, 12, 16, 200), outline=(*accent, 255), width=outline)
            b = d.textbbox((0, 0), text, font=num_font)
            d.text(((W - (b[2] - b[0])) // 2 - b[0], y0 + pad - nb[1]), text, font=num_font, fill=(*accent, 255))
            if spec.label:
                d.text(((W - (lb[2] - lb[0])) // 2 - lb[0], y0 + pad + num_h + gap - lb[1]), spec.label,
                       font=lab_font, fill=(*white, 255))
            return im

        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        count = max(1, round(spec.duration * spec.fps))
        cache: dict[str, Image.Image] = {}
        last_key, last_file = None, None
        for k in range(count):
            t = k / spec.fps
            text = spec.text(spec.value * ease_out(t / spec.count_seconds) if spec.count_seconds > 0 else None)
            scale = 0.85 + 0.15 * ease_out(t / POP_SECONDS)
            alpha = min(t / FADE_IN, 1.0, (spec.duration - t) / FADE_OUT)
            scale, alpha = round(scale, 3), round(max(alpha, 0.0), 2)
            key = f"{text}|{scale}|{alpha}"
            f = out / f"{k + 1:04d}.png"
            if key == last_key:  # held frames: a hard link, not another PNG
                try:
                    os.link(last_file, f)
                    continue
                except OSError:
                    pass
            im = cache.get(text) or cache.setdefault(text, card(text))
            if scale < 1:
                sw, sh = round(W * scale), round(H * scale)
                small = im.resize((sw, sh), Image.LANCZOS)
                im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                im.paste(small, ((W - sw) // 2, (H - sh) // 2))
            if alpha < 1:
                a = im.getchannel("A").point(lambda v, m=alpha: round(v * m))
                im = im.copy()
                im.putalpha(a)
            im.save(f, compress_level=1)
            last_key, last_file = key, f
        return count


RENDERERS: dict[str, InsertRenderer] = {"pillow": PillowRenderer()}


def renderer(name: str | None = None) -> InsertRenderer:
    return RENDERERS.get(name or settings.insert_renderer) or RENDERERS["pillow"]


def frames(spec: InsertSpec, root: Path, r: InsertRenderer | None = None) -> Frames:
    """The spec's frames under root/<hash>/, drawn once (cached until the spec or renderer changes)."""
    r = r or renderer()
    folder = root / spec.key(r)
    done = folder / DONE
    if done.is_file():
        return Frames(folder, int(done.read_text() or 0), spec.fps, spec.width, spec.height)
    tmp = folder.with_name(folder.name + ".tmp")
    count = r.render(spec, tmp)
    if count < 1:
        raise RuntimeError(f"{r.name} renderer drew no frames")
    (tmp / "spec.json").write_text(json.dumps(asdict(spec), indent=1), encoding="utf-8")
    (tmp / DONE).write_text(str(count))
    if folder.exists():
        shutil.rmtree(folder)
    tmp.rename(folder)
    return Frames(folder, count, spec.fps, spec.width, spec.height)


def prune(root: Path, keep: set[str]) -> None:
    """Drop cached frame folders no longer used by this short."""
    if not root.is_dir():
        return
    for d in root.iterdir():
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
