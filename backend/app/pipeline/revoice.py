"""Re-voice a recording (Phase 3, text-based): your narration (audio or video) → Whisper → each sentence spoken in a
Voice Library voice at your timing → the same recording with the new voice (video kept, audio replaced).

Keeps your timing and pauses (every sentence starts where you said it — dub.layout); does NOT keep your intonation:
the voice model reads the words in its own delivery. True voice conversion (your delivery, another timbre) needs a
voice-conversion model (OpenVoice V2 / Seed-VC class) — not added; see CLAUDE.md roadmap.

data/revoice/<id>/
  meta.json      name, created, source file, has_video, duration, output
  source.<ext>   what you recorded / imported
  words.json     Whisper words of it
  dub_1.json     the DubScript (lines = your sentences; edit them before generating) — the same as a clip's
  dub_1_*.wav    generated lines;  dub_1.wav  the laid-out track (dub.assemble on 0 → duration)
  revoiced.mp4 / revoiced.wav   the result
GPU work (Whisper, the voice worker) runs under GPU_LOCK in the API (gpu_work).
"""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from ..config import settings
from ..files import atomic_output
from ..models import Clip, DubScript, Word
from . import capture, dub, tts

MAX_SECONDS = 180.0  # generation runs inside one request (~2 s per second of speech)
MAX_UPLOAD = 500 * 2**20
_ID = re.compile(r"^[0-9a-f]{10}$")


def root() -> Path:
    return settings.data_dir / "revoice"


def folder(rid: str) -> Path:
    if not _ID.match(rid or ""):
        raise KeyError(rid)
    d = root() / rid
    if not (d / "meta.json").is_file():
        raise KeyError(rid)
    return d


def meta(rid: str) -> dict:
    return json.loads((folder(rid) / "meta.json").read_text(encoding="utf-8"))


def _save_meta(d: Path, m: dict) -> dict:
    with atomic_output(d / "meta.json") as staged:
        staged.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    return m


