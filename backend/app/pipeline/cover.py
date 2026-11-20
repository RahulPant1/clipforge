"""Reel / Shorts cover: a sharp frame of the speaker with the cover text printed big (short_N.cover.jpg, 1080x1920).

Frame: sampled across the clip (never inside struck words), scored by sharpness × face size × facing the camera;
you can pick another of the best candidates or let it choose. Text: clip.cover_text, else the Instagram post's
cover_text (Content Studio), else the hook. Text sits inside the centre 3:4 band, so Instagram's profile grid
(which shows only the middle 1080x1440 of a reel cover) never cuts it off.
"""
import logging
import math
from pathlib import Path

from ..models import Clip
from .fonts import font_file, strip_emoji
from .jumpcut import clean_removed

log = logging.getLogger(__name__)

W, H = 1080, 1920
GRID_TOP, GRID_BOTTOM = (H - W * 4 // 3) // 2, (H + W * 4 // 3) // 2  # 240..1680: visible in the IG grid
TEXT_BOTTOM = GRID_BOTTOM - 110
TEXT_W = 940
SAMPLES = 16
CHOICES = 4


def default_text(clip: Clip) -> str:
    ig = clip.content.get("instagram")
    return clip.cover_text or (ig.extra.get("cover_text", "") if ig else "") or clip.hook or clip.title


def _read(cap, t: float):
    import cv2

    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    return frame if ok else None


def _face(faces: list, follow: float | None):
    if not faces:
        return None
    if follow is not None:
        return min(faces, key=lambda f: abs(f[0] - follow))
    return max(faces, key=lambda f: f[1] * f[2])


def score_frame(frame, faces: list, follow: float | None = None) -> tuple[float, float | None]:
    """(score, face x) — sharp, bright, a big face looking at the camera wins; no face scores half."""
    import cv2
    import numpy as np

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, (320, int(320 * grey.shape[0] / grey.shape[1])))
    sharp = math.log1p(float(cv2.Laplacian(small, cv2.CV_64F).var()))
    bright = float(np.mean(small))
    s = sharp * (0.2 if bright < 35 or bright > 235 else 1.0)  # black / blown-out frames
    f = _face(faces, follow)
    if f is None:
        return s * 0.5, None
    look = abs(f[5]) if len(f) > 5 else 0.0
    return s * (1 + 6 * math.sqrt(f[1])) * (1 - 0.5 * min(look, 1.0)), float(f[0])


def candidates(video: Path, clip: Clip, detector=None, n: int = CHOICES, samples: int = SAMPLES) -> list[dict]:
    """The n best cover frames, best first: [{t (source s), x (face 0..1 or None), score}], ≥ 1 s apart."""
    import cv2

    from .reframe import DETECT_WIDTH, get_detector

    detector = detector or get_detector()
    removed = clean_removed(clip.removed, clip.start, clip.end)
    span = clip.end - clip.start
    times = [clip.start + span * (0.08 + 0.84 * k / max(samples - 1, 1)) for k in range(samples)]
    times = [t for t in times if not any(a <= t < b for a, b in removed)]
    cap = cv2.VideoCapture(str(video))
    scored = []
    for t in times:
        frame = _read(cap, t)
        if frame is None:
            continue
        h, w = frame.shape[:2]
        faces = detector.detect(cv2.resize(frame, (DETECT_WIDTH, int(h * DETECT_WIDTH / w))))
        s, x = score_frame(frame, faces, clip.options.follow)
        scored.append({"t": round(t, 2), "x": x, "score": round(s, 3)})
    cap.release()
    best: list[dict] = []
    for c in sorted(scored, key=lambda c: -c["score"]):
        if all(abs(c["t"] - b["t"]) >= 1.0 for b in best):
            best.append(c)
        if len(best) == n:
            break
    return best


