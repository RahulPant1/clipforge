"""Speaker-following 9:16 crop — a "virtual cameraman".

1. sample frames (~5/s) inside the clip and detect faces (OpenCV YuNet, Haar fallback)
2. pick the main face (big + confident, sticky to the previous one so it doesn't flip between people)
3. smooth: ignore small moves (deadzone), ease toward big moves, SNAP on camera cuts
   + lead room: shift the crop toward where the face looks / moves (YuNet eye+nose landmarks, track slope)
4. emit an ffmpeg `sendcmd` file that moves `crop@track`'s x per frame

Two-speaker shots (podcast: two faces too far apart for one 9:16 crop, on screen most of the clip) get a
"split" layout instead: a fixed 1080x960 close-up per speaker, left person on top, stacked with vstack —
or "speaker": a full-height 9:16 crop that hard-cuts to whoever is talking (mouth movement per face).

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


# A detected face: (cx, area, score[, cy, fh[, look]]) — centre x 0..1 of width, area 0..1 of frame, confidence,
# centre y 0..1 of height, face height 0..1 of frame height (cy/fh optional for simple/fake detectors),
# look = head turn from landmarks, (nose x − eye-midpoint x) / eye distance: 0 frontal, + turned to image-right.
Face = tuple


@dataclass
class Sample:
    t: float
    cx: float | None  # face centre x, 0..1 of frame width (None = no face)
    cut: bool = False  # camera/scene change since previous sample
    faces: list[Face] = field(default_factory=list)  # every face seen (two-speaker detection)
    patches: list = field(default_factory=list)  # per face: (mouth, eyes) small grey arrays, or None
    look: float | None = None  # the picked face's head turn (see Face); None = unknown (Haar, no face)


@dataclass
class Speaker:
    cx: float  # 0..1 of width
    cy: float  # 0..1 of height
    fh: float  # face height, 0..1 of frame height
    look: float = 0.0  # median head turn over the clip (see Face)


PANEL_W, PANEL_H = 1080, 960  # two panels stacked = the 1080x1920 canvas


def fit_graph(inp: str = "", out: str = "", size: tuple[int, int] = (1080, 1920)) -> str:
    """Whole frame scaled to the width, centred on a blurred, darkened copy that fills the canvas — for screen
    recordings, slides and action shots, where a 9:16 crop cuts the content off. Blur runs at 1/4 size (cheap)."""
    w, h = size
    bw, bh = w // 4 // 2 * 2, h // 4 // 2 * 2
    return (f"{inp}split[fitfg][fitbg];[fitbg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
            f"boxblur=12:2,eq=brightness=-0.08,scale={w}:{h},setsar=1[fitb];"
            f"[fitfg]scale='min({w},iw*{h}/ih)':-2,setsar=1[fitf];"
            f"[fitb][fitf]overlay=(W-w)/2:(H-h)/2{out}")


@dataclass
class ReframePlan:
    mode: str  # "face" | "split" | "speaker" | "manual" | "center" | "fit" | "none" (source already vertical)
    src_w: int
    src_h: int
    crop_w: int
    keyframes: list[tuple[float, int]] = field(default_factory=list)  # (t, crop x px)
    panels: list[tuple[int, int, int, int]] = field(default_factory=list)  # split: (w, h, x, y) top, bottom
    fit: list[tuple[float, float]] = field(default_factory=list)  # face/speaker: no-face spans shown whole (s)
    size: tuple[int, int] = (1080, 1920)  # output canvas (9:16, or 4:5 / 1:1 for feed versions)

    def filters(self, cmd_file: str | None) -> list[str]:
        w, h = self.size
        if self.mode == "fit":
            return [fit_graph(size=self.size)]
        if self.mode in ("face", "speaker") and self.keyframes and cmd_file and self.fit:
            # crop branch, with the fitted branch laid over it during the no-face shots (same source time)
            on = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in self.fit)
            crop = f"sendcmd=f={cmd_file},crop@track={self.crop_w}:{self.src_h}:{self.keyframes[0][1]}:0"
            return [f"split[rfc][rff];[rfc]{crop},scale={w}:{h},setsar=1[rfcv];"
                    f"{fit_graph('[rff]', '[rffv]', self.size)};"
                    f"[rfcv][rffv]overlay=enable='{on}'"]
        if self.mode == "split" and len(self.panels) == 2:
            (w0, h0, x0, y0), (w1, h1, x1, y1) = self.panels
            size = f"scale={w}:{h // 2},setsar=1"
            return [f"split[sp0][sp1];[sp0]crop={w0}:{h0}:{x0}:{y0},{size}[spt];"
                    f"[sp1]crop={w1}:{h1}:{x1}:{y1},{size}[spb];[spt][spb]vstack"]
        if self.mode in ("face", "speaker", "manual") and self.keyframes and cmd_file:
            x0 = self.keyframes[0][1]
            return [f"sendcmd=f={cmd_file}", f"crop@track={self.crop_w}:{self.src_h}:{x0}:0"]
        # centre crop to the canvas shape; a source narrower than it (vertical video → 4:5) loses top and bottom,
        # a little more from the bottom (faces sit in the upper half)
        return [f"crop='min(iw,ih*{w}/{h})':'min(ih,iw*{h}/{w})':(iw-ow)/2:(ih-oh)*0.4"]

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
            # row: x, y, w, h, right eye xy, left eye xy, nose xy, mouth corners xy ×2, score
            return [((f[0] + f[2] / 2) / w, (f[2] * f[3]) / (w * h), float(f[-1]), (f[1] + f[3] / 2) / h, f[3] / h,
                     head_turn(f[4], f[6], f[8])) for f in faces]
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        rects = self.haar.detectMultiScale(gray, 1.1, 6, minSize=(max(24, w // 25),) * 2)
        return [((x + fw / 2) / w, (fw * fh) / (w * h), 1.0, (y + fh / 2) / h, fh / h) for x, y, fw, fh in rects]


def head_turn(eye_a_x: float, eye_b_x: float, nose_x: float) -> float:
    """Nose offset from the eye midpoint in eye distances, −1..1: + = face turned toward image-right."""
    dist = abs(eye_b_x - eye_a_x)
    if dist < 1e-3:
        return 0.0
    return float(max(-1.0, min(1.0, (nose_x - (eye_a_x + eye_b_x) / 2) / dist)))


FOLLOW_RADIUS = 0.12  # a chosen person's face must be within this much of the frame width of where they were


def pick_face(faces: list[tuple[float, float, float]], prev: float | None,
              follow: float | None = None) -> float | None:
    """Biggest confident face, but stick with the previous person unless someone is much bigger.

    `follow` (x 0..1, chosen in the UI): only ever that person — the face nearest to where they were last seen
    (or to `follow` after a cut); None when they're not in frame, so the crop holds instead of jumping to someone else.
    """
    if not faces:
        return None
    if follow is not None:
        target = follow if prev is None else prev
        near = [f for f in faces if abs(f[0] - target) <= FOLLOW_RADIUS]
        return min(near, key=lambda f: abs(f[0] - target))[0] if near else None
    best = max(faces, key=lambda f: f[1] * f[2])
    if prev is None:
        return best[0]
    near = min(faces, key=lambda f: abs(f[0] - prev))
    return near[0] if near[1] * near[2] >= 0.6 * best[1] * best[2] else best[0]


def sample_faces(video: Path, start: float, end: float, detector: FaceDetector,
                 follow: float | None = None) -> tuple[list[Sample], int, int]:
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
            cx = pick_face(faces, None if cut else prev_cx, follow)
            prev_cx = cx if cx is not None else prev_cx
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            look = next((float(f[5]) for f in faces if f[0] == cx and len(f) > 5), None)
            samples.append(Sample(t=max(t, 0.0), cx=cx, cut=cut, faces=list(faces),
                                  patches=[face_patches(gray, f) for f in faces], look=look))
        idx += 1
    cap.release()
    return samples, src_w, src_h


# ------------------------------------------------------------------ two speakers

PATCH = (24, 12)  # mouth/eye thumbnails (w, h): enough to see lips move, cheap to keep for every sample
PATCH_MARGIN = 3  # extra px around each thumbnail so consecutive ones can be aligned (head sway, box jitter)


def face_patches(gray, f: Face):
    """(mouth, eyes) grey thumbnails for active-speaker detection; None without face geometry (cy, fh).

    Each is PATCH plus PATCH_MARGIN on every side, mean-removed (lighting flicker doesn't count as talking).
    """
    if len(f) < 5 or f[4] <= 0:
        return None
    import cv2
    import numpy as np

    h, w = gray.shape[:2]
    cx, cy, fh = f[0], f[3], f[4]
    fw = f[1] / fh  # area = fw * fh (both as fractions of the frame)
    pw, ph = PATCH
    gx, gy = (pw + 2 * PATCH_MARGIN) / pw, (ph + 2 * PATCH_MARGIN) / ph  # grow the box by the margin

    def box(y0: float, y1: float):
        half_w, half_h = 0.25 * fw * gx, (y1 - y0) / 2 * fh * gy
        mid_y = cy + (y0 + y1) / 2 * fh
        xa, xb = int((cx - half_w) * w), int((cx + half_w) * w)
        ya, yb = int((mid_y - half_h) * h), int((mid_y + half_h) * h)
        if xa < 0 or ya < 0 or xb > w or yb > h or xb - xa < 8 or yb - ya < 6:
            return None
        size = (pw + 2 * PATCH_MARGIN, ph + 2 * PATCH_MARGIN)
        patch = cv2.resize(gray[ya:yb, xa:xb], size, interpolation=cv2.INTER_AREA).astype(np.float32)
        return patch - patch.mean()

    mouth, eyes = box(0.15, 0.45), box(-0.25, -0.05)  # below the nose / across the eyes
    return (mouth, eyes) if mouth is not None and eyes is not None else None


def patch_change(cur, prev) -> float:
    """Mean abs difference after the best small shift: moving the whole patch is free, changing its shape isn't."""
    m = PATCH_MARGIN
    ph, pw = cur.shape[0] - 2 * m, cur.shape[1] - 2 * m
    centre = cur[m:m + ph, m:m + pw]
    return min(float(abs(centre - prev[m + dy:m + dy + ph, m + dx:m + dx + pw]).mean())
               for dy in range(-m, m + 1) for dx in range(-m, m + 1))


def _speaker(f: Face) -> Speaker:
    cy = f[3] if len(f) > 3 else 0.4
    fh = f[4] if len(f) > 4 else max(f[1], 0.0) ** 0.5 * 1.3  # area -> rough height for 3-tuple detectors
    return Speaker(cx=f[0], cy=cy, fh=fh, look=float(f[5]) if len(f) > 5 else 0.0)


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
        return Speaker(cx=m([x.cx for x in xs]), cy=m([x.cy for x in xs]), fh=m([x.fh for x in xs]),
                       look=m([x.look for x in xs]))

    return med(lefts), med(rights)


def split_panels(left: Speaker, right: Speaker, src_w: int, src_h: int, lead: float | None = None,
                 panel: tuple[int, int] = (PANEL_W, PANEL_H)) -> list[tuple[int, int, int, int]]:
    """One crop (w, h, x, y) per speaker in the panel's shape (9:8 for 9:16, 2:1 for 1:1), same size for both,
    face ~40% down the panel, with lead room
    (`lead` × panel width toward where the speaker looks) as long as the other face stays out.

    Sized so the face fills ~split_face_height of the panel, never wider than the gap between the two faces
    (so the other person stays out of frame) and never bigger than the source.
    """
    fh = (left.fh + right.fh) / 2
    h = min(max(fh * src_h / settings.split_face_height, 0.3 * src_h), src_h)
    pw, ph = panel
    w = h * pw / ph
    max_w = min((right.cx - left.cx) * src_w, src_w)
    if w > max_w:
        w = max_w
        h = w * ph / pw
    if h > src_h:
        h = src_h
        w = h * pw / ph
    w, h = int(w) // 2 * 2, int(h) // 2 * 2
    lead = settings.reframe_lead if lead is None else lead
    panels = []
    for sp, other in ((left, right), (right, left)):
        cx = sp.cx * src_w + lead * w * look_direction(sp.look)
        # lead room may not pull the other person's face into the panel
        keep_out = other.cx * src_w - (0.6 * other.fh * src_h if other is right else -0.6 * other.fh * src_h)
        if other is right:
            cx = min(cx, max(keep_out - w / 2, sp.cx * src_w))
        else:
            cx = max(cx, min(keep_out + w / 2, sp.cx * src_w))
        x = int(min(max(cx - w / 2, 0), src_w - w))
        y = int(min(max(sp.cy * src_h - 0.4 * h, 0), src_h - h))
        panels.append((w, h, x, y))
    return panels


def _side(sample: Sample, sp: Speaker) -> int | None:
    """Index of the face in this sample that is `sp` (nearest centre, within 10% of the frame)."""
    if not sample.faces:
        return None
    i = min(range(len(sample.faces)), key=lambda k: abs(sample.faces[k][0] - sp.cx))
    return i if abs(sample.faces[i][0] - sp.cx) < 0.1 else None


def speaker_activity(samples: list[Sample], left: Speaker, right: Speaker) -> list[tuple[float, float]]:
    """Per sample, how much each speaker's mouth moved since the previous sample (0 when unknown).

    Mouth change minus eye change: nodding, camera shake and lighting move both regions, talking mostly the mouth.
    """
    out: list[tuple[float, float]] = []
    prev: list = [None, None]
    for s in samples:
        acts = []
        for k, sp in enumerate((left, right)):
            i = _side(s, sp)
            cur = s.patches[i] if i is not None and i < len(s.patches) else None
            a = 0.0
            if cur is not None and prev[k] is not None and not s.cut:
                a = max(patch_change(cur[0], prev[k][0]) - patch_change(cur[1], prev[k][1]), 0.0)
            prev[k] = cur
            acts.append(a)
        out.append((acts[0], acts[1]))
    return out


def active_speaker_path(samples: list[Sample], acts: list[tuple[float, float]], left: Speaker, right: Speaker,
                        window: float | None = None, min_shot: float | None = None,
                        margin: float | None = None, min_turn: float | None = None,
                        lead: float = 0.0) -> list[Sample]:
    """Camera path that hard-cuts between the two speakers' fixed positions, following the talker.

    Offline (the whole clip is known), so cuts land where a turn STARTS instead of lagging behind it:
    1. per sample, the leader is whoever's mouth activity (averaged over `window` s, centred) beats the other's
       by `margin` and a noise floor — otherwise nobody leads
    2. turns shorter than `min_turn` s ("mm-hm", a laugh) are ignored
    3. cut at the start of each other-speaker turn, but never sooner than `min_shot` s after the last cut
    Starts on the first real turn's speaker (bigger face if nobody talks).
    `lead` (fraction of source width) shifts each shot toward where that speaker looks (lead room).
    """
    window = settings.speaker_window if window is None else window
    min_shot = settings.speaker_min_shot if min_shot is None else min_shot
    margin = settings.speaker_margin if margin is None else margin
    min_turn = settings.speaker_min_turn if min_turn is None else min_turn
    if not samples:
        return []
    floor = 0.5  # mean grey-level change; below this nobody is visibly talking
    ts = [s.t for s in samples]

    def avg(i: int, k: int) -> float:
        vals = [acts[j][k] for j in range(len(samples)) if abs(ts[j] - ts[i]) <= window / 2]
        return sum(vals) / len(vals)

    leaders: list[int | None] = []
    for i in range(len(samples)):
        lft, rgt = avg(i, 0), avg(i, 1)
        if max(lft, rgt) < floor:
            leaders.append(None)
        elif lft > rgt * (1 + margin):
            leaders.append(0)
        elif rgt > lft * (1 + margin):
            leaders.append(1)
        else:
            leaders.append(None)

    turns: list[tuple[int, float, float]] = []  # (speaker, start t, end t) of runs long enough to count
    i = 0
    while i < len(samples):
        j = i
        while j + 1 < len(samples) and leaders[j + 1] == leaders[i]:
            j += 1
        end_t = ts[j + 1] if j + 1 < len(samples) else ts[j]
        if leaders[i] is not None and end_t - ts[i] >= min_turn:
            turns.append((leaders[i], ts[i], end_t))
        i = j + 1

    cur = turns[0][0] if turns else (0 if left.fh >= right.fh else 1)
    since = ts[0]
    switches: list[tuple[float, int]] = []
    for who, t_start, t_end in turns:
        if who == cur:
            continue
        at = max(t_start, since + min_shot)
        if at < t_end:  # turn still going once we're allowed to cut
            switches.append((at, who))
            cur, since = who, at

    pos = (left.cx + lead * look_direction(left.look), right.cx + lead * look_direction(right.look))
    cur = turns[0][0] if turns else (0 if left.fh >= right.fh else 1)
    path, k = [], 0
    for i, s in enumerate(samples):
        cut = False
        while k < len(switches) and switches[k][0] <= s.t:
            cur, cut, k = switches[k][1], True, k + 1
        path.append(Sample(s.t, pos[cur], cut or (i > 0 and s.cut)))
    return path


# ------------------------------------------------------------------ lead room

LOOK_DEADBAND, LOOK_FULL = 0.1, 0.35  # head turn (eye distances): ignore below, full lead room at
MOVE_DEADBAND, MOVE_FULL = 0.02, 0.08  # face speed (frame widths / s): ignore below, full lead room at


def _ramp(x: float, dead: float, full: float) -> float:
    """0 inside ±dead, ±1 beyond ±full, linear between."""
    mag = min(max((abs(x) - dead) / (full - dead), 0.0), 1.0)
    return mag if x > 0 else -mag


def look_direction(look: float | None) -> float:
    return 0.0 if look is None else _ramp(look, LOOK_DEADBAND, LOOK_FULL)


def lead_directions(samples: list[Sample], window: float | None = None, hold: float | None = None) -> list[float]:
    """Per sample, −1 / 0 / +1: lead room to the left, none, to the right (where the face looks or moves).

    1. over `window` s centred on the sample, within the shot: the median head turn (landmark jitter doesn't
       count) plus the least-squares slope of the face track (walking toward one side), rounded to a side
    2. a new side must last `hold` s, else the previous one stays — a glance at the camera mustn't pan away and back
    """
    window = settings.lead_window if window is None else window
    hold = settings.lead_hold if hold is None else hold
    shot, ids = 0, []
    for i, s in enumerate(samples):
        shot += bool(i and s.cut)
        ids.append(shot)
    out = []
    for i, s in enumerate(samples):
        near = [samples[j] for j in range(len(samples)) if ids[j] == ids[i] and abs(samples[j].t - s.t) <= window / 2]
        looks = sorted(n.look for n in near if n.look is not None)
        d = look_direction(looks[len(looks) // 2]) if looks else 0.0
        pts = [(n.t, n.cx) for n in near if n.cx is not None]
        if len(pts) >= 3 and pts[-1][0] - pts[0][0] >= 0.5 * window:
            mt, mx = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            var = sum((p[0] - mt) ** 2 for p in pts)
            if var > 0:
                d += _ramp(sum((p[0] - mt) * (p[1] - mx) for p in pts) / var, MOVE_DEADBAND, MOVE_FULL)
        out.append(float(round(max(-1.0, min(1.0, d)))))

    i = 0
    while i < len(samples):  # runs of one side within a shot; too-short runs keep the previous side
        j = i
        while j + 1 < len(samples) and out[j + 1] == out[i] and ids[j + 1] == ids[i]:
            j += 1
        end_t = samples[j + 1].t if j + 1 < len(samples) and ids[j + 1] == ids[i] else samples[j].t
        if i > 0 and ids[i - 1] == ids[i] and end_t - samples[i].t < hold:
            out[i:j + 1] = [out[i - 1]] * (j + 1 - i)
        i = j + 1
    return out


# ------------------------------------------------------------------ smoothing

def smooth(samples: list[Sample], deadzone: float | None = None, alpha: float | None = None,
           lead: float = 0.0) -> list[Sample]:
    """Virtual cameraman: hold steady inside the deadzone, ease toward big moves, snap on cuts.

    `lead` (fraction of source width at full direction) adds lead room on top of the tracked face: its own
    eased offset, so turning the head pans gently even when the face itself stays inside the deadzone.
    """
    deadzone = settings.face_deadzone if deadzone is None else deadzone
    alpha = settings.face_smoothing if alpha is None else alpha
    dirs = lead_directions(samples) if lead else [0.0] * len(samples)
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

    out, pos, off = [], None, 0.0
    for s, d in zip(med, dirs, strict=True):
        if pos is None or s.cut:
            pos, off = s.cx, lead * d
        else:
            if abs(s.cx - pos) > deadzone:
                pos += alpha * (s.cx - pos)
            off += alpha * (lead * d - off)
        out.append(Sample(s.t, pos + off, s.cut))
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


# ------------------------------------------------------------------ shots without a face

FIT_MIN_SAMPLES = 3  # shorter shots (< ~0.6 s, often a false cut) copy the shot before them: no flicker


def no_face_spans(samples: list[Sample], duration: float, share: float | None = None) -> list[tuple[float, float]]:
    """Clip-relative spans of the shots (split at scene cuts) where any face shows in < `share` of samples.

    Uses every detected face, not just the followed one: a shot of the other person is still a face shot.
    """
    share = settings.face_min_coverage if share is None else share
    shots: list[list[Sample]] = []
    for s in samples:
        if not shots or s.cut:
            shots.append([])
        shots[-1].append(s)
    bounds = [0.0] + [sh[0].t for sh in shots[1:]] + [duration]
    no_face = [sum(bool(x.faces) for x in sh) / len(sh) < share for sh in shots]
    for i, sh in enumerate(shots):
        if len(sh) < FIT_MIN_SAMPLES and len(shots) > 1:
            no_face[i] = no_face[i - 1] if i else no_face[1]
    spans: list[tuple[float, float]] = []
    for i, nf in enumerate(no_face):
        a, b = round(bounds[i], 3), round(min(bounds[i + 1], duration), 3)
        if not nf or b <= a:
            continue
        if spans and abs(spans[-1][1] - a) < 1e-6:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    return spans


# ------------------------------------------------------------------ entry point

_detector: FaceDetector | None = None


def get_detector() -> FaceDetector:
    global _detector
    if _detector is None:
        _detector = FaceDetector()
        log.info("face detector: %s", _detector.kind)
    return _detector


_samples_cache: dict = {}  # the last clip's face samples: the 4:5 / 1:1 renders reuse them (one detection pass)


def _cached_samples(video: Path, start: float, end: float, detector: FaceDetector,
                    follow: float | None) -> tuple[list[Sample], int, int]:
    try:
        stamp = video.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (str(video), stamp, round(start, 3), round(end, 3), follow)
    if _samples_cache.get("key") != key or _samples_cache.get("detector") is not detector:  # (held, so no id reuse)
        _samples_cache.clear()
        _samples_cache.update(key=key, detector=detector, value=sample_faces(video, start, end, detector, follow))
    return _samples_cache["value"]


def tracked_path(job_dir: Path, name: str, src_w: int, crop_w: int, start: float) -> list[tuple[float, float]]:
    """Where the last render's crop sat — (SOURCE t, centre x 0..1) per change, from its sendcmd file — so the
    crop editor can show the tracker's choice and start from it. [] if that render had no moving crop."""
    f = job_dir / f"{name}.crop.cmd"
    if not f.is_file() or not src_w:
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip(";").split()
        if len(parts) == 4 and parts[1] == "crop@track":
            out.append((round(start + float(parts[0]), 3), round((int(parts[3]) + crop_w / 2) / src_w, 4)))
    return out


def source_size(video: Path) -> tuple[int, int]:
    import cv2

    cap = cv2.VideoCapture(str(video))
    size = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return size


def manual_path(keys: list, start: float, end: float) -> list[Sample]:
    """Hand-set crop keys (CropKey: SOURCE t, centre x, pan) → a clip-relative path for to_keyframes().

    The key at or before the clip start sets where it begins; each later key cuts there at its time (hold, then
    jump — like a camera change), or with pan=True glides there from the previous key. Keys past the end are ignored.
    """
    ks = sorted(keys, key=lambda k: k.t)
    before = [k for k in ks if k.t <= start]
    inside = [k for k in ks if start < k.t < end]
    first = before[-1] if before else (inside[0] if inside else None)
    if first is None:
        return []
    path = [Sample(0.0, first.x)]
    for k in inside:
        if k is first:
            continue
        path.append(Sample(round(k.t - start, 3), k.x, cut=not k.pan))
    return path


def plan_reframe(video: Path, start: float, end: float, detector: FaceDetector | None = None,
                 mode: str | None = None, follow: float | None = None,
                 size: tuple[int, int] = (1080, 1920), keys: list | None = None) -> ReframePlan:
    """`follow` (x 0..1 of a face picked with people()) tracks that one person in "face" mode, whatever `mode`
    says — except "center", which stays a plain centre crop. `size` = output canvas: its shape sets the crop
    width (9:16 narrow, 4:5 / 1:1 wider, so more of the scene stays in)."""
    plan = _plan_reframe(video, start, end, detector, mode, follow, size, keys)
    plan.size = size
    return plan


def _plan_reframe(video: Path, start: float, end: float, detector: FaceDetector | None,
                  mode: str | None, follow: float | None, size: tuple[int, int], keys: list | None) -> ReframePlan:
    try:
        import cv2
    except ImportError:
        log.warning("opencv not installed; using centre crop")
        return ReframePlan("center", 0, 0, 0)

    cap = cv2.VideoCapture(str(video))
    src_w, src_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    crop_w = int(src_h * size[0] / size[1]) // 2 * 2
    if not src_w or crop_w >= src_w:
        return ReframePlan("none", src_w, src_h, src_w)
    mode = mode or settings.reframe_mode
    if mode == "manual":  # you placed the crop by hand: no face detection at all
        path = manual_path(keys or [], start, end)
        if path:
            return ReframePlan("manual", src_w, src_h, crop_w, to_keyframes(path, src_w, crop_w, end - start))
        mode = "auto"  # no keys inside the clip (yet): the tracker decides
    if mode == "fit":
        return ReframePlan("fit", src_w, src_h, crop_w)
    if follow is not None and mode != "center":
        mode = "face"
    if mode not in ("face", "auto", "split", "speaker"):
        return ReframePlan("center", src_w, src_h, crop_w)

    fit_on = settings.reframe_no_face == "fit"
    try:
        samples, _, _ = _cached_samples(video, start, end, detector or get_detector(), follow)
    except Exception as e:
        log.warning("face sampling failed (%s); using centre crop", e)
        return ReframePlan("center", src_w, src_h, crop_w)

    if mode in ("auto", "split", "speaker"):
        # explicit split/speaker: two people showing up at all is enough; auto wants them most of the time
        pair = two_speakers(samples, crop_w / src_w, min_share=None if mode == "auto" else 0.15)
        layout = settings.two_speaker_layout if mode == "auto" else mode
        lead = settings.reframe_lead * crop_w / src_w
        if pair and layout == "speaker":
            path = active_speaker_path(samples, speaker_activity(samples, *pair), *pair, lead=lead)
            return ReframePlan("speaker", src_w, src_h, crop_w, to_keyframes(path, src_w, crop_w, end - start),
                               fit=no_face_spans(samples, end - start) if fit_on else [])
        if pair:
            return ReframePlan("split", src_w, src_h, crop_w,
                               panels=split_panels(*pair, src_w, src_h, panel=(size[0], size[1] // 2)))

    if fit_on:  # per shot: faces → tracked crop, none → the whole frame over a blurred copy
        spans = no_face_spans(samples, end - start) if samples else [(0.0, end - start)]
        keys = to_keyframes(smooth(samples, lead=settings.reframe_lead * crop_w / src_w), src_w, crop_w, end - start)
        if not keys and follow is not None:
            keys = to_keyframes([Sample(0.0, follow)], src_w, crop_w, end - start)
        if not keys or sum(b - a for a, b in spans) >= end - start - 1e-6:
            return ReframePlan("fit", src_w, src_h, crop_w)
        return ReframePlan("face", src_w, src_h, crop_w, keys, fit=spans)

    found = sum(s.cx is not None for s in samples)
    if not samples or found / len(samples) < settings.face_min_coverage:
        if follow is not None:  # you chose this person: keep the crop on them rather than the centre
            return ReframePlan("face", src_w, src_h, crop_w,
                               to_keyframes([Sample(0.0, follow)], src_w, crop_w, end - start))
        return ReframePlan("center", src_w, src_h, crop_w)

    keys = to_keyframes(smooth(samples, lead=settings.reframe_lead * crop_w / src_w), src_w, crop_w, end - start)
    return ReframePlan("face", src_w, src_h, crop_w, keys)


# ------------------------------------------------------------------ who's in the clip (choose who to follow)

PEOPLE_FRAMES = 8
PERSON_GAP = 0.1  # faces further apart than this (fraction of width) are different people


def people(video: Path, start: float, end: float, detector: FaceDetector | None = None,
           frames: int = PEOPLE_FRAMES) -> list[dict]:
    """Distinct faces across a few frames of the clip, left to right: x (0..1, what `follow` takes), share of frames
    they're in, size, and a small JPEG of the face (bytes) so the UI can show who is who."""
    import cv2

    detector = detector or get_detector()
    cap = cv2.VideoCapture(str(video))
    seen: list[tuple[float, float, int, object]] = []  # (cx, area, frame no, crop jpeg bytes)
    n = 0
    for k in range(frames):
        cap.set(cv2.CAP_PROP_POS_MSEC, (start + (k + 0.5) * (end - start) / frames) * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        n += 1
        h, w = frame.shape[:2]
        scale = DETECT_WIDTH / w
        for f in detector.detect(cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))):
            cy, fh = (f[3], f[4]) if len(f) > 4 else (0.4, 0.25)
            side = int(min(fh * h * 1.7, h, w))
            x0 = int(min(max(f[0] * w - side / 2, 0), w - side))
            y0 = int(min(max(cy * h - side / 2, 0), h - side))
            thumb = cv2.resize(frame[y0:y0 + side, x0:x0 + side], (160, 160))
            ok, jpg = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
            seen.append((float(f[0]), float(f[1]), k, jpg.tobytes() if ok else b""))
    cap.release()
    groups: list[list] = []
    for face in sorted(seen, key=lambda x: x[0]):
        if groups and face[0] - groups[-1][-1][0] <= PERSON_GAP:
            groups[-1].append(face)
        else:
            groups.append([face])
    out = []
    for g in groups:
        frames_in = len({x[2] for x in g})
        if frames_in < max(1, n // 4):  # a face in one frame out of eight: a poster, a passer-by, a false hit
            continue
        xs = sorted(x[0] for x in g)
        best = max(g, key=lambda x: x[1])
        out.append({"x": round(xs[len(xs) // 2], 3), "share": round(frames_in / max(n, 1), 2),
                    "size": round(best[1], 4), "jpeg": best[3]})
    return out
