"""Inserts: motion-graphics cards burned into a short at the moment something is said.

stat_counter (the first template): a spoken number counts up with a short label under it, e.g. "40%" / "of
startups fail". Everything here is pure — which numbers qualify (candidates), when a card shows (timing, in OUTPUT
time through CutPlan.remap) and where it sits (placement, clear of the tracked face, the hook card and captions).
The frames are drawn by insert_render.py; render.py overlays them.

Numbers always come from the transcript (numbers.py), never from a model.
"""
import re
from dataclasses import dataclass

from ..config import settings
from ..models import Clip, Insert, Segment, Word
from .highlights import _ends_sentence
from .jumpcut import CutPlan, clean_removed, clip_words, is_struck, norm
from .numbers import Num, clean, find

LEAD = 0.15  # s: the card starts just before the number is heard
GAP = 3.0  # s between two cards' starts (output time)
MIN_SHOWN = 1.0  # shorter than this after trims/cuts → left out
BOX = (840, 340)  # card canvas on 1080x1920; feed versions scale it with the height (a 1:1 shows the face
                  # mid-frame — a caption-sized card would cover it)
CAPTION_CLEAR = 230  # px above the bottom captions' margin: two caption lines + the emoji gap
LABEL_WORDS = 4
_LEAD_WORDS = {"and", "but", "so", "or", "then", "because", "like", "um", "uh"}


@dataclass
class Cand:
    num: Num
    t: float  # SOURCE start of the number's first word
    sentence: str  # the sentence it was said in (what Gemma and the label check read)
    after: list[str]  # words after the number, up to the sentence end (rule label)
    score: float


def _sentences(words: list[Word]) -> list[tuple[int, int]]:
    out, s = [], 0
    for i, w in enumerate(words):
        if _ends_sentence(w.word):
            out.append((s, i + 1))
            s = i + 1
    if s < len(words):
        out.append((s, len(words)))
    return out


def candidates(segs: list[Segment], clip: Clip) -> list[Cand]:
    """Stat-worthy numbers said in the clip (struck words excluded), best first."""
    removed = clean_removed(clip.removed)
    words = clip_words(segs, clip.start, clip.end)
    keys = {norm(k) for k in clip.keywords}
    out = []
    for a, b in _sentences(words):
        part = words[a:b]
        for n in find(part):
            if any(is_struck(w, removed) for w in part[n.start:n.end]):
                continue
            sentence = "".join(w.word for w in part).strip()
            after = [w.word.strip() for w in part[n.end:]]
            score = (3 if n.has_unit else 0) + (2 if n.value >= 10 else 0) + (1 if n.value >= 100 else 0)
            score += sum(norm(w.word) in keys for w in part)
            out.append(Cand(n, part[n.start].start, sentence, [x for x in after if clean(x)], score))
    return sorted(out, key=lambda c: (-c.score, c.t))


def rule_label(c: Cand) -> str:
    """A label from the words right after the number ("40% | of startups fail"), up to a comma / LABEL_WORDS."""
    words: list[str] = []
    for raw in c.after:
        if not words and clean(raw).lower() in _LEAD_WORDS:
            continue
        words.append(raw)
        if len(words) >= LABEL_WORDS or re.search(r"[,;:]$", raw):
            break
    return re.sub(r"[,;:.!?]+$", "", " ".join(words))[:40]


def from_candidate(c: Cand, label: str | None = None, approved: bool = False) -> Insert:
    n = c.num
    return Insert(t=round(c.t, 3), spoken=n.spoken[:80], value=n.value, decimals=n.decimals, prefix=n.prefix,
                  suffix=n.suffix, grouping=n.grouping, label=(rule_label(c) if label is None else label)[:40],
                  approved=approved)


def display(ins: Insert, value: float | None = None) -> str:
    """The card's number text: the final value, or a count-up frame's value in the same format."""
    return Num(0, 0, ins.value, ins.decimals, ins.prefix, ins.suffix, ins.grouping).text(value)


# ------------------------------------------------------------------ timing (output time)

@dataclass
class Timed:
    insert: Insert
    start: float  # OUTPUT seconds
    end: float


