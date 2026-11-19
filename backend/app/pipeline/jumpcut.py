"""Silence + filler-word removal using Whisper word timestamps.

Produces "keep" intervals (relative to the clip start) and remaps word timings onto the
shortened output timeline so captions stay in sync after the cuts.
"""
import re
from dataclasses import dataclass

from ..config import settings
from ..models import Segment, Word

_PUNCT = re.compile(r"[^\w']+")


def norm(word: str) -> str:
    return _PUNCT.sub("", word).lower()


def is_filler(word: str) -> bool:
    return norm(word) in settings.filler_words


@dataclass
class CutPlan:
    keeps: list[tuple[float, float]]  # relative to clip start, in source time
    words: list[Word]  # kept words, timed on the OUTPUT timeline
    source_duration: float

    @property
    def duration(self) -> float:
        return sum(e - s for s, e in self.keeps)

    @property
    def removed(self) -> float:
        return max(self.source_duration - self.duration, 0.0)

    def remap(self, t: float) -> float:
        """Source-relative time -> output time. Times inside a removed gap snap to the next keep."""
        out = 0.0
        for s, e in self.keeps:
            if t < s:
                return out
            if t <= e:
                return out + (t - s)
            out += e - s
        return out


def clip_words(segs: list[Segment], start: float, end: float) -> list[Word]:
    return [w for s in segs for w in s.words if w.start >= start - 0.05 and w.end <= end + 0.05]


def plan_cuts(segs: list[Segment], start: float, end: float, enabled: bool | None = None) -> CutPlan:
    enabled = settings.jumpcut_enabled if enabled is None else enabled
    total = end - start
    words = clip_words(segs, start, end)
    rel = [Word(start=max(w.start - start, 0), end=min(w.end - start, total), word=w.word) for w in words]

    spoken = [w for w in rel if not is_filler(w.word)] if enabled else rel
    if not enabled or not spoken:
        plan = CutPlan(keeps=[(0.0, round(total, 3))], words=[], source_duration=total)
        plan.words = _remap_words(plan, spoken)
        return plan

    pad, max_pause = settings.pause_padding, settings.max_pause
    keeps: list[list[float]] = []
    for w in spoken:
        s, e = max(w.start - pad, 0.0), min(w.end + pad, total)
        if keeps and s - keeps[-1][1] < max_pause:
            keeps[-1][1] = max(keeps[-1][1], e)  # short pause: keep it, merge
        else:
            keeps.append([s, e])
    merged = snap_to_frames([(s, e) for s, e in keeps if e - s > 0.05], settings.output_fps, total)

    plan = CutPlan(keeps=merged, words=[], source_duration=total)
    plan.words = _remap_words(plan, spoken)
    return plan


def snap_to_frames(keeps: list[tuple[float, float]], fps: int, total: float) -> list[tuple[float, float]]:
    """Move every cut onto the output frame grid (multiples of 1/fps).

    Video keeps whole frames, audio is trimmed to the exact same instants, so each segment is exactly
    N frames in both streams and A/V never drifts, however many cuts there are. Overlaps created by
    rounding are merged; segments shorter than one frame are dropped.
    """
    last = int(total * fps + 1e-9)  # frames available in the clip: n/fps < total
    out: list[list[int]] = []
    for s, e in keeps:
        a, b = max(round(s * fps), 0), min(round(e * fps), last)
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a / fps, b / fps) for a, b in out]


def _remap_words(plan: CutPlan, words: list[Word]) -> list[Word]:
    out = []
    for w in words:
        s, e = plan.remap(w.start), plan.remap(w.end)
        if e > s:
            out.append(Word(start=s, end=e, word=w.word))
    return out


def video_select_expr(keeps: list[tuple[float, float]], fps: int) -> str:
    """`select` expression keeping frames with s <= t < e (keeps are on the 1/fps grid, see snap_to_frames).

    Bounds sit half a frame early so float noise in frame times can't add or drop a frame:
    each segment yields exactly (e - s) * fps frames.
    """
    h = 0.5 / fps
    return "+".join(f"between(t,{s - h:.6f},{e - h:.6f})" for s, e in keeps)


def audio_cut_graph(keeps: list[tuple[float, float]], crossfade: float = 0.0) -> str:
    """-af graph cutting audio to exactly the same intervals as the video.

    Not `aselect`: in FFmpeg 8.0 and 8.0.1 (Ubuntu 26.04's package) it is a silent no-op — upstream commit
    565d14e4ff "f_select: Added activate for aselect" — which left audio uncut and out of sync with video.
    asplit/atrim/concat behave the same on every FFmpeg version and audio is small, so buffering is cheap.

    `crossfade` (seconds) blends each cut instead of butting waveforms together (which clicks). Segment i is
    trimmed `d` past its end — into audio that is being removed anyway — and `acrossfade` overlaps that tail
    with the start of segment i+1. The overlap eats exactly the extra `d`, so every kept segment still starts
    at the same output time and the total length is unchanged: A/V sync is untouched.
    """
    n = len(keeps)
    # per-cut fade length: never longer than the removed gap (we borrow audio from it) or the next segment
    fades = [min(crossfade, max(keeps[i + 1][0] - keeps[i][1], 0.0), keeps[i + 1][1] - keeps[i + 1][0])
             for i in range(n - 1)]
    fades = [d if d >= 0.001 else 0.0 for d in fades]
    ends = [e + (fades[i] if i < n - 1 else 0.0) for i, (_, e) in enumerate(keeps)]
    trims = [f"atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS" for (s, _), e in zip(keeps, ends, strict=True)]
    if n == 1:
        return trims[0]
    if not any(fades):
        return ";".join([
            f"asplit={n}" + "".join(f"[a{i}]" for i in range(n)),
            *(f"[a{i}]{t}[b{i}]" for i, t in enumerate(trims)),
            "".join(f"[b{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1",
        ])
    parts = [f"asplit={n}" + "".join(f"[a{i}]" for i in range(n)),
             *(f"[a{i}]{t}[b{i}]" for i, t in enumerate(trims))]
    prev = "b0"
    for i, d in enumerate(fades, start=1):
        join = f"acrossfade=d={d:.6f}" if d else "concat=n=2:v=0:a=1"
        out = f"[c{i}]" if i < n - 1 else ""  # last link is the graph output
        parts.append(f"[{prev}][b{i}]{join}{out}")
        prev = f"c{i}"
    return ";".join(parts)
