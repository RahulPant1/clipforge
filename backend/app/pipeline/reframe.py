"""Speaker-following 9:16 crop — a "virtual cameraman".

1. sample frames (~5/s) inside the clip and detect faces (OpenCV YuNet, Haar fallback)
2. pick the main face (big + confident, sticky to the previous one so it doesn't flip between people)
3. smooth: ignore small moves (deadzone), ease toward big moves, SNAP on camera cuts
4. emit an ffmpeg `sendcmd` file that moves `crop@track`'s x per frame

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


@dataclass
class Sample:
    t: float
    cx: float | None  # face centre x, 0..1 of frame width (None = no face)
    cut: bool = False  # camera/scene change since previous sample


@dataclass
class ReframePlan:
    mode: str  # "face" | "center" | "none" (source already vertical)
    src_w: int
    src_h: int
    crop_w: int
    keyframes: list[tuple[float, int]] = field(default_factory=list)  # (t, crop x px)

    def filters(self, cmd_file: str | None) -> list[str]:
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
            return [((f[0] + f[2] / 2) / w, (f[2] * f[3]) / (w * h), float(f[-1])) for f in faces]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        rects = self.haar.detectMultiScale(gray, 1.1, 6, minSize=(max(24, w // 25),) * 2)
        return [((x + fw / 2) / w, (fw * fh) / (w * h), 1.0) for x, y, fw, fh in rects]


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
            cx = pick_face(detector.detect(small), None if cut else prev_cx)
            prev_cx = cx if cx is not None else prev_cx
            samples.append(Sample(t=max(t, 0.0), cx=cx, cut=cut))
        idx += 1
    cap.release()
    return samples, src_w, src_h


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
    if (mode or settings.reframe_mode) != "face":
        return ReframePlan("center", src_w, src_h, crop_w)

    try:
        samples, _, _ = sample_faces(video, start, end, detector or get_detector())
    except Exception as e:
        log.warning("face sampling failed (%s); using centre crop", e)
        return ReframePlan("center", src_w, src_h, crop_w)

    found = sum(s.cx is not None for s in samples)
    if not samples or found / len(samples) < settings.face_min_coverage:
        return ReframePlan("center", src_w, src_h, crop_w)

    keys = to_keyframes(smooth(samples), src_w, crop_w, end - start)
    return ReframePlan("face", src_w, src_h, crop_w, keys)