def probe(path: Path) -> tuple[float, bool, bool]:
    """(duration, has video, has audio)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        info = json.loads(r.stdout or "{}")
        kinds = {s.get("codec_type") for s in info.get("streams", [])}
        return float(info.get("format", {}).get("duration") or 0), "video" in kinds, "audio" in kinds
    except (ValueError, TypeError):
        return 0.0, False, False


def all_items() -> list[dict]:
    if not root().is_dir():
        return []
    out = []
    for f in sorted(root().glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def create(filename: str, data: bytes, name: str = "", *, recorded: bool = False) -> dict:
    """Save the recording, transcribe it (GPU: hold GPU_LOCK) and draft its sentences."""
    rid = uuid.uuid4().hex[:10]
    d = root() / rid
    d.mkdir(parents=True)
    suffix = Path(filename).suffix.lower()
    ext = suffix if re.fullmatch(r"\.[a-z0-9]{1,7}", suffix) else ".bin"  # only a plain extension from the name
    src = d / f"{'capture' if recorded else 'source'}{ext}"
    src.write_bytes(data)
    try:
        if recorded:
            normalized = d / "source.mp4"
            capture.normalize(src, normalized)
            src.unlink()
            src = normalized
        duration, has_video, has_audio = probe(src)
        if not has_audio or duration <= 0:
            raise ValueError("no audio in that file")
        if duration > MAX_SECONDS:
            raise ValueError(f"keep it under {MAX_SECONDS / 60:.0f} minutes ({duration / 60:.1f} min)")
        heard = tts.transcribe_many([src])[0]
        words = [Word(start=w["start"], end=w["end"], word=w["word"]) for w in heard["words"]]
        if not words:
            raise ValueError("no speech found in that recording")
        (d / "words.json").write_text(json.dumps([w.model_dump() for w in words]), encoding="utf-8")
        dub.save(d, draft(words, duration))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return _save_meta(d, {"id": rid, "name": " ".join(name.split())[:80] or Path(filename).stem[:80],
                          "created": time.time(), "source": src.name, "has_video": has_video,
                          "duration": round(duration, 2), "language": heard.get("language"), "output": None})


def draft(words: list[Word], duration: float) -> DubScript:
    from ..models import DubLine
    from .judge import _text, sentences

    lines = []
    for k, ws in enumerate(sentences([(0, w) for w in words])):
        t = _text(ws)
        lines.append(DubLine(i=k, start=round(ws[0].start, 3), end=round(ws[-1].end, 3), original=t, text=t))
    return DubScript(clip=1, lines=lines)


def as_clip(m: dict) -> Clip:
    return Clip(index=1, start=0.0, end=float(m["duration"]), title=m["name"] or "recording", hook="", score=5)


def script(rid: str) -> DubScript:
    s = dub.load(folder(rid), 1)
    if s is None:
        raise KeyError(rid)
    return s


def edit(rid: str, voice: str | None, version: int | None, texts: dict[int, str]) -> DubScript:
    if voice:
        tts.load(voice)  # KeyError → 404
    return dub.save(folder(rid), dub.edit(script(rid), voice, version, texts))


def generate(rid: str, only: list[int] | None = None) -> dict:
    """Speak what needs it (GPU: hold GPU_LOCK), lay it on your timing, write the result."""
    d, m = folder(rid), meta(rid)
    s = dub.save(d, dub.generate(d, script(rid), as_clip(m), only))
    out = render(d, m, s)
    m["output"], m["voice"], m["version"], m["updated"] = out.name, s.voice, s.version, time.time()
    return _save_meta(d, m)


def render(d: Path, m: dict, s: DubScript) -> Path:
    """Video → same picture (copied, no re-encode) + the new voice; audio → the voice track."""
    track = d / s.track
    if not m["has_video"]:
        out = d / "revoiced.wav"
        shutil.copyfile(track, out)
        return out
    out = d / "revoiced.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(d / m["source"]), "-i", str(track),
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                        "-t", f"{m['duration']:.3f}", "-movflags", "+faststart", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:  # e.g. a video codec mp4 can't hold: encode it
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(d / m["source"]), "-i", str(track),
                            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                            "-c:a", "aac", "-b:a", "160k", "-t", f"{m['duration']:.3f}", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"couldn't write the video: {r.stderr.strip()[-200:]}")
    return out


def pickup(rid: str, line_id: int, *, expected_text: str | None = None) -> dict:
    current = next((line.text for line in script(rid).lines if line.i == line_id), "")
    return pickups(rid, {line_id: current if expected_text is None else expected_text})


def pickups(rid: str, expected_texts: dict[int, str]) -> dict:
    """Synthesize selected sentences in one load, then replace their windows in the original recording."""
    d, m, s = folder(rid), meta(rid), script(rid)
    by_id = {line.i: line for line in s.lines}
    windows = []
    for index, expected in expected_texts.items():
        line = by_id.get(index)
        if line is None or not line.text.strip():
            raise ValueError("Choose non-empty sentences to replace")
        if line.text != expected:
            raise ValueError("A sentence changed. Review the current wording and try again.")
        start, end = max(0, line.start), min(line.end, m["duration"])
        if end - start < 0.1:
            raise ValueError(f"Sentence {index + 1} has too little time for a pickup")
        windows.append((start, end, line))
    windows.sort(key=lambda window: window[0])
    if any(before[1] > after[0] for before, after in zip(windows, windows[1:], strict=False)):
        raise ValueError("Selected sentences overlap. Choose separate audio windows.")
    if not windows or not s.voice:
        raise ValueError("Choose sentences and a saved voice first")
    tts.prompt_for(tts.load(s.voice), s.version)
    with TemporaryDirectory(prefix=".pickup-", dir=d) as folder_name:
        directory = Path(folder_name)
        dictionary = dub.pronunciation_dictionary()
        texts = [dub.spoken_text(s, line, dictionary) for _, _, line in windows]
        names = [f"take-{line.i}.wav" for _, _, line in windows]
        takes = tts.speak(s.voice, texts, out_dir=directory, names=names, version=s.version)
        planned = []
        for (start, end, line), name, take in zip(windows, names, takes, strict=True):
            rate = max(1, take["seconds"] / (end - start))
            if rate > dub.MAX_RATE + 1e-6:
                raise ValueError(f"Sentence {line.i + 1} would sound rushed. Shorten its wording and try again.")
            planned.append((directory / name, start, end, rate))
        out = d / ("pickup.mp4" if m["has_video"] else "pickup.wav")
        try:
            _render_pickups(d / m["source"], planned, out, m["has_video"])
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Could not finish exporting the sentence pickups") from error
    indices = [line.i for _, _, line in windows]
    m.update(pickup_output=out.name, pickup_lines=indices, pickup_line=indices[0], pickup_updated=time.time())
    return _save_meta(d, m)


def _render_pickups(source: Path, planned: list[tuple[Path, float, float, float]], out: Path, video: bool) -> None:
    fade = 0.015
    gains, filters, inputs = [], [], []
    for index, (take, start, end, rate) in enumerate(planned, 1):
        span = end - start
        gains.append(f"max(clip(({start + fade:.6f}-t)/{fade},0,1),clip((t-{end - fade:.6f})/{fade},0,1))")
        inputs.extend(["-i", str(take)])
        filters.append(
            f"[{index}:a:0]atempo={rate:.6f},aresample=48000,apad,atrim=duration={span:.6f},"
            f"afade=t=in:d={fade},afade=t=out:st={span - fade:.6f}:d={fade},"
            f"adelay={round(start * 48000)}S:all=1[p{index}]"
        )
    input_labels = "[original]" + "".join(f"[p{index}]" for index in range(1, len(planned) + 1))
    graph = ";".join([
        f"[0:a:0]asetpts=PTS-STARTPTS,aeval='val(ch)*{'*'.join(gains)}':c=same[original]",
        *filters, f"{input_labels}amix=inputs={len(planned) + 1}:duration=first:normalize=0[a]",
    ])
    command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(source), *inputs,
               "-filter_complex", graph, "-map", "[a]"]
    with atomic_output(out) as staged:
        if video:
            tracks = ["-map", "0:v:0", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
            result = subprocess.run([*command, *tracks, "-c:v", "copy", str(staged)], capture_output=True, timeout=180)
            if result.returncode:
                result = subprocess.run(
                    [*command, *tracks, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                     "-threads:v", "2", str(staged)], capture_output=True, timeout=180,
                )
        else:
            result = subprocess.run([*command, "-c:a", "pcm_s16le", str(staged)], capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(f"Could not export the pickup: {result.stderr.decode(errors='replace')[-200:].strip()}")


def delete(rid: str) -> None:
    shutil.rmtree(folder(rid), ignore_errors=True)
