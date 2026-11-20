"""Fix what Whisper misheard: edit one word, or replace a word / phrase everywhere in a job's transcript.

transcript.json stays the single source of truth — captions, the .srt export, post writing and the UI all read it,
so a fix made here shows up everywhere (rendered shorts covering the change are marked stale by the API).

Words keep Whisper's convention of a leading space (" hello"). Word timings never change, except that a
multi-word match ("open ai" -> "OpenAI") is merged into one word spanning all of them.
"""
import json
import re
import threading
from pathlib import Path

from ..models import Segment, Word
from .jumpcut import norm

_lock = threading.Lock()  # read-modify-write of transcript.json
MAX_WORD = 60


def load(job_dir: Path) -> list[Segment]:
    p = job_dir / "transcript.json"
    return [Segment(**s) for s in json.loads(p.read_text())] if p.exists() else []


def save(job_dir: Path, segs: list[Segment]) -> None:
    tmp = job_dir / "transcript.json.tmp"
    tmp.write_text(json.dumps([s.model_dump() for s in segs], ensure_ascii=False))
    tmp.replace(job_dir / "transcript.json")


def rebuild_text(seg: Segment) -> None:
    if seg.words:
        seg.text = "".join(w.word for w in seg.words).strip()


def words_in(segs: list[Segment], start: float, end: float) -> list[dict]:
    """Words overlapping [start, end] with their address (seg, i) for editing."""
    return [{"seg": si, "i": wi, "start": w.start, "end": w.end, "word": w.word.strip()}
            for si, s in enumerate(segs) if s.end >= start and s.start <= end
            for wi, w in enumerate(s.words) if w.end > start and w.start < end]


def clean_word(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()[:MAX_WORD]
    if not text:
        raise ValueError("a word can't be empty")
    return " " + text


def set_word(job_dir: Path, seg: int, i: int, text: str) -> tuple[float, float]:
    """Replace one word's text. Returns its (start, end) in source time."""
    text = clean_word(text)
    with _lock:
        segs = load(job_dir)
        try:
            w = segs[seg].words[i]
        except IndexError:
            raise KeyError((seg, i)) from None
        w.word = text
        rebuild_text(segs[seg])
        save(job_dir, segs)
        return w.start, w.end


def _trailing_punct(word: str) -> str:
    m = re.search(r"[^\w']+$", word.strip())
    return m.group(0) if m else ""


def replace_all(job_dir: Path, find: str, replace: str, dry_run: bool = False) -> list[tuple[float, float]]:
    """Replace every occurrence of `find` (one or more whole words, case and punctuation ignored) with `replace`.

    A phrase match is merged into one word covering all of its timings. Each occurrence keeps its own trailing
    punctuation ("ai," -> "OpenAI,", "ai." -> "OpenAI.") — any typed into `replace` is dropped.
    Matches don't cross segment boundaries. Returns the changed (start, end) spans.
    """
    target = [norm(x) for x in find.split() if norm(x)]
    if not target:
        raise ValueError("nothing to find")
    # punctuation belongs to each occurrence ("resume." stays a sentence end), not to what you typed
    new = re.sub(r"[^\w']+$", "", clean_word(replace).strip())
    if not new:
        raise ValueError("the replacement needs at least one letter or digit")
    with _lock:
        segs = load(job_dir)
        spans: list[tuple[float, float]] = []
        for seg in segs:
            out: list[Word] = []
            ws, k = seg.words, 0
            while k < len(ws):
                window = ws[k:k + len(target)]
                if len(window) == len(target) and [norm(w.word) for w in window] == target:
                    merged = Word(start=window[0].start, end=window[-1].end,
                                  word=" " + new + _trailing_punct(window[-1].word))
                    if merged.word != "".join(w.word for w in window):  # already right: not a change
                        spans.append((merged.start, merged.end))
                    out.append(merged)
                    k += len(target)
                else:
                    out.append(ws[k])
                    k += 1
            if not dry_run:
                seg.words = out
                rebuild_text(seg)
        if spans and not dry_run:
            save(job_dir, segs)
        return spans