def crop_9x16(frame, x: float | None):
    """Vertical crop around face x (0..1), clamped to the frame; already-vertical sources are used whole."""
    h, w = frame.shape[:2]
    cw = min(w, int(h * 9 / 16))
    cx = (x if x is not None else 0.5) * w
    x0 = int(min(max(cx - cw / 2, 0), w - cw))
    return frame[:, x0:x0 + cw]


def _fit(draw, text: str, font_path: str | None):
    from PIL import ImageFont

    from .carousel import _font, wrap

    for size in range(150, 69, -8):
        font = ImageFont.truetype(font_path, size) if font_path else _font(size)
        lines = wrap(draw, text, font, TEXT_W)
        if len(lines) <= 3 and all(draw.textlength(ln, font=font) <= TEXT_W for ln in lines):
            return font, lines, size
    return font, lines[:4], size


def draw_cover(frame, x: float | None, text: str, out: Path, font_family: str | None = None) -> None:
    """Crop, scale to 1080x1920, darken the lower part and print `text` (emoji stripped: Pillow draws boxes)."""
    import cv2
    from PIL import Image, ImageDraw

    img = Image.fromarray(cv2.cvtColor(crop_9x16(frame, x), cv2.COLOR_BGR2RGB)).resize((W, H), Image.LANCZOS)
    shade = Image.new("L", (1, H))  # transparent at the top, ~70% black behind the text
    for y in range(H):
        shade.putpixel((0, y), int(180 * min(max((y - H * 0.42) / (H * 0.3), 0), 1)))
    img.paste(Image.new("RGB", (W, H)), (0, 0), shade.resize((W, H)))
    text = strip_emoji(text).upper()
    if text:
        draw = ImageDraw.Draw(img)
        font, lines, size = _fit(draw, text, font_file(font_family) if font_family else None)
        lh = int(size * 1.08)
        y = TEXT_BOTTOM - lh * len(lines)
        for i, line in enumerate(lines):
            fill = (255, 214, 10) if i == len(lines) - 1 and len(lines) > 1 else (255, 255, 255)  # last line pops
            draw.text((W // 2, y + i * lh), line, font=font, fill=fill, anchor="ma",
                      stroke_width=max(4, size // 14), stroke_fill=(0, 0, 0))
    img.save(out, "JPEG", quality=90)


def make_cover(video: Path, clip: Clip, job_dir: Path, font_family: str | None = None, detector=None) -> str:
    """Writes short_<n>.cover.jpg and fills clip.cover / cover_version. Returns the filename."""
    import cv2

    from .reframe import DETECT_WIDTH, get_detector

    t = clip.cover_t
    if t is None or not clip.start <= t <= clip.end:  # automatic: the best-scoring frame
        best = candidates(video, clip, detector, n=1)
        if not best:
            raise RuntimeError("couldn't read any frame of the clip")
        t = best[0]["t"]
    cap = cv2.VideoCapture(str(video))
    frame = _read(cap, t)
    cap.release()
    if frame is None:
        raise RuntimeError(f"couldn't read the frame at {t:.1f} s")
    h, w = frame.shape[:2]
    faces = (detector or get_detector()).detect(cv2.resize(frame, (DETECT_WIDTH, int(h * DETECT_WIDTH / w))))
    face = _face(faces, clip.options.follow)
    x = face[0] if face is not None else clip.options.follow
    name = f"short_{clip.index}.cover.jpg"
    draw_cover(frame, x, default_text(clip), job_dir / name, font_family)
    clip.cover = name
    clip.cover_version += 1
    return name


def preview(video: Path, t: float, x: float | None, width: int = 180) -> bytes:
    """Small 9:16 JPEG of a candidate frame (no text) for the frame picker."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    frame = _read(cap, t)
    cap.release()
    if frame is None:
        return b""
    crop = crop_9x16(frame, x)
    small = cv2.resize(crop, (width, int(width * 16 / 9)))
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpg.tobytes() if ok else b""
