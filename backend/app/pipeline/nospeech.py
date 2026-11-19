"""No-speech highlights — music, gameplay, sport, nature: videos where the transcript is too thin for Gemma.

1. analyse(): one ffmpeg pass for tiny grey frames (motion, scene cuts) + one for mono audio (loudness)
2. excitement(): per HOP s — loud for this video + getting louder + lots of motion + fast cutting
3. peak_windows(): the strongest peaks, each the PAYOFF of a clip (build-up before it, a little after),
   edges snapped to nearby scene cuts, NO_SPEECH_CLIP_SECONDS long
4. keyframes of each window → vision Gemma: score, title, hook, what's on screen.
   No vision / a failed call → the signal score and a generic title; never fails the job.

No librosa/PySceneDetect: ffmpeg + numpy are already here and are enough for loudness and cuts.
"""
import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import settings
from ..models import Clip, RenderOptions
from .llm import LLM

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

HOP = 0.5  # s per signal value
FRAME_FPS = 4  # analysis frames per second
FRAME_W, FRAME_H = 64, 36
AUDIO_RATE = 8000
CUT_DIFF = 28.0  # mean grey-level change between analysis frames that counts as a scene cut...
CUT_HIST = 0.35  # ...when the brightness histogram changes this much too (L1/2, 0..1)
SNAP_CUT = 3.0  # move a clip edge onto a scene cut up to this far away
PAYOFF_AT = 0.65  # the peak sits this far into the clip: build-up first, then the payoff
KEYFRAMES = (0.1, 0.35, 0.6, 0.85)  # where in a candidate the vision model gets a frame


@dataclass
class Signals:
    duration: float
    loud: list[float] = field(default_factory=list)  # dBFS per HOP
    motion: list[float] = field(default_factory=list)  # mean abs grey change per HOP
    cuts: list[float] = field(default_factory=list)  # scene cut times (s)


# ------------------------------------------------------------------ analysis

def probe_duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(video)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def loudness(video: Path, duration: float) -> list[float]:
    """dBFS per HOP from mono 8 kHz PCM (−90 for silence / no audio track)."""
    res = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(AUDIO_RATE),
                          "-f", "s16le", "-"], capture_output=True)
    n = max(int(duration / HOP), 1)
    pcm = np.frombuffer(res.stdout, dtype="<i2").astype(np.float32) / 32768 if res.returncode == 0 else np.zeros(0)
    hop = int(AUDIO_RATE * HOP)
    out = []
    for i in range(n):
        chunk = pcm[i * hop:(i + 1) * hop]
        rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        out.append(round(max(20 * np.log10(rms + 1e-9), -90.0), 2))
    return out


def frame_signals(video: Path, duration: float, on_progress: ProgressFn | None = None) -> tuple[list[float],
                                                                                                list[float]]:
    """(motion per HOP, scene cut times) from FRAME_FPS tiny grey frames, read as they decode."""
    size = FRAME_W * FRAME_H
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vf",
                             f"fps={FRAME_FPS},scale={FRAME_W}:{FRAME_H},format=gray", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    diffs: list[float] = []
    cuts: list[float] = []
    prev, prev_hist, k = None, None, 0
    expected = max(duration * FRAME_FPS, 1)
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        f = np.frombuffer(buf, np.uint8).astype(np.float32)
        hist = np.histogram(f, bins=16, range=(0, 256))[0] / size
        if prev is not None:
            d = float(np.abs(f - prev).mean())
            if d > CUT_DIFF and float(np.abs(hist - prev_hist).sum()) / 2 > CUT_HIST:
                cuts.append(round(k / FRAME_FPS, 3))
                d = 0.0  # a cut isn't motion
            diffs.append(d)
        else:
            diffs.append(0.0)
        prev, prev_hist = f, hist
        k += 1
        if on_progress and k % (FRAME_FPS * 30) == 0:
            on_progress(min(k / expected, 1.0), f"Scanning picture {int(k / FRAME_FPS)}/{int(duration)} s")
    proc.wait()
    per = int(FRAME_FPS * HOP)
    motion = [round(float(np.mean(diffs[i:i + per])), 3) for i in range(0, len(diffs), per)] if diffs else []
    return motion, cuts


