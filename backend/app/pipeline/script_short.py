"""Make a vertical, captioned short from a script, an existing voice and ordered local B-roll."""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import wrap

from ..config import settings
from ..files import atomic_output
from ..models import DubLine, DubScript, ScriptNarrationRequest, ScriptShortDraft, ScriptShortRequest, Word
from . import broll, captions, dub, narration_review, tts
from .fonts import font_for, fontsdir_option
from .presets import get_preset

MAX_SECONDS = 60
FPS = 24
SIZE = "720:1280"
_ID = re.compile(r"[0-9a-f]{10}")
_PREVIEW_ID = re.compile(r"[0-9a-f]{32}")
_MAX_PREVIEWS = 12


def root() -> Path:
    return settings.data_dir / "script_shorts"


def file(ident: str, name: str) -> Path:
    if not _ID.fullmatch(ident) or name not in ("short.mp4", "captions.srt"):
        raise KeyError(ident)
    path = root() / ident / name
    if not path.is_file():
        raise KeyError(ident)
    return path


def recent() -> list[dict]:
    result = []
    for path in root().glob("*/short.json"):
        if not _ID.fullmatch(path.parent.name):
            continue
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["created"], reverse=True)


def drafts() -> list[dict]:
    result = []
    for path in (root() / "_drafts").glob("*.json"):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(result, key=lambda item: item["updated"], reverse=True)


def save_draft(request: ScriptShortDraft) -> dict:
    folder = root() / "_drafts"
    folder.mkdir(parents=True, exist_ok=True)
    ident = request.id or uuid.uuid4().hex[:10]
    record = {**request.model_dump(), "id": ident, "updated": time.time()}
    with atomic_output(folder / f"{ident}.json") as staged:
        staged.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def assets() -> list[dict[str, str]]:
    return [{"id": ident, "name": name.as_posix()}
            for ident, (_, name) in sorted(broll.library_assets().items(), key=lambda item: str(item[1][1]).casefold())]


def _preview_root() -> Path:
    return root() / "_narration"


def preview_file(ident: str) -> Path:
    if not _PREVIEW_ID.fullmatch(ident):
        raise KeyError(ident)
    path = _preview_root() / ident / "voice.wav"
    if not path.is_file():
        raise KeyError(ident)
    return path


def _narration_settings(request: ScriptNarrationRequest) -> tuple[str, int, list[str], list[str]]:
    voice = tts.load(request.voice)
    build = tts.build_for(voice, request.version)
    if not build:
        raise ValueError("Choose a built voice version")
    version = build["version"]
    prompt = tts.prompt_for(voice, version)
    script = DubScript(clip=0, voice=request.voice, version=version)
    paragraphs = re.split(r"(?<=[.!?。！？।])\s+|\n+", request.text.strip())
    lines = [line for paragraph in paragraphs for line in wrap(paragraph, 240, break_long_words=False) if line.strip()]
    if not lines:
        raise ValueError("Enter a script to narrate")
    dictionary = dub.pronunciation_dictionary()
    spoken = [dub.spoken_text(
        script, DubLine(i=i, start=0, end=0, original=text, text=text), dictionary,
    ) for i, text in enumerate(lines)]
    # Any change that can alter the recording invalidates a previously approved preview.
    stamp = prompt.stat().st_mtime_ns if isinstance(prompt, Path) and prompt.is_file() else 0
    payload = json.dumps([request.voice, version, stamp, voice.get("language") or settings.tts_language,
                          settings.tts_model, lines, spoken], ensure_ascii=False)
    ident = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return ident, version, lines, spoken


def _prepare_narration(work: Path, request: ScriptNarrationRequest,
                       version: int, lines: list[str], spoken: list[str]) -> dict:
    names = [f"voice-{i}.wav" for i in range(len(lines))]
    tts.speak(request.voice, spoken, out_dir=work, names=names, version=version)
    takes = [work / name for name in names]
    timing = _join_voice(takes, work / "voice.wav")
    duration = timing[-1][0] + timing[-1][1]
    if duration > MAX_SECONDS:
        raise ValueError(f"Narration runs {duration:.1f} seconds. Shorten the script to fit {MAX_SECONDS} seconds.")
    aligned = tts.transcribe_many(takes)
    words = [
        Word(word=word.word, start=start + word.start, end=start + word.end).model_dump()
        for text, (start, seconds), heard in zip(lines, timing, aligned, strict=True)
        for word in dub.map_words(text, heard["words"], seconds)
    ]
    return {"duration": duration, "version": version, "words": words,
            "lines": [{"text": text, "start": start, "end": start + seconds, "spoken": intended,
                       "heard": heard.get("text"), "issues": narration_review.compare(intended, heard.get("text"))}
                      for text, (start, seconds), intended, heard in zip(lines, timing, spoken, aligned, strict=True)]}


def _preview_metadata(ident: str) -> dict:
    path = preview_file(ident).parent / "narration.json"
    return json.loads(path.read_text(encoding="utf-8"))


def preview_view(ident: str) -> dict:
    metadata = _preview_metadata(ident)
    return {"id": ident, "duration": metadata["duration"], "version": metadata["version"], "lines": metadata["lines"]}


def preview_lines(ident: str) -> list[dict]:
    return _preview_metadata(ident)["lines"]


