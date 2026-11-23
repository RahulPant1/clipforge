"""Step 3 — Gemma reads the transcript and picks the most shareable moments."""
import hashlib
import logging
import math
import re
import time
from collections.abc import Callable, Iterator

from ..config import settings
from ..models import Clip, Segment
from .llm import LLM

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["start", "end", "title", "hook", "score", "keywords"],
            },
        }
    },
    "required": ["clips"],
}


def _with_relevance(schema: dict) -> dict:
    item = schema["properties"]["clips"]["items"]
    return {**schema, "properties": {"clips": {**schema["properties"]["clips"], "items": {
        **item, "properties": {**item["properties"], "relevance": {"type": "integer"}},
        "required": [*item["required"], "relevance"]}}}}


TOPIC_SCHEMA = _with_relevance(CLIP_SCHEMA)  # with a topic, every pick also says how on-topic it is

PROMPT = """You are an expert short-form video editor.
From the transcript below, pick the {n} best self-contained moments for a YouTube Short.

A great clip:
- has a strong hook in the first 3 seconds (bold claim, question, surprising fact)
- covers ONE complete idea with a satisfying ending
- is surprising, funny, emotional or genuinely useful
- makes sense on its own (no "as I said earlier", no unresolved references)

Rules:
- Use ONLY start/end times that appear in the [start-end] brackets.
- Each clip must last between {min_s:.0f} and {max_s:.0f} seconds.
- title: catchy, under 60 characters.
- hook: short on-screen text for the first 3 seconds, under 8 words.
- score: 1-10 for how likely the clip is to go viral.
- keywords: 2-4 single words spoken in the clip that carry its meaning (numbers, key nouns,
  strong verbs). They will be highlighted in the captions, so copy them exactly as spoken.
{focus}
Transcript:
{transcript}"""


# Prompt-based clipping: "find me the moments about salary negotiation". Only added when the job has a topic.
FOCUS = """
Topic: the viewer only wants moments about "{topic}".
- Pick only moments that are clearly about this topic (directly, or a closely related point or example).
- If this part of the transcript has nothing on it, return fewer clips or an empty list.
  Never stretch an unrelated moment to fit the topic.
- relevance: 1-10, how much of the clip is about the topic (10 = all of it, 5 = touches on it, 1 = unrelated).
"""
MAX_TOPIC = 120
MIN_RELEVANCE = 5  # below this a pick is dropped as off_topic (the model was told 5 = "touches on it")


def clean_topic(topic: str | None) -> str:
    """One line, no quotes (it sits inside quotes in the prompt), at most MAX_TOPIC characters."""
    return re.sub(r"\s+", " ", re.sub(r"[\"“”]", "", topic or "")).strip()[:MAX_TOPIC].strip()


def focus_block(topic: str) -> str:
    return FOCUS.format(topic=topic) if topic else ""


