"""Learning mode: study notes from a video — summary, chapters, key concepts, timestamped notes, quiz, hands-on
tasks, what to learn next. Every point is tied to a real transcript time, so you can jump to where it's said.

1. map: the transcript in windows (the same windows as clip selection, so each fits Gemma's context) → per window
   a chapter title, timestamped notes and the concepts explained there. Times must be [bracket] times of that
   window (snapped to the nearest line start); a window that fails is skipped, never the whole job.
2. reduce: one call over the notes of all windows → summary, chapters (picked by number from the window/note
   starts, first at 0:00 — YouTube's chapter rules), the most important concepts (by number), a quiz (each
   question tied to a note number, so the answer is in the video), hands-on tasks and next steps.
3. notes.md: everything as Markdown, times as YouTube links when the source is a YouTube video.

Guardrail: only what the video says — the prompts forbid outside facts; concepts keep the map step's explanation
(written while reading that part of the transcript), the reduce step only chooses and orders.
"""
import json
import logging
import re
from pathlib import Path

from ..config import settings
from ..models import Chapter, Concept, LearnNotes, QuizQuestion, Segment, StudyNote
from .content import plain
from .highlights import CHARS_PER_TOKEN, PROMPT_CTX_SHARE, _line, windows
from .llm import LLM

log = logging.getLogger(__name__)

MAX_NOTES_PER_WINDOW = 8
MAX_CONCEPTS = 10
QUIZ = 5
MIN_CHAPTER_GAP = 10.0  # s: YouTube needs chapters ≥ 10 s long

MAP_PROMPT = """You are making study notes from part {k} of {n} of the video "{title}".

Transcript part (times in seconds):
{transcript}

From THIS part only:
- chapter: a short title (max 8 words) for what this part covers.
- notes: up to {max_notes} key points in the order they are said. Each: t = the [start] time of the line where it
  is said (copy it from the brackets), title (max 10 words), note (1-2 sentences in your own words).
- concepts: the important ideas, terms or techniques explained in this part. Each: name, explanation (1-2
  sentences, as the video explains it), t = the [start] time where it is explained.
Use only what is said here; do not add facts from outside the video. Skip greetings, sponsors and
"subscribe" talk. Same language as the transcript."""

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "chapter": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "object", "properties": {
            "t": {"type": "number"}, "title": {"type": "string"}, "note": {"type": "string"}},
            "required": ["t", "title", "note"]}},
        "concepts": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "explanation": {"type": "string"}, "t": {"type": "number"}},
            "required": ["name", "explanation", "t"]}},
    },
    "required": ["chapter", "notes", "concepts"],
}

REDUCE_PROMPT = """You are writing a study guide for the video "{title}" ({minutes:.0f} minutes) from notes taken
while watching it.

Parts of the video (P = part number, with its start time):
{parts}

Notes (N = note number):
{notes}

Concepts explained (C = concept number):
{concepts}

Write:
- summary: 3-5 sentences on what the video teaches and who it is for.
- chapters: 3-12 chapter starts as NOTE or PART numbers in time order: [{{"ref": "P1", "title": "..."}}, ...].
  The first must be P1. Titles max 6 words.
- key_concepts: the numbers of the {max_concepts} most important concepts, most important first, e.g. ["C3", "C1"].
- quiz: {quiz} multiple-choice questions that check understanding (not trivia). Each: q, choices (4 options),
  answer (index 0-3 of the right one), explain (one sentence), ref (the N number of the note that answers it).
- hands_on: 1-3 short exercises to try the main ideas yourself (in a simulator, in code, on paper — whatever fits).
- next_steps: 2-4 topics to learn next, building on this video.
Use only the notes above for the summary, chapters and quiz; do not invent facts. Same language as the notes."""

REDUCE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "chapters": {"type": "array", "items": {"type": "object", "properties": {
            "ref": {"type": "string"}, "title": {"type": "string"}}, "required": ["ref", "title"]}},
        "key_concepts": {"type": "array", "items": {"type": "string"}},
        "quiz": {"type": "array", "items": {"type": "object", "properties": {
            "q": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}},
            "answer": {"type": "integer"}, "explain": {"type": "string"}, "ref": {"type": "string"}},
            "required": ["q", "choices", "answer", "explain", "ref"]}},
        "hands_on": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "chapters", "key_concepts", "quiz", "hands_on", "next_steps"],
}


def _txt(v, limit: int) -> str:
    return " ".join(plain(str(v or "")).replace("*", "").split())[:limit].strip()


def _snap(t, starts: list[float]) -> float | None:
    """A model time → the nearest line start (None when it isn't a number or is far from every line)."""
    if not isinstance(t, (int, float)) or not starts:
        return None
    best = min(starts, key=lambda s: abs(s - t))
    return round(best, 2) if abs(best - t) <= 15 else None