def _publish_preview(work: Path, ident: str, metadata: dict) -> None:
    directory = _preview_root()
    saved = directory / ident
    ready = work / "ready"
    ready.mkdir()
    (work / "voice.wav").replace(ready / "voice.wav")
    (ready / "narration.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    ready.replace(saved)
    pinned = {draft.get("preview_id") for draft in drafts()}
    previous = sorted((p for p in directory.iterdir() if p.is_dir() and _PREVIEW_ID.fullmatch(p.name)
                       and p != saved and p.name not in pinned), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in previous[_MAX_PREVIEWS - 1:]:
        shutil.rmtree(old)


def preview(request: ScriptNarrationRequest) -> dict:
    ident, version, lines, spoken = _narration_settings(request)
    directory = _preview_root()
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / ident
    if saved.is_dir():
        os.utime(saved)
    else:
        with TemporaryDirectory(prefix=".preview-", dir=directory) as staging:
            work = Path(staging)
            metadata = _prepare_narration(work, request, version, lines, spoken)
            _publish_preview(work, ident, metadata)
    return preview_view(ident)


def _scene_frames(metadata: dict, count: int, starts: list[int] | None) -> list[int]:
    frame_total = max(count, math.ceil(metadata["duration"] * FPS))
    if starts is None:
        return [i * frame_total // count for i in range(count + 1)]
    lines = metadata["lines"]
    if (len(starts) != count or starts[0] != 0 or starts != sorted(set(starts))
            or any(not 0 <= index < len(lines) for index in starts)):
        raise ValueError("Assign each scene a different script line in order, starting with the first line")
    frames = [round(lines[index]["start"] * FPS) for index in starts] + [frame_total]
    if any(right <= left for left, right in zip(frames, frames[1:], strict=False)):
        raise ValueError("Two scene cuts are too close. Choose different starting lines.")
    return frames


def create(request: ScriptShortRequest) -> dict:
    library = broll.library_assets()
    if any(ident not in library for ident in request.assets):
        raise ValueError("One of the selected B-roll files is no longer available")
    preview_id, version, lines, spoken = _narration_settings(request)
    if request.preview_id and request.preview_id != preview_id:
        raise ValueError("The script, voice or pronunciation changed. Generate a fresh preview.")
    if request.scene_starts is not None and not request.preview_id:
        raise ValueError("Generate a narration preview before assigning scene timing")
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex[:10]
    with TemporaryDirectory(prefix=".script-short-", dir=folder) as staging:
        work = Path(staging)
        output = work / "output"
        output.mkdir()
        if request.preview_id:
            try:
                audio = preview_file(request.preview_id)
                narration = _preview_metadata(request.preview_id)
            except KeyError as error:
                raise ValueError("That preview expired. Generate a fresh narration preview.") from error
            shutil.copyfile(audio, work / "voice.wav")
        else:
            narration = _prepare_narration(work, request, version, lines, spoken)
        duration = narration["duration"]
        frames = _scene_frames(narration, len(request.assets), request.scene_starts)
        words = [Word.model_validate(word) for word in narration["words"]]
        _render(work, output, [library[key][0] for key in request.assets], words, frames)
        metadata = {"id": ident, "title": request.title, "duration": round(duration, 2), "created": time.time(),
                    "voice": request.voice, "version": version}
        (output / "short.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        output.replace(folder / ident)
    return metadata


def _join_voice(takes: list[Path], destination: Path) -> list[tuple[float, float]]:
    timing = []
    frames = 0
    with wave.open(str(takes[0]), "rb") as first:
        channels, width, rate = first.getnchannels(), first.getsampwidth(), first.getframerate()
    with wave.open(str(destination), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        for take in takes:
            with wave.open(str(take), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (channels, width, rate):
                    raise RuntimeError("Generated narration files use incompatible audio formats")
                length = source.getnframes()
                if not length:
                    raise RuntimeError("Voice generation returned an empty line")
                timing.append((frames / rate, length / rate))
                stream.writeframesraw(source.readframes(length))
                frames += length
    return timing


def _ffmpeg(work: Path, arguments: list[str]) -> None:
    try:
        result = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *arguments],
                                cwd=work, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Could not finish rendering this short") from error
    if result.returncode:
        raise RuntimeError(f"Could not render this short: {result.stderr.decode(errors='replace')[-240:].strip()}")


def _render(work: Path, output: Path, visuals: list[Path], words: list[Word], boundaries: list[int]) -> None:
    names = []
    for index, asset in enumerate(visuals):
        frames = boundaries[index + 1] - boundaries[index]
        name = f"scene-{index}.mp4"
        _ffmpeg(work, [
            "-stream_loop", "-1", "-protocol_whitelist", "file", "-f", "mov", "-threads", "1", "-i", str(asset),
            "-map", "0:v:0", "-an", "-vf", f"fps={FPS},scale={SIZE}:force_original_aspect_ratio=increase,"
            f"crop={SIZE},setsar=1", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "2", "-pix_fmt", "yuv420p", name,
        ])
        names.append(name)
    (work / "scenes.txt").write_text("".join(f"file '{name}'\n" for name in names), encoding="ascii")
    preset = get_preset()
    font = font_for("".join(word.word for word in words), preset.font)
    (work / "captions.ass").write_text(captions.build_ass(words, [], preset=preset, font=font, animate=False),
                                       encoding="utf-8")
    (output / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
    _ffmpeg(work, [
        "-f", "concat", "-safe", "1", "-i", "scenes.txt", "-i", "voice.wav",
        "-map", "0:v:0", "-map", "1:a:0", "-vf", f"ass=captions.ass{fontsdir_option(font)}",
        "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output / "short.mp4"),
    ])
