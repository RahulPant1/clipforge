"""Clip judge + gap detector: is this candidate publishable as cut, and if not, what exactly is missing?

1. rule_checks(): measured from the transcript, no model — starts mid-sentence, starts on an unexplained "this/it/
   so…", ends mid-sentence, announces "three reasons" and covers fewer.
2. Gemma (one call per clip) sees ~JUDGE_CONTEXT_SECONDS of transcript BEFORE and AFTER the clip and answers yes/no
   checks with evidence: hook, setup present, payoff reached, meaning kept, value, clarity. Small models are bad at
   calibrated 1-10 scores (real candidates averaged 8.6-9.5) but decent at "quote the unexplained words".
   Quotes must be in the transcript; suggested times must be [bracket] times from the context, are snapped to a
   sentence edge and keep the clip ≤ max_clip_seconds — else dropped.
3. verdict(): a rule over the checks — not faithful → reject; no value and no hook → reject; missing setup/payoff
   with a fix that fits → trim; without one → needs_context; else ready.

Never fails a job: a failed Gemma call leaves the rule checks (verdict from those alone, model=None).
"""
import logging
import re

from ..config import settings
from ..models import Clip, ClipFix, ClipReview, ReviewCheck, Segment, Word
from .content import plain
from .highlights import _ends_sentence, sentence_bounds
from .jumpcut import clean_removed, is_struck, norm
from .llm import LLM

log = logging.getLogger(__name__)

REFERENCE_STARTERS = {"this", "that", "these", "those", "it", "its", "they", "them", "he", "she", "so", "and", "but",
                      "because", "also", "then", "which"}  # not "there"/"here": "There are three reasons…"
LIST_RE = re.compile(r"\b(two|three|four|five|six|seven|2|3|4|5|6|7)\s+(?:\w+\s+)?(things|reasons|ways|steps|tips|"
                     r"mistakes|rules|lessons|questions|points|ideas|habits|signs|types|secrets|principles)\b", re.I)
COUNT = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
MARKERS = re.compile(r"\b(first(?:ly)?|second(?:ly)?|third(?:ly)?|fourth|fifth|sixth|seventh|number\s+(?:one|two|"
                     r"three|four|five|six|seven)|finally|lastly)\b", re.I)
SNAP = 4.0  # s: a suggested edge moves at most this far to land on a sentence edge

PROMPT = """You are a strict short-form video editor checking ONE candidate clip before it is published.
The viewer will see THE CLIP alone, nothing before or after it.

Sentences BEFORE the clip (B1 is right before it; not in the clip):
{before}

THE CLIP ({dur:.0f} s):
{clip}

Sentences AFTER the clip (A1 is right after it; not in the clip):
{after}

Answer about THE CLIP:
- setup_lines: the numbers of the B sentences a viewer MUST hear to understand THE CLIP, e.g. [1] or [2, 1].
  [] when it makes sense on its own. A clip that introduces its own topic ("Speaking of email, …", "Tip number
  seven: …") needs none. Only list a B sentence if THE CLIP points back to it ("this approach", "as I said", an
  "it"/"that" whose meaning is only in that sentence).
- setup_why: the words in THE CLIP that need it, or "".
- payoff_lines: the numbers of the A sentences needed for THE CLIP to reach its conclusion / answer / punchline,
  e.g. [1, 2]. [] when it is complete. Only list them if THE CLIP starts something (a question, a list, a story,
  a "here's why") that is finished only in those sentences.
- payoff_why: what is missing without them, or "".
- hook_ok: do its first ~3 seconds give a reason to keep watching?
- faithful: does cutting it here keep the speaker's meaning? false if it drops a "but"/qualification that
  reverses it, or presents a quoted / sarcastic / hypothetical view as the speaker's own. faithful_note: why.
- value_ok: does it give an insight, a laugh or an emotion on its own?
- clear_ok: is it easy to follow from speech alone?
Never invent what was said."""

SCHEMA = {
    "type": "object",
    "properties": {
        "setup_lines": {"type": "array", "items": {"type": "integer"}}, "setup_why": {"type": "string"},
        "payoff_lines": {"type": "array", "items": {"type": "integer"}}, "payoff_why": {"type": "string"},
        "hook_ok": {"type": "boolean"}, "faithful": {"type": "boolean"}, "faithful_note": {"type": "string"},
        "value_ok": {"type": "boolean"}, "clear_ok": {"type": "boolean"},
    },
    "required": ["setup_lines", "setup_why", "payoff_lines", "payoff_why", "hook_ok", "faithful", "faithful_note",
                 "value_ok", "clear_ok"],
}
CONTEXT_SENTENCES = 6  # B1..B6 / A1..A6 at most (and within JUDGE_CONTEXT_SECONDS)
PAUSE = 0.6  # s of silence before a word = a sentence boundary even without punctuation (Whisper skips it often)