def map_window(win: list[Segment], k: int, n: int, title: str, llm: LLM) -> dict:
    """One window → {start, chapter, notes[StudyNote], concepts[Concept]} (validated)."""
    raw = llm.chat_json(MAP_PROMPT.format(k=k, n=n, title=title or "untitled", max_notes=MAX_NOTES_PER_WINDOW,
                                          transcript="\n".join(_line(s) for s in win)),
                        MAP_SCHEMA, think=settings.learn_think)
    if not isinstance(raw, dict):
        raise ValueError(f"not an object: {str(raw)[:100]}")
    starts = [s.start for s in win]
    notes, concepts = [], []
    for it in raw.get("notes") or []:
        if not isinstance(it, dict):
            continue
        t, ttl, note = _snap(it.get("t"), starts), _txt(it.get("title"), 120), _txt(it.get("note"), 600)
        if t is not None and ttl and note:
            notes.append(StudyNote(t=t, title=ttl, note=note))
    for it in raw.get("concepts") or []:
        if not isinstance(it, dict):
            continue
        t, name, ex = _snap(it.get("t"), starts), _txt(it.get("name"), 80), _txt(it.get("explanation"), 600)
        if t is not None and name and ex:
            concepts.append(Concept(name=name, explanation=ex, t=t))
    notes = sorted(notes, key=lambda x: x.t)[:MAX_NOTES_PER_WINDOW]
    return {"start": round(win[0].start, 2), "chapter": _txt(raw.get("chapter"), 100) or f"Part {k}",
            "notes": notes, "concepts": concepts}


