"""Speaker-following 9:16 crop — a "virtual cameraman".

1. sample frames (~5/s) inside the clip and detect faces (OpenCV YuNet, Haar fallback)
2. pick the main face (big + confident, sticky to the previous one so it doesn't flip between people)
3. smooth: ignore small moves (deadzone), ease toward big moves, SNAP on camera cuts
4. emit an ffmpeg `sendcmd` file that moves `crop@track`'s x per frame

Two-speaker shots (podcast: two faces too far apart for one 9:16 crop, on screen most of the clip) get a
"split" layout instead: a fixed 1080x960 close-up per speaker, left person on top, stacked with vstack.

Times here are relative to the clip start in SOURCE time (crop runs before jump-cut `select`).
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

# github.com/.../raw returns a Git-LFS pointer for this file; media.githubusercontent serves the binary
YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
DETECT_WIDTH = 640


# A detected face: (cx, area, score[, cy, fh]) — centre x 0..1 of width, area 0..1 of frame, confidence,
# centre y 0..1 of height, face height 0..1 of frame height (cy/fh optional for simple/fake detectors).
Face = tuple


@dataclass
class Sample:
    t: float
    cx: float | None  # face centre x, 0..1 of frame width (None = no face)
    cut: bool = False  # camera/scene change since previous sample
    faces: list[Face] = field(default_factory=list)  # every face seen (two-speaker detection)


@dataclass
class Speaker:
    cx: float  # 0..1 of width
    cy: float  # 0..1 of height
    fh: float  # face height, 0..1 of frame height


PANEL_W, PANEL_H = 1080, 960  # two panels stacked = the 1080x1920 canvas


@dataclass
class ReframePlan:
    mode: str  # "face" | "split" | "center" | "none" (source already vertical)
    src_w: int
    src_h: int
    crop_w: int
    keyframes: list[tuple[float, int]] = field(default_factory=list)  # (t, crop x px)
    panels: list[tuple[int, int, int, int]] = field(default_factory=list)  # split: (w, h, x, y) top, bottom

    def filters(self, cmd_file: str | None) -> list[str]:
        if self.mode == "split" and len(self.panels) == 2:
            (w0, h0, x0, y0), (w1, h1, x1, y1) = self.panels
            size = f"scale={PANEL_W}:{PANEL_H},setsar=1"
            return [f"split[sp0][sp1];[sp0]crop={w0}:{h0}:{x0}:{y0},{size}[spt];"
                    f"[sp1]crop={w1}:{h1}:{x1}:{y1},{size}[spb];[spt][spb]vstack"]
        if self.mode == "face" and self.keyframes and cmd_file:
            x0 = self.keyframes[0][1]
            return [f"sendcmd=f={cmd_file}", f"crop@track={self.crop_w}:{self.src_h}:{x0}:0"]
        return ["crop='min(iw,ih*9/16)':ih"]

    def sendcmd(self) -> str:
        return "".join(f"{t:.3f} crop@track x {x};\n" for t, x in self.keyframes)


# ------------------------------------------------------------------ detection

class FaceDetector:
    """Returns faces as (cx 0..1, area 0..1, score) on a downscaled frame."""

    def __init__(self, model_dir: Path | None = None):
        import cv2

        self.cv2 = cv2
        self.yunet = None
        model = self._ensure_model(model_dir or settings.data_dir / "models")
        if model:
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.6, 0.3, 50)
            except Exception as e:  # old OpenCV etc.
                log.warning("YuNet unavailable (%s), using Haar cascade", e)
        if self.yunet is None:
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        self.kind = "yunet" if self.yunet is not None else "haar"

    @staticmethod
    def _ensure_model(model_dir: Path) -> Path | None:
        path = model_dir / "face_detection_yunet_2023mar.onnx"
        if path.exists() and path.stat().st_size > 50_000:
            return path
        try:
            import requests

            model_dir.mkdir(parents=True, exist_ok=True)
            r = requests.get(YUNET_URL, timeout=60)
            r.raise_for_status()
            if len(r.content) < 50_000:
                raise ValueError("downloaded file is not the model (LFS pointer?)")
            path.write_bytes(r.content)
            return path
        except Exception as e:
            log.warning("could not download YuNet model (%s); falling back to Haar", e)
            return None

    def detect(self, img) -> list[tuple[float, float, float]]:
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(img)
            if faces is None:
                return []
            return [((f[0] + f[2] / 2) / w, (f[2] * f[3]) / (w * h), float(f[-1]), (f[1] + f[3] / 2) / h, f[3] / h)
                    for f in faces]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        rects = self.haar.detectMultiScale(gray, 1.1, 6, minSize=(max(24, w // 25),) * 2)
        return [((x + fw / 2) / w, (fw * fh) / (w * h), 1.0, (y + fh / 2) / h, fh / h) for x, y, fw, fh in rects]


def pick_face(faces: list[tuple[float, float, float]], prev: float | None) -> float | None:
    """Biggest confident face, but stick with the previous person unless someone is much bigger."""
    if not faces:
        return None
    best = max(faces, key=lambda f: f[1] * f[2])
    if prev is None:
        return best[0]
    near = min(faces, key=lambda f: abs(f[0] - prev))
    return near[0] if near[1] * near[2] >= 0.6 * best[1] * best[2] else best[0]


def sample_faces(video: Path, start: float, end: float,
                 detector: FaceDetector) -> tuple[list[Sample], int, int]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    src_w, src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(fps / settings.face_sample_fps))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    duration = end - start

    samples: list[Sample] = []
    prev_hist, prev_cx, idx = None, None, 0
    while True:
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 - start
            if t > duration:
                break
            scale = DETECT_WIDTH / frame.shape[1]
            small = cv2.resize(frame, (DETECT_WIDTH, int(frame.shape[0] * scale)))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            cut = prev_hist is not None and \
                cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < settings.scene_cut_threshold
            prev_hist = hist
            faces = detector.detect(small)
            cx = pick_face(faces, None if cut else prev_cx)
            prev_cx = cx if cx is not None else prev_cx
            samples.append(Sample(t=max(t, 0.0), cx=cx, cut=cut, faces=list(faces)))
        idx += 1
    cap.release()
    return samples, src_w, src_h


# ------------------------------------------------------------------ two speakers

def _speaker(f: Face) -> Speaker:
    cy = f[3] if len(f) > 3 else 0.4
    fh = f[4] if len(f) > 4 else max(f[1], 0.0) ** 0.5 * 1.3  # area -> rough height for 3-tuple detectors
    return Speaker(cx=f[0], cy=cy, fh=fh)


def two_speakers(samples: list[Sample], crop_frac: float,
                 min_share: float | None = None) -> tuple[Speaker, Speaker] | None:
    """Left and right speaker if two faces that don't fit in one 9:16 crop are on screen in >= min_share of samples.

    crop_frac = 9:16 crop width / source width. Per sample, the two strongest faces count when the smaller is at
    least a quarter the size of the bigger (not a face in the background) and they're more than 70% of a crop apart.
    Speakers are the median position/size over those samples (podcast cameras are static).
    """
    min_share = settings.two_speaker_min_share if min_share is None else min_share
    if not samples:
        return None
    lefts, rights = [], []
    for s in samples:
        faces = sorted(s.faces, key=lambda f: -f[1] * f[2])[:2]
        if len(faces) < 2 or faces[1][1] < 0.25 * faces[0][1]:
            continue
        a, b = sorted(faces, key=lambda f: f[0])
        if b[0] - a[0] <= 0.7 * crop_frac:
            continue
        lefts.append(_speaker(a))
        rights.append(_speaker(b))
    if not lefts or len(lefts) / len(samples) < min_share:
        return None

    def med(xs: list[Speaker]) -> Speaker:
        def m(v):
            return sorted(v)[len(v) // 2]
        return Speaker(cx=m([x.cx for x in xs]), cy=m([x.cy for x in xs]), fh=m([x.fh for x in xs]))

    return med(lefts), med(rights)


def split_panels(left: Speaker, right: Speaker, src_w: int, src_h: int) -> list[tuple[int, int, int, int]]:
    """One 9:8 crop (w, h, x, y) per speaker, same size for both, face ~40% down the panel.

    Sized so the face fills ~split_face_height of the panel, never wider than the gap between the two faces
    (so the other person stays out of frame) and never bigger than the source.
    """
    fh = (left.fh + right.fh) / 2
    h = min(max(fh * src_h / settings.split_face_height, 0.3 * src_h), src_h)
    w = h * PANEL_W / PANEL_H
    max_w = min((right.cx - left.cx) * src_w, src_w)
    if w > max_w:
        w = max_w
        h = w * PANEL_H / PANEL_W
    w, h = int(w) // 2 * 2, int(h) // 2 * 2
    panels = []
    for sp in (left, right):
        x = int(min(max(sp.cx * src_w - w / 2, 0), src_w - w))
        y = int(min(max(sp.cy * src_h - 0.4 * h, 0), src_h - h))
        panels.append((w, h, x, y))
    return panels


# ------------------------------------------------------------------ smoothing

def smooth(samples: list[Sample], deadzone: float | None = None, alpha: float | None = None) -> list[Sample]:
    """Virtual cameraman: hold steady inside the deadzone, ease toward big moves, snap on cuts."""
    deadzone = settings.face_deadzone if deadzone is None else deadzone
    alpha = settings.face_smoothing if alpha is None else alpha
    known = [s.cx for s in samples if s.cx is not None]
    if not known:
        return []

    # fill gaps: hold last known face (leading gap takes the first known)
    filled, last = [], known[0]
    for s in samples:
        last = s.cx if s.cx is not None else last
        filled.append(Sample(s.t, last, s.cut))

    # median-of-3 within a shot kills single-frame false detections
    med = []
    for i, s in enumerate(filled):
        win = [filled[j].cx for j in (i - 1, i, i + 1)
               if 0 <= j < len(filled) and not (j > i and filled[j].cut) and not (j < i and s.cut)]
        med.append(Sample(s.t, sorted(win)[1] if len(win) == 3 else s.cx, s.cut))

    out, pos = [], None
    for s in med:
        if pos is None or s.cut:
            pos = s.cx
        elif abs(s.cx - pos) > deadzone:
            pos += alpha * (s.cx - pos)
        out.append(Sample(s.t, pos, s.cut))
    return out


def to_keyframes(path: list[Sample], src_w: int, crop_w: int, duration: float,
                 fps: int | None = None) -> list[tuple[float, int]]:
    """Per-output-frame crop x (px), linearly interpolated, no interpolation across cuts."""
    fps = fps or settings.output_fps
    if not path:
        return []
    max_x = max(src_w - crop_w, 0)

    def px(cx: float) -> int:
        return int(min(max(round(cx * src_w - crop_w / 2), 0), max_x))

    keys: list[tuple[float, int]] = []
    j, n = 0, int(duration * fps) + 1
    for k in range(n):
        t = k / fps
        while j + 1 < len(path) and path[j + 1].t <= t:
            j += 1
        a = path[j]
        if j + 1 < len(path) and not path[j + 1].cut and path[j + 1].t > a.t and t > a.t:
            b = path[j + 1]
            cx = a.cx + (b.cx - a.cx) * (t - a.t) / (b.t - a.t)
        else:
            cx = a.cx
        x = px(cx)
        if not keys or keys[-1][1] != x:
            keys.append((round(t, 3), x))
    return keys


# ------------------------------------------------------------------ entry point

_detector: FaceDetector | None = None


def get_detector() -> FaceDetector:
    global _detector
    if _detector is None:
        _detector = FaceDetector()
        log.info("face detector: %s", _detector.kind)
    return _detector


def plan_reframe(video: Path, start: float, end: float, detector: FaceDetector | None = None,
                 mode: str | None = None) -> ReframePlan:
    try:
        import cv2
    except ImportError:
        log.warning("opencv not installed; using centre crop")
        return ReframePlan("center", 0, 0, 0)

    cap = cv2.VideoCapture(str(video))
    src_w, src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    crop_w = int(src_h * 9 / 16) // 2 * 2
    if not src_w or crop_w >= src_w:
        return ReframePlan("none", src_w, src_h, src_w)
    mode = mode or settings.reframe_mode
    if mode not in ("face", "auto", "split"):
        return ReframePlan("center", src_w, src_h, crop_w)

    try:
        samples, _, _ = sample_faces(video, start, end, detector or get_detector())
    except Exception as e:
        log.warning("face sampling failed (%s); using centre crop", e)
        return ReframePlan("center", src_w, src_h, crop_w)

    if mode in ("auto", "split"):  # "split" = stack whenever two people show up at all, not only most of the time
        pair = two_speakers(samples, crop_w / src_w, min_share=0.15 if mode == "split" else None)
        if pair:
            return ReframePlan("split", src_w, src_h, crop_w, panels=split_panels(*pair, src_w, src_h))

    found = sum(s.cx is not None for s in samples)
    if not samples or found / len(samples) < settings.face_min_coverage:
        return ReframePlan("center", src_w, src_h, crop_w)

    keys = to_keyframes(smooth(samples), src_w, crop_w, end - start)
    return ReframePlan("face", src_w, src_h, crop_w, keys)