# ------------------------------------------------------------------ transcript views

def _words(segs: list[Segment]) -> list[tuple[int, Word]]:
    return [(i, w) for i, s in enumerate(segs) for w in s.words]


def _lines(words: list[tuple[int, Word]]) -> str:
    """Words grouped by their segment, as [start-end] lines (like the selection prompt)."""
    out, cur, seg = [], [], None
    for i, w in words:
        if seg is not None and i != seg and cur:
            out.append(cur)
            cur = []
        seg = i
        cur.append(w)
    if cur:
        out.append(cur)
    return "\n".join(f"[{ln[0].start:.1f}-{ln[-1].end:.1f}] {''.join(w.word for w in ln).strip()}" for ln in out)


def sentences(words: list[tuple[int, Word]]) -> list[list[Word]]:
    """Words → sentences: after . ? ! … or before a pause ≥ PAUSE (Whisper often leaves segment ends bare)."""
    out, cur = [], []
    for k, (_, w) in enumerate(words):
        if cur and (_ends_sentence(cur[-1].word) or w.start - cur[-1].end >= PAUSE):
            out.append(cur)
            cur = []
        cur.append(w)
        del k
    if cur:
        out.append(cur)
    return out


def _sentence_line(tag: str, ws: list[Word]) -> str:
    return f"{tag} [{ws[0].start:.1f}-{ws[-1].end:.1f}] {''.join(w.word for w in ws).strip()}"


def views(segs: list[Segment], clip: Clip, context: float | None = None) -> tuple[list, list, list]:
    """(before, clip, after) words: struck words are not in the clip (the viewer never hears them)."""
    ctx = settings.judge_context_seconds if context is None else context
    removed = clean_removed(clip.removed)
    allw = _words(segs)
    before = [(i, w) for i, w in allw if clip.start - ctx <= w.start and (w.start + w.end) / 2 < clip.start]
    inside = [(i, w) for i, w in allw if clip.start <= (w.start + w.end) / 2 < clip.end and not is_struck(w, removed)]
    after = [(i, w) for i, w in allw if clip.end <= (w.start + w.end) / 2 and w.end <= clip.end + ctx]
    return before, inside, after


# ------------------------------------------------------------------ rules (no model)

def rule_checks(segs: list[Segment], clip: Clip) -> list[ReviewCheck]:
    before, inside, _ = views(segs, clip, context=60)
    out: list[ReviewCheck] = []
    if not inside:
        return out
    first, last = inside[0][1], inside[-1][1]
    prev = before[-1][1] if before else None
    if prev and not _ends_sentence(prev.word) and first.start - prev.end < PAUSE:
        out.append(ReviewCheck(key="opening", ok=False, source="rule", quote=first.word.strip(),
                               note="Starts in the middle of a sentence."))
    elif norm(first.word) in REFERENCE_STARTERS:  # a hint only: "So, …" usually opens a fine clip
        out.append(ReviewCheck(key="hint", ok=False, source="rule", quote=first.word.strip(),
                               note=f"Opens with “{first.word.strip()}” — check it doesn't point back to "
                                    "something said before the clip."))
    nxt = next((w for _, w in views(segs, clip, context=5)[2]), None)
    if not _ends_sentence(last.word) and not (nxt and nxt.start - last.end >= PAUSE):
        out.append(ReviewCheck(key="ending", ok=False, source="rule", quote=last.word.strip(),
                               note="Ends in the middle of a sentence."))
    text = "".join(w.word for _, w in inside)
    m = LIST_RE.search(text)
    if m:
        n = COUNT.get(m.group(1).lower()) or int(m.group(1))
        said = len(MARKERS.findall(text[m.end():]))
        if 0 < said < n:
            out.append(ReviewCheck(key="list", ok=False, source="rule", quote=m.group(0),
                                   note=f"Announces {n} {m.group(2)} but only {said} are counted off in the clip."))
    return out


# ------------------------------------------------------------------ fixes

def _snap(t: float, edges: list[float]) -> float | None:
    near = [e for e in edges if abs(e - t) <= SNAP]
    return min(near, key=lambda e: abs(e - t)) if near else None