def analyse(video: Path, cache: Path | None = None, on_progress: ProgressFn | None = None) -> Signals:
    if cache and cache.exists():
        try:
            return Signals(**json.loads(cache.read_text()))
        except (ValueError, TypeError):
            pass
    duration = probe_duration(video)
    motion, cuts = frame_signals(video, duration, on_progress)
    sig = Signals(duration=duration, loud=loudness(video, duration), motion=motion, cuts=cuts)
    if cache:
        cache.write_text(json.dumps(sig.__dict__))
    return sig


# ------------------------------------------------------------------ scoring

def _robust_z(x: np.ndarray) -> np.ndarray:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return np.clip((x - med) / (mad if mad > 1e-6 else 1.0), -3, 3)


def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    k = max(int(seconds / HOP), 1)
    return np.convolve(x, np.ones(k) / k, mode="same") if len(x) >= k else x


def excitement(sig: Signals) -> np.ndarray:
    """Per HOP: loud (for this video) 40%, getting louder 25%, motion 25%, cut rate 10%; smoothed over 2 s."""
    n = max(len(sig.loud), len(sig.motion))
    if n == 0:
        return np.zeros(0)
    loud = np.full(n, -90.0)
    loud[:len(sig.loud)] = sig.loud
    motion = np.zeros(n)
    motion[:len(sig.motion)] = sig.motion
    back = int(3 / HOP)
    before = np.array([loud[max(i - back, 0):i].mean() if i else loud[0] for i in range(n)])
    rise = np.maximum(loud - before, 0)
    cut_rate = np.zeros(n)
    for c in sig.cuts:
        lo, hi = int((c - 2.5) / HOP), int((c + 2.5) / HOP)
        cut_rate[max(lo, 0):min(hi, n)] += 1
    score = (0.4 * _robust_z(loud) + 0.25 * _robust_z(rise) + 0.25 * _robust_z(np.log1p(motion))
             + 0.1 * _robust_z(cut_rate))
    return _smooth(score, 2.0)


def _snap(t: float, cuts: list[float], lo: float, hi: float) -> float:
    near = [c for c in cuts if abs(c - t) <= SNAP_CUT and lo <= c <= hi]
    return min(near, key=lambda c: abs(c - t)) if near else t


def peak_windows(sig: Signals, exc: np.ndarray, count: int,
                 length: float | None = None) -> list[tuple[float, float, float]]:
    """Up to `count` non-overlapping (start, end, peak score), best first.

    Each window puts its peak PAYOFF_AT of the way in, then each edge moves to a scene cut within SNAP_CUT s
    when that keeps the length within min/max clip seconds.
    """
    length = min(max(length or settings.no_speech_clip_seconds, settings.min_clip_seconds),
                 settings.max_clip_seconds)
    dur = sig.duration or len(exc) * HOP
    if dur < settings.min_clip_seconds or len(exc) == 0:
        return []
    length = min(length, dur)
    taken: list[tuple[float, float]] = []
    out = []
    for i in np.argsort(-exc):
        if len(out) >= count:
            break
        peak = (i + 0.5) * HOP
        start = min(max(peak - PAYOFF_AT * length, 0.0), dur - length)
        end = start + length
        s2 = _snap(start, sig.cuts, max(end - settings.max_clip_seconds, 0), end - settings.min_clip_seconds)
        e2 = _snap(end, sig.cuts, s2 + settings.min_clip_seconds, min(s2 + settings.max_clip_seconds, dur))
        if not s2 < peak < e2:
            s2, e2 = start, end
        if any(s2 < b and e2 > a for a, b in taken):
            continue
        taken.append((s2, e2))
        out.append((round(s2, 2), round(e2, 2), float(exc[i])))
    return out


# ------------------------------------------------------------------ vision

VISION_PROMPT = """These {k} frames are in order from a {dur:.0f}-second stretch of the video "{title}". \
It has no speech (music, gameplay, sport, nature or similar).

Judge it as a standalone YouTube Short: would it stop someone scrolling?
- mostly_text: true if most frames are text, credits, titles, logos, menus or black/blank screens
- score: 1-10. Be strict: an ordinary scene where nothing special happens is 4-5. 7-8 = clear action, a \
reveal, a funny or beautiful moment. 9-10 = rare, unmissable. Text/credits/black = 1-2.
- title: max 8 words, catchy, about what is visible
- hook: max 6 words for a title card at the start
- description: one sentence of what happens on screen, as the action itself (not "the frames show...")
Describe only what you can see. Never guess names, places or numbers that are not visible."""

