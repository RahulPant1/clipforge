"""Replace voice: a clip's dialogue, sentence by sentence, re-spoken in a Voice Library voice (tts.py), placed
where the original sentences were, so the video keeps its timing.

dub_N.json (DubScript) per clip:
  lines   the clip's sentences (judge.sentences: punctuation or a pause) — original text, start/end in SOURCE
          seconds, the text to say instead (edit it), and the generated speech for it
  track   dub_N.wav: every line's speech placed at its original start, clip-relative, the clip's length

Laying the lines out (layout()), in order, each as close to its original start as it can be:
  a line starts at its original time, or later when the one before still runs (never > LATE behind), or up to
  EARLY sooner when it needs the room; it may run LATE into the next sentence's time; the clip end is hard.
  too long for that → sped up (ffmpeg atempo) by up to MAX_RATE; still too long → cut with a fade, too_long.
  Measured on a real clip: the cloned voice needed 34 s for 28.8 s of fast original speech (1.18x overall, > 1.4x
  for some lines) — fixed slots + 1.2x cut every line, so the layout borrows time between neighbours.
Every piece gets FADE fades. v1 replaces the clip's whole audio (the original voice can't be separated from
music/ambience without a separation model); music can be added in the Shorts step (ducked under the new voice).

Render (render.py): options.dub + a track → the track instead of the source audio, captions/stat cards/sfx from the
new words (segments()), no jump cuts (the new speech has its own pauses). Generation holds GPU_LOCK (tts worker +
Whisper for the word timings, one after the other).
"""
import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..models import Clip, DubLine, DubScript, Segment, Word
from . import tts
from .jumpcut import clip_words

log = logging.getLogger(__name__)

SR = 48000  # the track's rate (render mixes at 48 kHz)
MAX_RATE = 1.35  # speed-up that still sounds natural for speech
EARLY = 0.4  # s a line may start before its original time when it needs room
LATE = 0.6  # s a line may run into the next one's time (which then starts later)
GAP = 0.12  # s of air between two lines
TOL = 0.06  # s: overshoot from atempo rounding that isn't worth cutting
FADE = 0.015  # s, every piece
TAIL = 0.08  # s fade when a too-long line is cut


def path(job_dir: Path, index: int) -> Path:
    return job_dir / f"dub_{index}.json"


def load(job_dir: Path, index: int) -> DubScript | None:
    f = path(job_dir, index)
    try:
        return DubScript(**json.loads(f.read_text(encoding="utf-8"))) if f.is_file() else None
    except (ValueError, TypeError):
        return None


def save(job_dir: Path, script: DubScript) -> DubScript:
    f = path(job_dir, script.clip)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(script.model_dump_json(indent=1), encoding="utf-8")
    tmp.replace(f)
    return script


def draft(segs: list[Segment], clip: Clip) -> DubScript:
    """The clip's sentences as they are said now (each line's text starts as the original)."""
    from .judge import _text, sentences

    words = [(0, w) for w in clip_words(segs, clip.start, clip.end)]
    lines = []
    for k, ws in enumerate(sentences(words)):
        t = _text(ws)
        lines.append(DubLine(i=k, start=round(ws[0].start, 3), end=round(ws[-1].end, 3), original=t, text=t))
    return DubScript(clip=clip.index, lines=lines)


def edit(script: DubScript, voice: str | None = None, version: int | None = None,
         texts: dict[int, str] | None = None) -> DubScript:
    """New voice / version / line texts. A line whose text or voice changed needs generating again."""
    if voice is not None:
        script.voice = voice
    script.version = version if voice is not None or version is not None else script.version
    for ln in script.lines:
        if texts and ln.i in texts:
            ln.text = " ".join(str(texts[ln.i]).split())[:2000]
    return script


def voice_tag(script: DubScript) -> str:
    return f"{script.voice}@v{script.version}" if script.version else f"{script.voice}@newest"


def stale(script: DubScript, ln: DubLine) -> bool:
    """Needs generating: never made, text changed, or made with another voice/version."""
    if not ln.text.strip():
        return False  # silence: nothing to generate
    return ln.file is None or ln.said != ln.text or ln.voice != voice_tag(script)


def _name(script: DubScript, ln: DubLine) -> str:
    h = hashlib.sha1(f"{voice_tag(script)}|{ln.text}".encode()).hexdigest()[:8]
    return f"dub_{script.clip}_{ln.i}_{h}.wav"


def generate(job_dir: Path, script: DubScript, clip: Clip, only: list[int] | None = None,
             on_progress=None) -> DubScript:
    """Speech for the lines that need it (or `only` these), in ONE tts worker load, then Whisper for their word
    timings, then the track. GPU: hold GPU_LOCK. Raises when the voice isn't usable."""
    if not script.voice:
        raise ValueError("choose a voice first")
    v = tts.load(script.voice)  # KeyError → 404
    tts.prompt_for(v, script.version)  # KeyError when that version doesn't exist
    todo = [ln for ln in script.lines if (only is None and stale(script, ln))
            or (only is not None and ln.i in only and ln.text.strip())]
    if todo:
        if on_progress:
            on_progress(0.1, f"Speaking {len(todo)} line(s) in {v['name']}")
        names = [_name(script, ln) for ln in todo]
        made = tts.speak(script.voice, [ln.text for ln in todo], out_dir=job_dir, names=names,
                         version=script.version)
        if on_progress:
            on_progress(0.7, "Timing the new words")
        heard = tts.transcribe_many([job_dir / n for n in names])
        for ln, n, m, h in zip(todo, names, made, heard, strict=True):
            old = ln.file
            ln.file, ln.said, ln.voice, ln.seconds = n, ln.text, voice_tag(script), m["seconds"]
            ln.words = map_words(ln.text, h["words"], m["seconds"])
            if old and old != n:
                (job_dir / old).unlink(missing_ok=True)
    return assemble(job_dir, script, clip.start, clip.end)


