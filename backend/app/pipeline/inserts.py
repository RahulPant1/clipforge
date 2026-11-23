"""Inserts: motion-graphics cards burned into a short at the moment something is said.

stat_counter (the first template): a spoken number counts up with a short label under it, e.g. "40%" / "of
startups fail". Everything here is pure — which numbers qualify (candidates), when a card shows (timing, in OUTPUT
time through CutPlan.remap) and where it sits (placement, clear of the tracked face, the hook card and captions).
The frames are drawn by insert_render.py; render.py overlays them.

Numbers always come from the transcript (numbers.py), never from a model: suggest() shows Gemma the candidates
by id and it only answers which ones are worth a card and a label made of words from that sentence.
"""
import logging
import re
from dataclasses import dataclass

from ..config import settings
from ..models import Clip, Insert, Segment, Word
from .highlights import _ends_sentence
from .jumpcut import CutPlan, clean_removed, clip_words, is_struck, norm
from .llm import LLM
from .numbers import Num, clean, find

log = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ Gemma: which numbers, what label

MAX_CANDS = 8  # numbers shown to Gemma per clip (best by score, then in the order they're said)
RULE_MIN_SCORE = 3  # no Gemma answer: only numbers with a unit or ≥ 100 (score) get a card
MAX_KEPT = 8  # Clip.inserts max_length

PROMPT = """You choose on-screen stat cards for a YouTube Short. A card shows a number that is SAID in the short,
big, with a short label under it, at the moment it is said.

Numbers said in this short (id: number - the sentence it is in):
{cands}

For each number worth a card, give its id, how much the card adds (worth 1-10) and a label.
Rules:
- worth 8-10: a striking figure the viewer should remember (a percentage, money, a big count, a result).
  worth 1-5: incidental (a count of things, an age, a time, a step number, a figure that is just mentioned).
- label: 3-6 words read right after the number that finish the thought, e.g. for "40%" in "about 40% of new
  startups fail in their first year" -> "of startups fail in year one" is wrong (changed words), "of new startups
  fail" is right. Use ONLY words from its sentence, in order (leave words out, never add or change any).
  Do not repeat the number.
- Never invent numbers or facts. Leave out ids that aren't worth a card.
- Same language as the sentence."""

SCHEMA = {
    "type": "object",
    "properties": {"cards": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "worth": {"type": "integer"}, "label": {"type": "string"}},
        "required": ["id", "worth", "label"]}}},
    "required": ["cards"],
}


def build_prompt(cands: list[Cand]) -> str:
    return PROMPT.format(cands="\n".join(f"{i}: {c.num.text()} - \"{c.sentence[:300]}\""
                                         for i, c in enumerate(cands, 1)))


def label_ok(label: str, c: Cand) -> bool:
    """Every word of the label was said in the number's sentence (case/punctuation aside), 1-6 words, and it isn't
    just the number again. This is what keeps a model-written label from adding facts."""
    said = {norm(w) for w in c.sentence.split()}
    got = [norm(w) for w in label.split() if norm(w)]
    number = {norm(w) for w in c.num.spoken.split()} | {norm(c.num.text())}
    return 1 <= len(got) <= 6 and all(w in said for w in got) and not all(w in number for w in got)


def without_number(label: str, c: Cand) -> str:
    """Gemma likes to repeat the number ("70% of the wealth"); the card already shows it big."""
    number = {norm(w) for w in c.num.spoken.split()} | {norm(c.num.text())}
    words = [w for w in label.split() if norm(w) not in number and not re.fullmatch(r"[\d.,%₹$€£x]+", w)]
    return " ".join(words)


def _tidy(label) -> str:
    return re.sub(r"[,;:.!?]+$", "", " ".join(str(label or "").replace("*", "").split()).strip("\"“”' "))[:40]


def suggest(clip: Clip, segs: list[Segment], llm: LLM, approve: bool = False) -> list[Insert]:
    """Cards worth showing in this clip (≤ INSERTS_MAX, best first). No numbers → [] without asking Gemma.
    Gemma failing or answering nonsense → the best rule-scored numbers with rule labels (never fails the job)."""
    cands = sorted(candidates(segs, clip)[:MAX_CANDS], key=lambda c: c.t)
    if not cands:
        return []
    picked: list[tuple[int, Cand, str]] = []  # (worth, cand, label)
    try:
        raw = llm.chat_json(build_prompt(cands), SCHEMA, think=settings.content_think)
        cards = raw.get("cards") if isinstance(raw, dict) else None
        if not isinstance(cards, list):
            raise ValueError(f"no cards list in {str(raw)[:120]}")
        seen = set()
        for card in cards:
            if not isinstance(card, dict):
                continue
            try:
                i, worth = int(card.get("id")), int(card.get("worth"))
            except (TypeError, ValueError):
                continue
            if not 1 <= i <= len(cands) or i in seen or worth < settings.insert_min_worth:
                continue
            seen.add(i)
            c = cands[i - 1]
            label = without_number(_tidy(card.get("label")), c)
            if not label_ok(label, c):
                log.info("stat card label %r isn't from its sentence — using the words after the number", label)
                label = rule_label(c)
            picked.append((min(worth, 10), c, label))
    except Exception as e:  # one bad Gemma answer never costs the short its cards
        log.warning("stat card picks failed for clip %s (%s) — picking by rule", clip.index, e)
        picked = [(c.score, c, rule_label(c)) for c in cands if c.score >= RULE_MIN_SCORE]
    picked.sort(key=lambda x: (-x[0], -x[1].score, x[1].t))
    out: list[Insert] = []
    for _, c, label in picked:
        if all(abs(c.t - o.t) >= GAP for o in out):
            out.append(from_candidate(c, label, approved=approve))
        if len(out) >= settings.inserts_max:
            break
    return sorted(out, key=lambda x: x.t)


def merge(clip: Clip, new: list[Insert]) -> list[Insert]:
    """Fresh suggestions in place of the old ones; approved and hand-made cards always stay (and win a clash)."""
    keep = [i for i in clip.inserts if i.approved or i.custom]
    out = keep + [n for n in new if all(abs(n.t - k.t) >= 0.3 for k in keep)]
    return sorted(out, key=lambda x: x.t)[:MAX_KEPT]


def forget_changed(clip: Clip, spans: list[tuple[float, float]]) -> bool:
    """Transcript words changed in `spans` (SOURCE): suggested cards there may show the old number → dropped (and
    asked again on the next produce). Approved / hand-made ones are yours: kept as they are. True if any dropped."""
    gone = [i for i in clip.inserts if not (i.approved or i.custom) and any(a - 0.1 <= i.t < b + 0.1 for a, b in spans)]
    if gone:
        clip.inserts = [i for i in clip.inserts if i not in gone]
        clip.inserts_checked = False
    return bool(gone)


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