def make_fix(segs: list[Segment], clip: Clip, start: float | None, end: float | None,
             reason: str, snap: bool = True) -> tuple[ClipFix | None, str]:
    """Snap suggested edges to sentence edges; refuse if the clip would pass max_clip_seconds.
    Returns (fix, why_not) — why_not explains a fix that couldn't be applied."""
    starts, ends = sentence_bounds(segs)
    s, e = clip.start, clip.end
    if start is not None and start < clip.start:
        s = _snap(start, starts) if snap else start
        if s is None:
            return None, "the suggested start isn't near a sentence start"
    if end is not None and end > clip.end:
        e = _snap(end, ends) if snap else end
        if e is None:
            return None, "the suggested end isn't near a sentence end"
    if (s, e) == (clip.start, clip.end):
        return None, ""
    if e - s > settings.max_clip_seconds:
        return None, (f"fixing it needs {e - s:.0f} s — over the {settings.max_clip_seconds:.0f} s a Short allows")
    moves = ([f"start {clip.start - s:.1f} s earlier"] if s < clip.start else []) + \
            ([f"end {e - clip.end:.1f} s later"] if e > clip.end else [])
    why = f"{reason[0].upper()}{reason[1:]}: " if reason else ""
    return ClipFix(start=round(s, 2), end=round(e, 2), reason=f"{why}{' and '.join(moves)}."[:300]), ""


def rule_fix(segs: list[Segment], clip: Clip, checks: list[ReviewCheck]) -> tuple[ClipFix | None, str]:
    """Mid-sentence edges: back to the sentence start / on to the sentence end (when within SNAP_REACH)."""
    starts, ends = sentence_bounds(segs)
    s = e = None
    if any(c.key == "opening" and c.note.startswith("Starts in the middle") for c in checks):
        prior = [t for t in starts if t < clip.start - 0.01]
        s = max(prior) if prior and clip.start - max(prior) <= 15 else None
    if any(c.key == "ending" and c.source == "rule" for c in checks):
        later = [t for t in ends if t > clip.end + 0.01]
        e = min(later) if later and min(later) - clip.end <= 15 else None
    if s is None and e is None:
        return None, "no sentence edge close enough"
    return make_fix(segs, clip, s, e, "whole sentences")


# ------------------------------------------------------------------ verdict

def verdict(checks: list[ReviewCheck], fix: ClipFix | None) -> str:
    bad = {c.key for c in checks if not c.ok}
    gemma_bad = {c.key for c in checks if not c.ok and c.source == "gemma"}
    if "faithful" in bad or {"value", "hook"} <= gemma_bad:
        return "reject"
    if bad & {"opening", "ending", "list"}:
        return "trim" if fix else "needs_context"
    return "ready"


# ------------------------------------------------------------------ the judge

def _in(quote: str, words: list[tuple[int, Word]], min_words: int = 1) -> bool:
    """quote appears word for word (case/punctuation aside) in words; a long quote may be a 6-word run of it."""
    q = [norm(x) for x in quote.split() if norm(x)]
    text = [norm(w.word) for _, w in words]
    if len(q) < min_words:
        return False
    k = min(len(q), 6)  # models paraphrase the tail of long quotes: a verbatim 6-word run is enough evidence
    runs = [q[i:i + k] for i in range(len(q) - k + 1)]
    return any(text[i:i + k] == r for r in runs for i in range(len(text) - k + 1))


def _clean_quote(v) -> str:
    return " ".join(str(v or "").replace("*", "").split()).strip().strip('"“”\'')


def _short(text: str, words: int = 12) -> str:
    w = text.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def _times(words: list[tuple[int, Word]]) -> set[float]:
    return {round(w.start, 1) for _, w in words} | {round(w.end, 1) for _, w in words}


