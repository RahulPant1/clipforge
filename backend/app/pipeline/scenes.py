"""Scene index: what is on screen, scene by scene, so clip selection can see as well as read.

1. scan(): scene cuts from one pass of tiny grey frames (nospeech.frame_signals) + face share from a few frames
2. worth_describing(): single-camera talking heads (few cuts, a face in most frames) gain nothing from a vision
   model ("a man talking") — SCENE_INDEX=auto skips them; screens, demos, b-roll and edited videos are described
3. spans(): scenes split at cuts, short ones merged, long ones split (≤ SCENE_SPAN s), ≤ SCENE_MAX spans
4. describe(): one keyframe per span → vision Gemma: one sentence + what kind of shot it is
   (a failed frame is left undescribed; no vision model → no descriptions; never fails the job)

Saved as scenes.json in the job folder (cached; retries reuse it). highlights.build_prompt() puts the described
scenes (not plain talking-head shots) between the transcript lines as ON SCREEN lines; the Transcript step shows
them and searches them.
"""
import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import settings
from .llm import LLM
from .nospeech import frame_signals, has_vision, probe_duration

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

VERSION = 1
FACE_SAMPLES = 16
FACE_MIN_AREA = 0.004  # of the frame: smaller faces are crowds / background, not a presenter
KINDS = ("talking_head", "screen", "slides", "demo", "product", "people", "outdoor", "text", "other")
QUIET = {"talking_head"}  # kinds left out of the selection prompt: they say nothing the transcript doesn't
FRAME_W = 448

PROMPT = """This is one frame from a video{title}, at {at}.

Describe what is ON SCREEN for a video editor who can only read the transcript:
- description: one plain sentence, max 25 words, about what is visible and happening — objects, actions, what a
  screen or slide shows (app names, chart type, headings you can read). Not "the frame shows". Describe only
  what you can see; never guess names, places or numbers that are not visible.
- kind: talking_head (a person speaking to camera or in conversation, nothing else of note), screen (a computer
  or phone screen / screen recording), slides (a presentation slide or chart), demo (hands showing or using
  something), product (an object shown off), people (several people / an event), outdoor (landscape, street,
  action outside), text (title card, captions, credits), other."""

SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)}},
    "required": ["description", "kind"],
}


@dataclass
class Scene:
    start: float
    end: float
    t: float  # where the keyframe was taken
    description: str = ""
    kind: str = ""  # one of KINDS, "" = not described


@dataclass
class SceneIndex:
    mode: str  # described | skipped | off
    reason: str = ""
    duration: float = 0.0
    cuts_per_min: float = 0.0
    face_share: float = 0.0
    model: str | None = None
    cuts: list[float] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    version: int = VERSION

    @property
    def described(self) -> list[Scene]:
        return [s for s in self.scenes if s.description]

    def for_prompt(self) -> list[Scene]:
        """Scenes worth a line in the selection prompt: described and not a plain talking head."""
        return [s for s in self.described if s.kind not in QUIET]


# ------------------------------------------------------------------ scan

def face_share(video: Path, duration: float, n: int = FACE_SAMPLES) -> float:
    """Share of n evenly spread frames with a presenter-sized face (YuNet, Haar fallback)."""
    import cv2
    import numpy as np

    from .reframe import get_detector

    det, hits, seen = get_detector(), 0, 0
    for k in range(n):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{duration * (k + 0.5) / n:.2f}", "-i", str(video),
                              "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True).stdout
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
        if img is None:
            continue
        seen += 1
        hits += any(f[1] >= FACE_MIN_AREA for f in det.detect(img))
    return round(hits / seen, 3) if seen else 0.0


def worth_describing(cuts_per_min: float, faces: float) -> tuple[bool, str]:
    """auto mode: is this video more than a talking head? (calibrated on real jobs, see CLAUDE.md)"""
    if cuts_per_min >= settings.scene_skip_cuts_per_min:
        return True, f"{cuts_per_min:.1f} scene cuts a minute"
    if faces < settings.scene_skip_face_share:
        return True, f"a face in only {faces:.0%} of frames"
    return False, f"talking head: {cuts_per_min:.1f} cuts a minute, a face in {faces:.0%} of frames"


# ------------------------------------------------------------------ spans

def spans(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    """Scenes from the cuts: shorter than SCENE_MIN merged into the one before, longer than SCENE_SPAN split evenly,
    and at most SCENE_MAX in all (a fast-cut video gets longer spans rather than hundreds of frames)."""
    if duration <= 0:
        return []
    edges = [0.0, *[c for c in sorted(cuts) if 0 < c < duration], duration]
    merged: list[list[float]] = []
    for a, b in zip(edges, edges[1:], strict=False):
        if merged and (b - a < settings.scene_min or merged[-1][1] - merged[-1][0] < settings.scene_min):
            merged[-1][1] = b
        else:
            merged.append([a, b])
    span = max(settings.scene_span, duration / max(settings.scene_max, 1))
    out: list[tuple[float, float]] = []
    for a, b in merged:
        k = max(1, round((b - a) / span))
        step = (b - a) / k
        out += [(round(a + i * step, 2), round(a + (i + 1) * step, 2)) for i in range(k)]
    while len(out) > settings.scene_max:  # many scenes just over span: join neighbours pairwise
        out = [(out[i][0], out[min(i + 1, len(out) - 1)][1]) for i in range(0, len(out), 2)]
    return out


# ------------------------------------------------------------------ describe

def keyframe(video: Path, t: float, width: int = FRAME_W) -> bytes:
    res = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                         capture_output=True)
    return res.stdout if res.returncode == 0 else b""


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02}"


