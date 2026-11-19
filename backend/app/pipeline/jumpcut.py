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
    merged = [(round(s, 3), round(e, 3)) for s, e in keeps if e - s > 0.05]

    plan = CutPlan(keeps=merged, words=[], source_duration=total)
    plan.words = _remap_words(plan, spoken)
    return plan


def _remap_words(plan: CutPlan, words: list[Word]) -> list[Word]:
    out = []
    for w in words:
        s, e = plan.remap(w.start), plan.remap(w.end)
        if e > s:
            out.append(Word(start=s, end=e, word=w.word))
    return out


def select_expr(keeps: list[tuple[float, float]]) -> str:
    """ffmpeg select/aselect expression keeping only the given intervals."""
    return "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keeps)
