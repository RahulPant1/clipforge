"""Cut the speaker out of a frame, for cutout covers and quote cards.

rembg with u2net_human_seg on the CPU: ~0.6 s per frame, model 168 MB downloaded once into ~/.rembg. Chosen on real
frames over isnet-general-use (kept mic stands and on-screen text) and birefnet-portrait (31 s per frame on CPU and
killed for memory under WSL's 9 GB). CPU on purpose: the GPU belongs to Whisper/Gemma (one GPU job at a time).

Anything going wrong (rembg missing, no download, no person) → None, and the caller falls back to a plain frame.
"""
import logging
import threading
from collections import OrderedDict

log = logging.getLogger(__name__)

MODEL = "u2net_human_seg"
MIN_AREA = 0.04  # a person smaller than 4% of the frame isn't worth a cutout
MAX_FILL = 0.85  # a person (head above shoulders) fills 41-79% of their box on real frames; an on-screen text card
#                 over a blurred face filled 90% and got "cut out" — anything this boxy isn't a person
SPAN = 0.45  # keep the person within ±0.45 frame heights of their face: side-by-side podcast panels touch
_session = None
_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()  # (id, t, x) -> RGBA cutout; cover + quote cards share one


def _mask(rgb):
    """0..255 person mask (H×W uint8) for an RGB array. Replaced in tests."""
    global _session
    import numpy as np
    from PIL import Image
    from rembg import new_session, remove

    with _lock:
        if _session is None:
            _session = new_session(MODEL, providers=["CPUExecutionProvider"])
        m = remove(Image.fromarray(rgb), session=_session, only_mask=True)
    return np.asarray(m, dtype=np.uint8)


def pick_person(mask, face_x: float | None):
    """Keep the one person (connected blob) nearest the face we follow, else the biggest; None if too small."""
    import cv2
    import numpy as np

    binary = (mask > 127).astype(np.uint8)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(binary, 8)
    if n <= 1:
        return None
    h, w = mask.shape
    blobs = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 0.01 * h * w]
    if not blobs:
        return None
    if face_x is not None:
        def dist(i):  # 0 when the face x is inside the blob's box
            x0, x1 = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH]
            fx = face_x * w
            return 0 if x0 <= fx <= x1 else min(abs(fx - x0), abs(fx - x1))
        best = min(blobs, key=lambda i: (dist(i), -stats[i, cv2.CC_STAT_AREA]))
    else:
        best = max(blobs, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    if stats[best, cv2.CC_STAT_AREA] < MIN_AREA * h * w:
        return None
    keep = np.where(labels == best, mask, 0).astype(np.uint8)
    if face_x is not None:  # two people in touching panels form one blob: cut it down to this one
        lo, hi = int(max(face_x * w - SPAN * h, 0)), int(min(face_x * w + SPAN * h, w))
        keep[:, :lo], keep[:, hi:] = 0, 0
        if (keep > 127).sum() < MIN_AREA * h * w:
            return None
    return keep


def cutout(frame_bgr, face_x: float | None = None, key=None):
    """RGBA PIL image of the person, cropped to them (soft edges kept); None if there's no usable person.
    No face (face_x None) → None: on real footage a face-less frame "cut out" on-screen text as a person.
    `key` (e.g. (video, t)) caches the result so the cover and the quote cards don't cut the same frame twice."""
    import cv2
    import numpy as np
    from PIL import Image

    if face_x is None:
        return None
    ck = (key, round(face_x, 2)) if key is not None else None
    if ck is not None and ck in _cache:
        _cache.move_to_end(ck)
        return _cache[ck]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    try:
        mask = _mask(rgb)
    except Exception as e:
        log.warning("cutout unavailable (%s) — using the plain frame", e)
        return None
    person = pick_person(mask, face_x)
    if person is not None:
        ys, xs = np.nonzero(person > 8)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        if (person[y0:y1, x0:x1] > 127).mean() > MAX_FILL:
            person = None
    if person is None:
        out = None
    else:
        rgba = np.dstack([rgb, person])[y0:y1, x0:x1]
        out = Image.fromarray(rgba, "RGBA")
        out.info["touches_bottom"] = y1 >= rgb.shape[0] - 2  # cut off by the frame: anchor it to the bottom
    if ck is not None:
        _cache[ck] = out
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return out


def outlined(person, scale_h: int, max_w: int, stroke: int = 12, glow=(255, 77, 109)):
    """The person resized to fit (scale_h × max_w), on a transparent canvas with a white stroke and a soft
    accent-coloured glow around the silhouette. Returns (RGBA image, padding added on each side)."""
    from PIL import Image, ImageFilter

    s = min(scale_h / person.height, max_w / person.width)
    p = person.resize((max(1, round(person.width * s)), max(1, round(person.height * s))), Image.LANCZOS)
    pad = stroke * 4
    canvas = Image.new("RGBA", (p.width + 2 * pad, p.height + 2 * pad))
    alpha = Image.new("L", canvas.size)
    alpha.paste(p.getchannel("A"), (pad, pad))
    halo = alpha.filter(ImageFilter.MaxFilter(stroke * 2 + 1)).filter(ImageFilter.GaussianBlur(stroke * 1.5))
    canvas.paste(Image.new("RGBA", canvas.size, (*glow, 255)), (0, 0), halo.point(lambda v: v * 0.8))
    edge = alpha.filter(ImageFilter.MaxFilter(stroke | 1))
    canvas.paste(Image.new("RGBA", canvas.size, (255, 255, 255, 255)), (0, 0), edge)
    canvas.alpha_composite(p, (pad, pad))
    return canvas, pad