def ask(segs: list[Segment], clip: Clip, llm: LLM) -> tuple[list[ReviewCheck], float | None, float | None, str]:
    """Gemma's checks + the start/end its chosen sentences imply. It picks sentence NUMBERS (B1.., A1..), so every
    flag points at real words outside the clip and every fix lands on a sentence edge (free-text quotes and times
    were unreliable: on real clips the setup flag either fired on self-introducing clips or never)."""
    before, inside, after = views(segs, clip)
    bs = sentences(before)[-CONTEXT_SENTENCES:][::-1]  # B1 = right before the clip
    as_ = sentences(after)[:CONTEXT_SENTENCES]
    raw = llm.chat_json(PROMPT.format(
        before="\n".join(_sentence_line(f"B{k}", ws) for k, ws in reversed(list(enumerate(bs, 1))))
        or "(the video starts here)",
        clip=_lines(inside), dur=clip.end - clip.start,
        after="\n".join(_sentence_line(f"A{k}", ws) for k, ws in enumerate(as_, 1)) or "(the video ends here)"),
        SCHEMA, think=settings.judge_think)
    if not isinstance(raw, dict):
        raise ValueError(f"not an object: {str(raw)[:100]}")

    def flag(k: str) -> bool:
        v = raw.get(k)
        if not isinstance(v, bool):
            raise ValueError(f"{k} is not true/false")
        return v

    def picks(k: str, n: int) -> list[int]:
        v = raw.get(k)
        if not isinstance(v, list):
            raise ValueError(f"{k} is not a list")
        return sorted({int(x) for x in v if isinstance(x, (int, float)) and 1 <= int(x) <= n})

    checks: list[ReviewCheck] = []
    start = end = None
    reasons = []
    need_b = picks("setup_lines", len(bs))
    if need_b:
        first = bs[max(need_b) - 1]  # everything from the furthest needed sentence up to the clip
        why = _clean_quote(raw.get("setup_why"))
        checks.append(ReviewCheck(key="opening", ok=False, quote=why[:200] if _in(why, inside) else "",
                                  note=f"Needs what is said just before it: “{_short(_text(first))}”"
                                       + (f" (for “{_short(why, 8)}”)." if why else ".")))
        start = first[0].start
        reasons.append("start where it's set up")
    need_a = picks("payoff_lines", len(as_))
    if need_a:
        last = as_[max(need_a) - 1]
        why = plain(str(raw.get("payoff_why") or "")).strip()[:200]
        checks.append(ReviewCheck(key="ending", ok=False,
                                  note=f"{why or 'Stops before its conclusion'} — it comes right after: "
                                       f"“{_short(_text(as_[0]))}”."))
        end = last[-1].end
        reasons.append("end where it pays off")
    if not flag("faithful"):
        checks.append(ReviewCheck(key="faithful", ok=False,
                                  note=plain(str(raw.get("faithful_note") or "")).strip()[:400]
                                  or "Cut here, it changes what the speaker meant."))
    for key, label in (("hook_ok", "hook"), ("value_ok", "value"), ("clear_ok", "clear")):
        if not flag(key):
            checks.append(ReviewCheck(key=label, ok=False, note={
                "hook": "The first seconds don't give a reason to keep watching.",
                "value": "No clear insight, laugh or emotion on its own.",
                "clear": "Hard to follow from speech alone."}[label]))
    passed = {"hook", "value", "clear", "faithful", "opening", "ending"} - {c.key for c in checks}
    checks += [ReviewCheck(key=k, ok=True) for k in sorted(passed)]
    return checks, start, end, " and ".join(reasons)


def _text(ws: list[Word]) -> str:
    return "".join(w.word for w in ws).strip()


def review(segs: list[Segment], clip: Clip, llm: LLM | None) -> ClipReview:
    """The clip judged as it is now (start-end, struck words removed)."""
    rules = rule_checks(segs, clip)
    checks, fix, why_not, model = list(rules), None, "", None
    if llm is not None:
        try:
            asked, s, e, reason = ask(segs, clip, llm)
            model = getattr(llm, "model", None) or "gemma"
            have = {(c.key, c.ok) for c in asked}
            checks = asked + [r for r in rules if (r.key, False) not in have]  # rules add what Gemma didn't see
            if s is not None or e is not None:
                fix, why_not = make_fix(segs, clip, s, e, reason, snap=False)  # already sentence edges
        except Exception as e:  # one bad answer never costs the job: the measured checks still stand
            log.warning("clip judge: Gemma failed for %.0f-%.0f s (%s) — rule checks only", clip.start, clip.end, e)
    if fix is None and any(not c.ok and c.key in ("opening", "ending") for c in checks):
        rfix, rwhy = rule_fix(segs, clip, checks)
        fix, why_not = (rfix, "") if rfix else (None, why_not or rwhy)
    if why_not:
        checks.append(ReviewCheck(key="fix", ok=False, source="rule", note=f"No automatic fix: {why_not}."))
    return ClipReview(verdict=verdict(checks, fix), checks=checks, fix=fix, start=clip.start, end=clip.end,
                      model=model)


def review_all(segs: list[Segment], clips: list[Clip], llm: LLM | None, on_progress=None) -> dict:
    """Every candidate (in place) → counts per verdict, for the trace."""
    counts: dict[str, int] = {}
    for i, c in enumerate(clips):
        if on_progress:
            on_progress(i / max(len(clips), 1), f"Checking clip {i + 1}/{len(clips)}")
        c.review = review(segs, c, llm)
        counts[c.review.verdict] = counts.get(c.review.verdict, 0) + 1
    return counts


def rebalance(clips: list[Clip], n: int) -> list[str]:
    """Pre-approved rejects swap with the best unapproved candidate that isn't rejected. Returns what changed."""
    from .highlights import rank

    changed = []
    spare = sorted([c for c in clips if not c.approved and not (c.review and c.review.verdict == "reject")],
                   key=lambda c: -rank(c))
    for c in [c for c in clips if c.approved and c.review and c.review.verdict == "reject"]:
        if not spare:
            break
        sub = spare.pop(0)
        c.approved, sub.approved = False, True
        changed.append(f"#{c.index} rejected → #{sub.index} approved instead")
    return changed