def describe_one(frame: bytes, at: float, llm: LLM, title: str = "") -> tuple[str, str]:
    raw = llm.chat_json(PROMPT.format(title=f' ("{title[:100]}")' if title else "", at=_mmss(at)), SCHEMA,
                        think=settings.vision_think, images=[frame])
    if not isinstance(raw, dict):
        raise ValueError(f"not an object: {str(raw)[:100]}")
    text = " ".join(str(raw.get("description") or "").replace("*", "").split()).strip().strip('"')
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    if not text:
        raise ValueError("empty description")
    return text[:200], kind


GIVE_UP = 4  # this many failures in a row before any success → the model can't do it; stop asking


THUMB_W = 320


def thumb_path(job_dir: Path, k: int) -> Path:
    return job_dir / "scenes" / f"scene_{k}.jpg"


def thumb(job_dir: Path, video: Path, index: "SceneIndex", k: int) -> Path | None:
    """Scene k's keyframe as a small JPEG (saved while describing; made once on request for older indexes)."""
    if not 0 <= k < len(index.scenes):
        return None
    f = thumb_path(job_dir, k)
    if not f.is_file():
        data = keyframe(video, index.scenes[k].t, THUMB_W)
        if not data:
            return None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    return f


def describe(video: Path, scenes: list[Scene], llm: LLM, on_progress: ProgressFn | None = None,
             title: str = "", thumbs: Path | None = None) -> int:
    """Fills scene.description/kind in place; returns how many were described. thumbs: job dir to keep each
    keyframe as the scene's thumbnail (scenes/scene_K.jpg)."""
    done, fails = 0, 0
    for i, s in enumerate(scenes):
        if on_progress:
            on_progress(i / max(len(scenes), 1), f"Gemma looking at scene {i + 1}/{len(scenes)}")
        try:
            frame = keyframe(video, s.t)
            if not frame:
                raise ValueError("no frame")
            if thumbs is not None:
                thumb_path(thumbs, i).parent.mkdir(parents=True, exist_ok=True)
                thumb_path(thumbs, i).write_bytes(frame)
            s.description, s.kind = describe_one(frame, s.t, llm, title)
            done += 1
        except Exception as e:  # one bad frame never fails the job
            fails += 1
            log.warning("scene %.0f-%.0f s not described: %s", s.start, s.end, e)
            if not done and fails >= GIVE_UP:
                log.warning("vision model failed %d times in a row — no scene descriptions for this video", fails)
                break
    return done


# ------------------------------------------------------------------ build / load

def load(job_dir: Path) -> SceneIndex | None:
    f = job_dir / "scenes.json"
    if not f.is_file():
        return None
    try:
        raw = json.loads(f.read_text())
        if raw.get("version") != VERSION:
            return None
        raw["scenes"] = [Scene(**s) for s in raw.get("scenes", [])]
        return SceneIndex(**raw)
    except (ValueError, TypeError, KeyError):
        return None


def save(index: SceneIndex, job_dir: Path) -> None:
    (job_dir / "scenes.json").write_text(json.dumps(asdict(index), indent=1, ensure_ascii=False))


def build(video: Path, job_dir: Path, llm: LLM, on_progress: ProgressFn | None = None, title: str = "",
          mode: str | None = None) -> SceneIndex:
    """The job's scene index (cached in scenes.json). mode: off | auto | on (default SCENE_INDEX)."""
    mode = (mode or settings.scene_index).lower()
    if mode == "off":
        return SceneIndex(mode="off", reason="SCENE_INDEX=off")
    cached = load(job_dir)
    if cached and (cached.mode == "described" or mode == "auto"):
        return cached

    def prog(p: float, m: str) -> None:
        if on_progress:
            on_progress(p, m)

    duration = probe_duration(video)
    prog(0.0, "Looking for scene cuts")
    _, cuts = frame_signals(video, duration, lambda p, m: prog(0.3 * p, m))
    per_min = round(len(cuts) / (duration / 60), 2) if duration else 0.0
    faces = face_share(video, duration)
    want, why = worth_describing(per_min, faces) if mode == "auto" else (True, "SCENE_INDEX=on")
    index = SceneIndex(mode="skipped", reason=why, duration=round(duration, 2), cuts_per_min=per_min,
                       face_share=faces, cuts=cuts)
    if want and not has_vision(llm):
        index.reason = f"{why}, but {getattr(llm, 'model', 'the model')} can't see images"
        want = False
    if want:
        index.scenes = [Scene(a, b, round((a + b) / 2, 2)) for a, b in spans(cuts, duration)]
        index.model = getattr(llm, "model", None)
        shutil.rmtree(job_dir / "scenes", ignore_errors=True)  # thumbnails of an older index
        n = describe(video, index.scenes, llm, lambda p, m: prog(0.35 + 0.65 * p, m), title, thumbs=job_dir)
        index.mode = "described" if n else "skipped"
        index.reason = why if n else f"{why}, but no frame could be described"
    save(index, job_dir)
    prog(1.0, f"{len(index.described)} scenes described" if index.mode == "described" else
         f"Scene descriptions skipped ({index.reason})")
    return index
