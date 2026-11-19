"""Step 3 — Gemma reads the transcript and picks the most shareable moments."""
import hashlib
import logging
import math
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

Transcript:
{transcript}"""


def clean_keywords(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = [str(k).strip().lower() for k in raw if str(k).strip()]
    return list(dict.fromkeys(out))[:4]


def _line(s: Segment) -> str:
    return f"[{s.start:.1f}-{s.end:.1f}] {s.text}"


def windows(segs: list[Segment], size: float, max_chars: int | None = None) -> Iterator[list[Segment]]:
    """Split transcript into chunks of ~size seconds AND at most max_chars of transcript lines,
    so the prompt always leaves Gemma room in its context to think and answer."""
    if not segs:
        return
    win: list[Segment] = []
    t0, chars = segs[0].start, 0
    for s in segs:
        n = len(_line(s)) + 1
        if win and (s.start - t0 > size or (max_chars and chars + n > max_chars)):
            yield win
            win, t0, chars = [], s.start, 0
        win.append(s)
        chars += n
    if win:
        yield win


# Measured with gemma4 on a real transcript: ~2.6 chars/token (timestamps tokenise heavily). With thinking on,
# picking 2-4 clips needs ~1.2-1.8k output tokens, so the transcript gets at most half the context window.
CHARS_PER_TOKEN = 2.5
PROMPT_CTX_SHARE = 0.5


def transcript_char_budget(template: str = PROMPT) -> int:
    return int(settings.ollama_num_ctx * PROMPT_CTX_SHARE * CHARS_PER_TOKEN) - len(template)


PROMPT_FIELDS = ("{n}", "{min_s", "{max_s", "{transcript}")


def check_prompt(template: str) -> None:
    """A custom selection prompt must use the same placeholders as PROMPT (eval script, see app/eval.py)."""
    missing = [f for f in PROMPT_FIELDS if f not in template]
    if missing:
        raise ValueError(f"prompt is missing placeholders: {', '.join(missing)}")


def prompt_id(template: str) -> str:
    return hashlib.sha256(template.encode()).hexdigest()[:10]


def build_prompt(win: list[Segment], n: int, template: str = PROMPT) -> str:
    lines = "\n".join(_line(s) for s in win)
    return template.format(n=n, min_s=settings.min_clip_seconds,
                           max_s=settings.max_clip_seconds, transcript=lines)


def snap(start: float, end: float, segs: list[Segment]) -> tuple[float, float]:
    """Align to real sentence boundaries so cuts never chop a word."""
    s = min((x.start for x in segs), key=lambda t: abs(t - start))
    e = min((x.end for x in segs), key=lambda t: abs(t - end))
    return s, e


def fit_duration(start: float, end: float, segs: list[Segment]) -> tuple[float, float] | None:
    """Trim trailing segments if too long; reject if too short."""
    if end - start > settings.max_clip_seconds:
        ends = [x.end for x in segs if start < x.end <= start + settings.max_clip_seconds]
        if not ends:
            return None
        end = max(ends)
    if end - start < settings.min_clip_seconds:
        return None
    return start, end


def dedupe(clips: list[Clip], n: int) -> list[Clip]:
    kept: list[Clip] = []
    for c in sorted(clips, key=lambda c: -c.score):
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
                      prompt: str = PROMPT, think=_SETTING, trace: dict | None = None) -> list[Clip]:
    """Returns up to `keep` (default n) non-overlapping candidates; the best `n` are pre-approved.

    `trace` (optional dict) is filled with everything the model returned and what happened to each pick —
    saved as candidates.json per job and compared across models/prompts by `python -m app.eval`.
    """
    think = settings.select_think if think is _SETTING else think
    wins = list(windows(segs, settings.window_seconds, transcript_char_budget(prompt)))
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
            raw = llm.chat_json(build_prompt(win, per_window, prompt), CLIP_SCHEMA, think=think)
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
                candidates.append(clip)
                rec["clip"] = clip
            except (KeyError, ValueError, TypeError) as e:
                log.warning("bad clip from model: %s (%s)", item, e)
                rec["outcome"] = "invalid"
    if trace is not None:  # filled before a possible raise, so failed runs still show what went wrong
        trace.update(model=getattr(llm, "model", None), think=think, prompt_id=prompt_id(prompt),
                     num_ctx=settings.ollama_num_ctx, min_s=settings.min_clip_seconds,
                     max_s=settings.max_clip_seconds, asked_total=want, windows=win_log, items=items, clips=[])
    if wins and len(failures) == len(wins):
        raise RuntimeError(f"Gemma failed on every transcript chunk — is Ollama running and the model "
                           f"pulled? Last error: {failures[-1][:200]}")
    if on_progress:
        on_progress(1.0, f"{len(candidates)} candidate clips found")
    clips = dedupe(candidates, want)
    best = {id(c) for c in sorted(clips, key=lambda c: -c.score)[:n]}
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
                rec.update(start=clip.start, end=clip.end, score=clip.score)
        trace["clips"] = [c.model_dump(include={"start", "end", "title", "hook", "score", "keywords", "approved"})
                          for c in clips]
    return clips