def clean_keywords(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = [str(k).strip().lower() for k in raw if str(k).strip()]
    return list(dict.fromkeys(out))[:4]


def _line(s: Segment) -> str:
    return f"[{s.start:.1f}-{s.end:.1f}] {s.text}"


# Scene index (scenes.py): what is visible, between the transcript lines. Only added when the job has scenes.
SCENE_NOTE = """
Lines starting with "  ↳ on screen" are not speech: they say what is visible while the line above is spoken.
- Prefer moments where what is said and what is shown pay off together (a demo, a result on screen, a
  before/after, something the speaker shows), and skip clips that only make sense with a visual outside them.
- They have no times on purpose: clip start/end still come only from the [start-end] transcript brackets.
"""


def _scene_line(sc) -> str:
    # no [start-end]: on real footage Gemma used scene edges as clip edges → 20 s clips, 7 of 18 too short
    return f"  ↳ on screen ({sc.kind.replace('_', ' ')}): {sc.description}"


def _scenes_in(scenes: list | None, start: float, end: float) -> list:
    return [sc for sc in scenes or [] if start <= sc.start < end]


TAIL_MIN = 2  # × max_clip_seconds: a shorter last window is merged into the one before


def windows(segs: list[Segment], size: float, max_chars: int | None = None,
            scenes: list | None = None) -> Iterator[list[Segment]]:
    """Split transcript into chunks of ~size seconds AND at most max_chars of transcript lines (+ the ON SCREEN
    lines that fall inside them), so the prompt always leaves Gemma room in its context to think and answer."""
    if not segs:
        return
    extra = [0] * len(segs)  # chars of scene lines that start during segment i (before the next one)
    for sc in scenes or []:
        i = next((k for k, x in enumerate(segs) if x.end > sc.start), len(segs) - 1)
        extra[i] += len(_scene_line(sc)) + 1
    out: list[list[Segment]] = []
    size_of: dict[int, int] = {}  # id(segment) → its chars in the prompt (line + scene lines)
    win: list[Segment] = []
    t0, chars = segs[0].start, 0
    for k, s in enumerate(segs):
        n = len(_line(s)) + 1 + extra[k]
        size_of[id(s)] = n
        if win and (s.start - t0 > size or (max_chars and chars + n > max_chars)):
            out.append(win)
            win, t0, chars = [], s.start, 0
        win.append(s)
        chars += n
    if win and out and win[-1].end - win[0].start < TAIL_MIN * settings.max_clip_seconds:
        # a tail too short for a clip (seen: the last 12 s of a video) only yields 5-11 s picks that all get
        # rejected → one window with the one before if it fits the budget, else the two split evenly
        both = out.pop() + win
        total = sum(size_of[id(x)] for x in both)
        if not max_chars or total <= max_chars:
            win = both
        else:
            run, cut = 0, len(both)
            for i, x in enumerate(both):
                run += size_of[id(x)]
                if run >= total / 2:
                    cut = i + 1
                    break
            if sum(size_of[id(x)] for x in both[:cut]) > max_chars:  # one long line crossed the middle
                cut -= 1
            if 0 < cut < len(both) and sum(size_of[id(x)] for x in both[cut:]) <= max_chars:
                out.append(both[:cut])
                win = both[cut:]
            else:  # can't balance within budget: leave them as they were
                out.append(both[:len(both) - len(win)])
    if win:
        out.append(win)
    yield from out


# Measured with gemma4 on a real transcript: ~2.6 chars/token (timestamps tokenise heavily). With thinking on,
# picking 2-4 clips needs ~1.2-1.8k output tokens, so the transcript gets at most half the context window.
CHARS_PER_TOKEN = 2.5
PROMPT_CTX_SHARE = 0.5


def transcript_char_budget(template: str = PROMPT, topic: str = "", scenes: bool = False) -> int:
    return (int(settings.ollama_num_ctx * PROMPT_CTX_SHARE * CHARS_PER_TOKEN) - len(template) - len(focus_block(topic))
            - (len(SCENE_NOTE) if scenes else 0))


PROMPT_FIELDS = ("{n}", "{min_s", "{max_s", "{transcript}")


def check_prompt(template: str) -> None:
    """A custom selection prompt must use the same placeholders as PROMPT (eval script, see app/eval.py)."""
    missing = [f for f in PROMPT_FIELDS if f not in template]
    if missing:
        raise ValueError(f"prompt is missing placeholders: {', '.join(missing)}")


def prompt_id(template: str) -> str:
    return hashlib.sha256(template.encode()).hexdigest()[:10]


def build_prompt(win: list[Segment], n: int, template: str = PROMPT, topic: str = "",
                 scenes: list | None = None) -> str:
    """The prompt for one window. `scenes` (scenes.SceneIndex.for_prompt()) become ON SCREEN lines in time order."""
    shown = _scenes_in(scenes, win[0].start, win[-1].end) if win else []
    rows: list[str] = []
    for i, s in enumerate(win):  # each scene under the line being spoken when it starts
        rows.append(_line(s))
        nxt = win[i + 1].start if i + 1 < len(win) else float("inf")
        rows += [_scene_line(sc) for sc in shown if (i == 0 or sc.start >= s.start) and sc.start < nxt]
    lines = "\n".join(rows)
    if "{focus}" not in template:  # custom eval prompts needn't know about topics: it goes before the transcript
        template = template.replace("{transcript}", "{focus}{transcript}", 1)
    return template.format(n=n, min_s=settings.min_clip_seconds, max_s=settings.max_clip_seconds,
                           transcript=lines, focus=focus_block(topic) + (SCENE_NOTE if shown else ""))


SNAP_REACH = 8.0  # s: how far a clip edge may move to land on a sentence boundary
_SENTENCE_END = (".", "?", "!", "…")


def _ends_sentence(word: str) -> bool:
    return word.strip().rstrip("\"'”’)]").endswith(_SENTENCE_END)


def sentence_bounds(segs: list[Segment]) -> tuple[list[float], list[float]]:
    """(sentence start times, sentence end times) from the words' punctuation.

    Whisper segments are NOT sentences: on real footage a third of the clips snapped to segment edges ended
    mid-sentence ("Now, I'm not saying that"). Without punctuation (some languages) this is just the first/last word.
    """
    words = [w for s in segs for w in s.words]
    starts, ends = [], []
    for prev, w in zip([None, *words], words, strict=False):
        if prev is None or _ends_sentence(prev.word):
            starts.append(w.start)
            if prev is not None:
                ends.append(prev.end)
    if words:
        ends.append(words[-1].end)
    return starts, ends


def _nearest(times: list[float], t: float, reach: float = SNAP_REACH) -> float | None:
    near = [x for x in times if abs(x - t) <= reach]
    return min(near, key=lambda x: abs(x - t)) if near else None


def snap(start: float, end: float, segs: list[Segment]) -> tuple[float, float]:
    """Align to sentence boundaries (within SNAP_REACH), else to Whisper segment edges — cuts never chop a word."""
    starts, ends = sentence_bounds(segs)
    s = _nearest(starts, start)
    e = _nearest([x for x in ends if s is None or x > s], end)
    if s is None:
        s = min((x.start for x in segs), key=lambda t: abs(t - start))
    if e is None or e <= s:
        e = min((x.end for x in segs), key=lambda t: abs(t - end))
    return s, e


def fit_duration(start: float, end: float, segs: list[Segment]) -> tuple[float, float] | None:
    """Trim if too long (to the last sentence end that fits, else the last segment end); reject if too short."""
    lo, hi = start + settings.min_clip_seconds, start + settings.max_clip_seconds
    if end - start > settings.max_clip_seconds:
        ends = [x for x in sentence_bounds(segs)[1] if lo <= x <= hi] or [
            x.end for x in segs if start < x.end <= hi]
        if not ends:
            return None
        end = max(ends)
    if end - start < settings.min_clip_seconds:
        return None
    return start, end


def rank(c: Clip) -> int:
    """Best first: virality, plus how on-topic it is when the job has a topic."""
    return c.score + (c.relevance or 0)


def dedupe(clips: list[Clip], n: int) -> list[Clip]:
    kept: list[Clip] = []
    for c in sorted(clips, key=lambda c: -rank(c)):
        if all(c.end <= k.start or c.start >= k.end for k in kept):
            kept.append(c)
        if len(kept) == n:
            break
    kept.sort(key=lambda c: c.start)
    for i, c in enumerate(kept, 1):
        c.index = i
    return kept


MAX_CLIPS_PER_WINDOW = 8  # more than this from one chunk and Gemma starts returning overlapping filler


def clips_per_window(want: int, num_windows: int) -> int:
    """How many clips to ask Gemma for per transcript chunk so all chunks together can supply `want`.

    +1 spare per chunk because some picks get rejected (too short) or deduped (overlap).
    `settings.clips_per_window` is the floor — long videos still get a choice from every chunk.
    """
    if num_windows <= 0:
        return settings.clips_per_window
    need = math.ceil(want / num_windows) + 1
    return min(MAX_CLIPS_PER_WINDOW, max(settings.clips_per_window, need))


_SETTING = object()  # "use settings.select_think" (None itself means "don't send think")


def select_highlights(segs: list[Segment], n: int, llm: LLM,
                      on_progress: ProgressFn | None = None, keep: int | None = None, *,
                      prompt: str = PROMPT, think=_SETTING, trace: dict | None = None,
                      topic: str = "", scenes: list | None = None) -> list[Clip]:
    """Returns up to `keep` (default n) non-overlapping candidates; the best `n` are pre-approved.

    `topic` (optional) steers the prompt to moments about it; picks it rates below MIN_RELEVANCE are dropped
    (outcome off_topic) and the rest are ranked by score + relevance.

    `trace` (optional dict) is filled with everything the model returned and what happened to each pick —
    saved as candidates.json per job and compared across models/prompts by `python -m app.eval`.
    """
    think = settings.select_think if think is _SETTING else think
    topic = clean_topic(topic)
    schema = TOPIC_SCHEMA if topic else CLIP_SCHEMA
    wins = list(windows(segs, settings.window_seconds, transcript_char_budget(prompt, topic, bool(scenes)), scenes))
    want = max(keep or n, n)
    per_window = clips_per_window(want, len(wins))
    candidates: list[Clip] = []
    failures: list[str] = []
    win_log: list[dict] = []
    items: list[dict] = []  # every pick the model made, with its fate
    for i, win in enumerate(wins):
        if on_progress:
            on_progress(i / len(wins), f"Gemma reviewing part {i + 1}/{len(wins)}")
        t0 = time.monotonic()
        entry = {"window": i, "start": win[0].start, "end": win[-1].end, "asked": per_window}
        win_log.append(entry)
        try:
            raw = llm.chat_json(build_prompt(win, per_window, prompt, topic, scenes), schema, think=think)
        except Exception as e:  # one bad window shouldn't kill the job
            log.warning("window %d skipped: %s", i, e)
            failures.append(str(e))
            entry.update(seconds=round(time.monotonic() - t0, 1), error=str(e)[:300])
            continue
        entry["seconds"] = round(time.monotonic() - t0, 1)
        returned = raw.get("clips", []) if isinstance(raw, dict) else []
        entry["returned"] = len(returned)
        for item in returned:
            rec = {"window": i, "raw": item}
            items.append(rec)
            try:
                start, end = snap(float(item["start"]), float(item["end"]), win)
                fitted = fit_duration(start, end, win)
                if not fitted:
                    rec["outcome"] = "too_short"  # under min_clip_seconds even after snapping
                    continue
                clip = Clip(
                    start=fitted[0], end=fitted[1],
                    title=str(item["title"])[:80], hook=str(item["hook"])[:60],
                    score=max(1, min(10, int(item["score"]))),
                    keywords=clean_keywords(item.get("keywords")),
                )
                if topic:
                    clip.relevance = max(1, min(10, int(item["relevance"])))
                    if clip.relevance < MIN_RELEVANCE:
                        rec["outcome"] = "off_topic"
                        continue
                candidates.append(clip)
                rec["clip"] = clip
            except (KeyError, ValueError, TypeError) as e:
                log.warning("bad clip from model: %s (%s)", item, e)
                rec["outcome"] = "invalid"
    if trace is not None:  # filled before a possible raise, so failed runs still show what went wrong
        trace.update(model=getattr(llm, "model", None), think=think, prompt_id=prompt_id(prompt), topic=topic,
                     scenes=len(scenes or []),
                     num_ctx=settings.ollama_num_ctx, min_s=settings.min_clip_seconds,
                     max_s=settings.max_clip_seconds, asked_total=want, windows=win_log, items=items, clips=[])
    if wins and len(failures) == len(wins):
        raise RuntimeError(f"Gemma failed on every transcript chunk — is Ollama running and the model "
                           f"pulled? Last error: {failures[-1][:200]}")
    if on_progress:
        on_progress(1.0, f"{len(candidates)} candidate clips found")
    clips = dedupe(candidates, want)
    best = {id(c) for c in sorted(clips, key=lambda c: -rank(c))[:n]}
    for c in clips:
        c.approved = id(c) in best
    if trace is not None:
        kept = {id(c) for c in clips}
        for rec in items:
            clip = rec.pop("clip", None)
            if clip is not None:
                if id(clip) in kept:
                    rec["outcome"] = "kept"
                elif any(clip.start < k.end and clip.end > k.start for k in clips):
                    rec["outcome"] = "overlap"  # lost to a higher-scored clip covering the same moment
                else:
                    rec["outcome"] = "ranked_out"  # fine, but more candidates than asked for and others scored higher
                rec.update(start=clip.start, end=clip.end, score=clip.score, relevance=clip.relevance)
        trace["clips"] = [c.model_dump(include={"start", "end", "title", "hook", "score", "relevance", "keywords",
                                                "approved"})
                          for c in clips]
    return clips