def _mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02}:{t % 60:02}" if t >= 3600 else f"{t // 60}:{t % 60:02}"


def dedupe_concepts(concepts: list[Concept]) -> list[Concept]:
    seen, out = set(), []
    for c in concepts:
        key = re.sub(r"[^a-z0-9]+", "", c.name.lower())
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def reduce(parts: list[dict], duration: float, title: str, llm: LLM) -> LearnNotes:
    notes = sorted([n for p in parts for n in p["notes"]], key=lambda x: x.t)
    concepts = dedupe_concepts([c for p in parts for c in p["concepts"]])
    refs = {f"P{i}": p["start"] for i, p in enumerate(parts, 1)} | {f"N{i}": n.t for i, n in enumerate(notes, 1)}
    raw = llm.chat_json(REDUCE_PROMPT.format(
        title=title or "untitled", minutes=duration / 60, max_concepts=MAX_CONCEPTS, quiz=QUIZ,
        parts="\n".join(f"P{i} [{_mmss(p['start'])}] {p['chapter']}" for i, p in enumerate(parts, 1)),
        notes="\n".join(f"N{i} [{_mmss(n.t)}] {n.title}: {n.note}" for i, n in enumerate(notes, 1)),
        concepts="\n".join(f"C{i} {c.name}: {c.explanation}" for i, c in enumerate(concepts, 1)) or "(none)"),
        REDUCE_SCHEMA, think=settings.learn_think)
    if not isinstance(raw, dict):
        raise ValueError(f"not an object: {str(raw)[:100]}")

    def ref(v) -> str:
        return str(v or "").strip().upper().replace(" ", "")

    chapters: list[Chapter] = []
    for it in raw.get("chapters") or []:
        if isinstance(it, dict) and ref(it.get("ref")) in refs and _txt(it.get("title"), 100):
            t = refs[ref(it.get("ref"))]
            if not chapters or t - chapters[-1].t >= MIN_CHAPTER_GAP:
                chapters.append(Chapter(t=t, title=_txt(it.get("title"), 100)))
    chapters.sort(key=lambda c: c.t)
    if len(chapters) < 3:  # not usable as YouTube chapters: one per part instead
        chapters = [Chapter(t=p["start"], title=p["chapter"]) for p in parts]
    chapters[0] = chapters[0].model_copy(update={"t": 0.0})  # YouTube: the first chapter starts at 0:00

    picked = []
    for r in raw.get("key_concepts") or []:
        m = re.fullmatch(r"C(\d+)", ref(r))
        if m and 1 <= int(m.group(1)) <= len(concepts) and concepts[int(m.group(1)) - 1] not in picked:
            picked.append(concepts[int(m.group(1)) - 1])
    picked += [c for c in concepts if c not in picked]  # the model's order first, then the rest as said
    quiz = []
    for it in raw.get("quiz") or []:
        if not isinstance(it, dict) or ref(it.get("ref")) not in refs:
            continue
        choices = [_txt(c, 200) for c in it.get("choices") or [] if _txt(c, 200)]
        ans = it.get("answer")
        if len(choices) < 2 or len(set(choices)) != len(choices) or not isinstance(ans, int) \
                or not 0 <= ans < len(choices) or not _txt(it.get("q"), 300):
            continue
        quiz.append(QuizQuestion(q=_txt(it.get("q"), 300), choices=choices[:5], answer=ans,
                                 explain=_txt(it.get("explain"), 400), t=refs[ref(it.get("ref"))]))
    return LearnNotes(title=title, summary=_txt(raw.get("summary"), 1500), chapters=chapters[:12],
                      concepts=picked[:MAX_CONCEPTS], notes=notes, quiz=quiz[:QUIZ],
                      hands_on=[_txt(x, 400) for x in raw.get("hands_on") or [] if _txt(x, 400)][:3],
                      next_steps=[_txt(x, 200) for x in raw.get("next_steps") or [] if _txt(x, 200)][:4],
                      model=getattr(llm, "model", None))


def build(segs: list[Segment], llm: LLM, duration: float, title: str = "", on_progress=None) -> LearnNotes:
    if not segs:
        raise RuntimeError("Learning mode needs speech: no transcript in this video.")
    budget = int(settings.ollama_num_ctx * PROMPT_CTX_SHARE * CHARS_PER_TOKEN) - len(MAP_PROMPT)
    wins = list(windows(segs, settings.window_seconds, budget))
    parts, errors = [], []
    for k, win in enumerate(wins, 1):
        if on_progress:
            on_progress((k - 1) / (len(wins) + 1), f"Gemma taking notes: part {k}/{len(wins)}")
        try:
            parts.append(map_window(win, k, len(wins), title, llm))
        except Exception as e:  # one bad part never costs the notes
            log.warning("learn: part %d skipped: %s", k, e)
            errors.append(str(e)[:200])
    if not parts:
        raise RuntimeError(f"Gemma couldn't take notes on any part — last error: {errors[-1] if errors else '?'}")
    if on_progress:
        on_progress(len(wins) / (len(wins) + 1), "Writing the summary, chapters and quiz")
    try:
        return reduce(parts, duration, title, llm)
    except Exception as e:  # the per-part notes are still worth having
        log.warning("learn: summary step failed (%s) — notes and chapters per part only", e)
        notes = sorted([n for p in parts for n in p["notes"]], key=lambda x: x.t)
        chapters = [Chapter(t=0.0 if i == 0 else p["start"], title=p["chapter"]) for i, p in enumerate(parts)]
        return LearnNotes(title=title, chapters=chapters, notes=notes, model=getattr(llm, "model", None),
                          concepts=dedupe_concepts([c for p in parts for c in p["concepts"]])[:MAX_CONCEPTS])


# ------------------------------------------------------------------ files

def youtube_id(url: str) -> str | None:
    m = re.search(r"(?:youtu\.be/|v=|/shorts/|/live/|/embed/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def markdown(n: LearnNotes, url: str = "") -> str:
    vid = youtube_id(url)

    def at(t: float) -> str:
        return f"[{_mmss(t)}](https://youtu.be/{vid}?t={int(t)})" if vid else _mmss(t)

    out = [f"# {n.title or 'Study notes'}", ""]
    if url:
        out += [f"Source: {url}", ""]
    if n.summary:
        out += ["## Summary", "", n.summary, ""]
    if n.chapters:
        out += ["## Chapters", "", *[f"{_mmss(c.t)} {c.title}" for c in n.chapters], ""]
    if n.concepts:
        out += ["## Key concepts", "", *[f"- **{c.name}** ({at(c.t)}) — {c.explanation}" for c in n.concepts], ""]
    if n.notes:
        out += ["## Notes", "", *[f"- {at(x.t)} **{x.title}** — {x.note}" for x in n.notes], ""]
    if n.quiz:
        out += ["## Quiz", ""]
        for i, q in enumerate(n.quiz, 1):
            out += [f"{i}. {q.q}", *[f"   - {'ABCDE'[k]}) {c}" for k, c in enumerate(q.choices)], ""]
        out += ["### Answers", "", *[f"{i}. {'ABCDE'[q.answer]} — {q.explain} ({at(q.t)})"
                                     for i, q in enumerate(n.quiz, 1)], ""]
    if n.hands_on:
        out += ["## Try it yourself", "", *[f"- {x}" for x in n.hands_on], ""]
    if n.next_steps:
        out += ["## Learn next", "", *[f"- {x}" for x in n.next_steps], ""]
    return "\n".join(out)


def save(n: LearnNotes, job_dir: Path, url: str = "") -> None:
    (job_dir / "learn.json").write_text(n.model_dump_json(indent=1), encoding="utf-8")
    (job_dir / "notes.md").write_text(markdown(n, url), encoding="utf-8")


def load(job_dir: Path) -> LearnNotes | None:
    f = job_dir / "learn.json"
    try:
        return LearnNotes(**json.loads(f.read_text(encoding="utf-8"))) if f.is_file() else None
    except (ValueError, TypeError):
        return None