def timing(ins: Insert, clip: Clip, plan: CutPlan, words: list[Word]) -> Timed | None:
    """When the card shows in the rendered short, or None (outside the clip after a trim, its number struck,
    too short to read). Starts LEAD before the number, holds to the end of its sentence (min/max seconds)."""
    if not clip.start <= ins.t < clip.end:
        return None
    removed = clean_removed(clip.removed)
    if not ins.custom:  # its number was cut out of the short → no card for it
        said = [w for w in words if abs(w.start - ins.t) < 0.06]
        if said and is_struck(said[0], removed):
            return None
    start = max(plan.remap(ins.t - clip.start) - LEAD, 0.0)
    ends = [w.end for w in words if w.start >= ins.t - 0.06 and _ends_sentence(w.word) and not is_struck(w, removed)]
    said_until = plan.remap(min(ends) - clip.start) if ends else start + settings.insert_max_seconds
    length = min(max(said_until - start, settings.insert_min_seconds), settings.insert_max_seconds)
    end = min(start + length, plan.duration)
    return Timed(ins, round(start, 3), round(end, 3)) if end - start >= MIN_SHOWN else None


def schedule(clip: Clip, plan: CutPlan, segs: list[Segment]) -> list[Timed]:
    """The approved inserts this render shows: timed, in order, ≤ INSERTS_MAX, ≥ GAP apart, never overlapping."""
    words = clip_words(segs, clip.start, clip.end)
    timed = sorted((t for i in clip.inserts if i.approved and (t := timing(i, clip, plan, words))),
                   key=lambda x: x.start)
    out: list[Timed] = []
    for t in timed:
        if len(out) >= settings.inserts_max:
            break
        if out and (t.start - out[-1].start < GAP or t.start < out[-1].end + 0.2):
            continue
        out.append(t)
    return out


# ------------------------------------------------------------------ placement

def box_size(height: int) -> tuple[int, int]:
    s = height / 1920
    return round(BOX[0] * s), round(BOX[1] * s)


def placement(ins: Insert, size: tuple[int, int], *, split: bool, caption_position: str | None,
              captions: bool) -> tuple[int, int]:
    """Top-left (x, y) of the card on a `size` canvas.

    Tracked faces sit in the upper half and the hook card owns the top, so by default the card goes in the band
    above the bottom captions (chest height). Captions in the middle → under them. Two stacked speakers ("split")
    have a face in both halves: the card goes to the top (`upper`), where the hook card sits for its first seconds.
    """
    w, h = size
    bw, bh = box_size(h)
    x = (w - bw) // 2
    where = ins.position if ins.position != "auto" else ("upper" if split else "lower")
    top_margin = round(settings.hook_margin_v * h / 1920)
    if where == "upper":
        return x, top_margin
    margin = round(settings.caption_margin_v * h / 1920)
    clear = round(CAPTION_CLEAR * (h / 1920) ** 0.5)
    if captions and (caption_position or settings.caption_position) == "middle" and not split:
        y = h // 2 + clear // 2 + 20  # below the centred caption lines
        return x, min(y, h - margin - bh)
    y = h - margin - (clear if captions else 0) - bh
    return x, max(y, top_margin)


# ------------------------------------------------------------------ ffmpeg

@dataclass
class Layer:
    input: int  # ffmpeg input index of the frame sequence
    x: int
    y: int
    start: float  # OUTPUT seconds
    end: float


def graph(base: str, layers: list[Layer]) -> tuple[str, str]:
    """filter_complex pieces putting each card's frames (shifted to its start) on [base] → (graph, out label)."""
    parts, cur = [], base
    for k, ly in enumerate(layers):
        parts.append(f"[{ly.input}:v]format=rgba,setpts=PTS-STARTPTS+{ly.start:.3f}/TB[ins{k}]")
        parts.append(f"[{cur}][ins{k}]overlay={ly.x}:{ly.y}:eof_action=pass:"
                     f"enable='between(t,{ly.start:.3f},{ly.end - 0.001:.3f})'[insv{k}]")
        cur = f"insv{k}"
    return ";".join(parts), cur