VISION_SCHEMA = {
    "type": "object",
    "properties": {"mostly_text": {"type": "boolean"}, "score": {"type": "integer"}, "title": {"type": "string"},
                   "hook": {"type": "string"}, "description": {"type": "string"}},
    "required": ["mostly_text", "score", "title", "hook", "description"],
}
TEXT_SCORE = 2  # cap for credits / title cards / black, whatever score the model gave


def keyframes(video: Path, start: float, end: float, width: int = 448) -> list[bytes]:
    out = []
    for f in KEYFRAMES:
        res = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start + f * (end - start):.2f}", "-i", str(video),
                              "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe",
                              "-vcodec", "mjpeg", "-"], capture_output=True)
        if res.returncode == 0 and res.stdout:
            out.append(res.stdout)
    return out


def has_vision(llm: LLM) -> bool:
    caps = getattr(llm, "capabilities", None)
    if caps is None:
        return True  # can't tell (fakes, other clients): just try
    try:
        found = caps()
    except Exception:
        return True
    return not found or "vision" in found  # older Ollama doesn't report capabilities


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02}"


def select_no_speech(video: Path, n: int, llm: LLM, on_progress: ProgressFn | None = None,
                     keep: int | None = None, *, title: str = "", cache: Path | None = None,
                     trace: dict | None = None) -> list[Clip]:
    """Up to `keep` (default n) non-overlapping candidates from audio/visual peaks; the best `n` are pre-approved."""
    want = max(keep or n, n)

    def prog(p: float, m: str) -> None:
        if on_progress:
            on_progress(p, m)

    sig = analyse(video, cache, lambda p, m: prog(0.5 * p, m))
    exc = excitement(sig)
    pool = peak_windows(sig, exc, want + max(2, want // 2))  # a few spare: vision may sink some
    vision = has_vision(llm) and settings.no_speech_vision
    items, clips = [], []
    for i, (s, end, sig_score) in enumerate(pool):
        prog(0.5 + 0.5 * i / max(len(pool), 1), f"Gemma watching candidate {i + 1}/{len(pool)}" if vision
             else f"Scoring candidate {i + 1}/{len(pool)}")
        rec: dict = {"start": s, "end": end, "signal": round(sig_score, 2)}
        items.append(rec)
        clip = Clip(start=s, end=end, title=f"Highlight at {_mmss(s)}", hook="", keywords=[],
                    score=max(1, min(10, round(5 + 1.5 * sig_score))),
                    options=RenderOptions(jumpcut=False, caption_style="none"))  # no speech: nothing to cut/caption
        if vision:
            try:
                frames = keyframes(video, s, end)
                raw = llm.chat_json(VISION_PROMPT.format(k=len(frames), dur=end - s, title=title or "untitled"),
                                    VISION_SCHEMA, think=settings.vision_think, images=frames)
                clip.score = max(1, min(10, int(raw["score"])))
                if raw.get("mostly_text") is True:
                    clip.score = min(clip.score, TEXT_SCORE)
                clip.title = str(raw["title"]).strip()[:80] or clip.title
                clip.hook = str(raw["hook"]).strip()[:60]
                clip.visual = str(raw.get("description", "")).strip()[:300]
                rec.update(vision=raw)
            except Exception as e:  # one bad call (or a model without vision) mustn't fail the job
                log.warning("vision scoring skipped for %.0f-%.0f s: %s", s, end, e)
                rec["error"] = str(e)[:300]
        clips.append((clip, sig_score))
    # vision score first, signal strength breaks ties
    ranked = [c for c, _ in sorted(clips, key=lambda x: (-x[0].score, -x[1]))][:want]
    best = {id(c) for c in ranked[:n]}
    for c in ranked:
        c.approved = id(c) in best
    prog(1.0, f"{len(ranked)} candidate clips found")
    if trace is not None:
        trace.update(mode="no_speech", model=getattr(llm, "model", None), vision=vision, duration=sig.duration,
                     scene_cuts=len(sig.cuts), items=items,
                     clips=[c.model_dump(include={"start", "end", "title", "hook", "score", "visual", "approved"})
                            for c in ranked])
    ranked.sort(key=lambda c: c.start)
    for i, c in enumerate(ranked, 1):  # same numbering as highlights.dedupe (short_<index>.mp4)
        c.index = i
    return ranked