def map_words(text: str, heard: list[dict], seconds: float) -> list[Word]:
    """Captions show what you typed, timed by what Whisper heard: same word count → its times; otherwise the
    typed words spread over the speech in proportion to their length."""
    typed = text.split()
    if not typed:
        return []
    if len(heard) == len(typed):
        return [Word(start=h["start"], end=h["end"], word=f" {t}") for h, t in zip(heard, typed, strict=True)]
    lo = heard[0]["start"] if heard else 0.0
    hi = heard[-1]["end"] if heard else seconds
    total = sum(len(t) + 1 for t in typed)
    out, t = [], lo
    for w in typed:
        d = (hi - lo) * (len(w) + 1) / total
        out.append(Word(start=round(t, 3), end=round(t + d * 0.9, 3), word=f" {w}"))
        t += d
    return out


# ------------------------------------------------------------------ the track

def _pcm(f: Path, rate: float = 1.0) -> np.ndarray:
    af = f"atempo={rate:.4f}," if abs(rate - 1) > 1e-3 else ""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-af", f"{af}aresample={SR}", "-ac", "1",
                        "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"can't read {f.name}: {r.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(r.stdout, "<f4").copy()


def _fade(x: np.ndarray, start: float, end: float) -> np.ndarray:
    n_in, n_out = min(int(start * SR), len(x) // 2), min(int(end * SR), len(x) // 2)
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)
    return x


def layout(script: DubScript, clip_start: float, clip_end: float) -> list[tuple[DubLine, float, float, float]]:
    """(line, clip-relative start, speed-up, seconds kept) for the generated lines inside the clip, in order."""
    span = clip_end - clip_start
    inside = sorted([ln for ln in script.lines if clip_start <= ln.start < clip_end], key=lambda x: x.start)
    out, cursor = [], 0.0
    for k, ln in enumerate(inside):
        orig = ln.start - clip_start
        if not ln.file or not ln.text.strip() or not ln.seconds:
            cursor = max(cursor, min(orig, span))
            continue
        nxt = inside[k + 1].start - clip_start if k + 1 < len(inside) else span
        limit = min(nxt + LATE, span) if k + 1 < len(inside) else span
        need = ln.seconds
        start = max(orig, cursor)
        if start + need > limit:  # needs room: start a little early if the line before allows it
            start = max(orig - EARLY, cursor, 0.0)
        room = max(limit - start, 0.05)
        rate = min(max(need / room, 1.0), MAX_RATE) if need > room else 1.0
        dur = need / rate
        kept = dur if dur <= room + TOL else room
        out.append((ln, start, rate, kept))
        cursor = start + kept + GAP
    return out


def assemble(job_dir: Path, script: DubScript, clip_start: float, clip_end: float) -> DubScript:
    """Every generated line laid out (layout()) → dub_N.wav (48 kHz mono, clip_start → clip_end). No GPU."""
    lo, hi = clip_start, clip_end
    track = np.zeros(int((hi - lo) * SR) + 1, dtype=np.float32)
    for ln in script.lines:
        ln.fit, ln.rate, ln.at, ln.over = None, None, None, None
    for ln, at, rate, kept in layout(script, lo, hi):
        if not (job_dir / ln.file).is_file():
            continue
        x = _fade(_pcm(job_dir / ln.file, rate), FADE, FADE)
        cap = int(kept * SR)
        ln.fit = "ok" if rate == 1.0 else "stretched"
        if len(x) > cap + int(TOL * SR):  # still too long at MAX_RATE: cut, with a fade
            ln.over = round((len(x) - cap) / SR, 2)
            x = _fade(x[:cap], 0, TAIL)
            ln.fit = "too_long"
        ln.rate, ln.at = round(rate, 3), round(at, 3)
        a = int(at * SR)
        track[a:a + len(x)] += x[: max(len(track) - a, 0)]
    np.clip(track, -1, 1, out=track)
    out = job_dir / f"dub_{script.clip}.wav"
    with tempfile.NamedTemporaryFile(suffix=".f32", dir=job_dir, delete=False) as tmp:
        tmp.write(track.tobytes())
    try:
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", tmp.name,
                            "-c:a", "pcm_s16le", str(out)], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"couldn't write the dub track: {r.stderr.decode(errors='replace')[-200:]}")
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    script.track, script.span = out.name, (round(lo, 3), round(hi, 3))
    script.track_version += 1
    return script


def segments(script: DubScript, clip_start: float, clip_end: float) -> list[Segment]:
    """The new dialogue as transcript segments in SOURCE time, where layout() put it (captions, .srt, sfx)."""
    out = []
    for ln, at, rate, kept in layout(script, clip_start, clip_end):
        if not ln.words:
            continue
        base = clip_start + at
        ws = [Word(start=round(base + w.start / rate, 3), end=round(min(base + w.end / rate, base + kept), 3),
                   word=w.word) for w in ln.words if w.start / rate < kept]
        if ws:
            out.append(Segment(start=ws[0].start, end=ws[-1].end, text=ln.text, words=ws))
    return out


def track_for(job_dir: Path, clip: Clip) -> tuple[Path, list[Segment]] | None:
    """What render uses when options.dub is on: (dub track, new segments), or None (no track yet)."""
    if not clip.options.dub:
        return None
    script = load(job_dir, clip.index)
    if not script or not script.track or not (job_dir / script.track).is_file():
        return None
    if script.span != (round(clip.start, 3), round(clip.end, 3)):  # trimmed since: lay the lines out again
        script = save(job_dir, assemble(job_dir, script, clip.start, clip.end))
    return job_dir / script.track, segments(script, clip.start, clip.end)
