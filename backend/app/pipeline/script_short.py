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
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import wrap

from ..config import settings
from ..files import atomic_output
from ..models import (
    DubLine,
    DubScript,
    ScriptCaptionOptions,
    ScriptNarrationRequest,
    ScriptShortDraft,
    ScriptShortRequest,
    Word,
)
from . import brand, broll, captions, dub, kokoro, narration_review, script_graphics, sfx, tts
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


def script_lines(text: str) -> list[str]:
    paragraphs = re.split(r"(?<=[.!?。！？।])\s+|\n+", text.strip())
    return [line for paragraph in paragraphs for line in wrap(paragraph, 240, break_long_words=False) if line.strip()]


def _narration_settings(request: ScriptNarrationRequest) -> tuple[str, int | None, list[str], list[str]]:
    if kokoro.is_preset(request.voice):
        lines = script_lines(request.text)
        if not lines:
            raise ValueError("Enter a script to narrate")
        dictionary = dub.pronunciation_dictionary()
        spoken = [dub.spoken_text(
            DubScript(clip=0), DubLine(i=index, start=0, end=0, original=line, text=line), dictionary,
        ) for index, line in enumerate(lines)]
        payload = json.dumps([request.voice, kokoro.fingerprint(request.voice), lines, spoken], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:32], None, lines, spoken

    voice = tts.load(request.voice)
    build = tts.build_for(voice, request.version)
    if not build:
        raise ValueError("Choose a built voice version")
    version = build["version"]
    prompt = tts.prompt_for(voice, version)
    script = DubScript(clip=0, voice=request.voice, version=version)
    lines = script_lines(request.text)
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


def _speak(request: ScriptNarrationRequest, lines: list[str], work: Path, names: list[str],
           version: int | None) -> None:
    if kokoro.is_preset(request.voice):
        kokoro.speak(request.voice, lines, work, names)
    else:
        tts.speak(request.voice, lines, out_dir=work, names=names, version=version)


def _prepare_narration(work: Path, request: ScriptNarrationRequest,
                       version: int | None, lines: list[str], spoken: list[str]) -> dict:
    names = [f"voice-{i}.wav" for i in range(len(lines))]
    _speak(request, spoken, work, names, version)
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


def retake(ident: str, index: int, request: ScriptNarrationRequest) -> dict:
    old = _preview_metadata(ident)
    fingerprint, version, lines, spoken = _narration_settings(request)
    if old.get("source", ident) != fingerprint:
        raise ValueError("The script, voice or pronunciation changed. Generate a fresh preview first.")
    if not 0 <= index < len(lines):
        raise ValueError("Choose a line in this narration preview")
    selected = old["lines"][index]
    start, end = selected["start"], selected["end"]
    updated = uuid.uuid4().hex
    with TemporaryDirectory(prefix=".retake-", dir=_preview_root()) as staging:
        work = Path(staging)
        _speak(request, [spoken[index]], work, ["take.wav"], version)
        take = work / "take.wav"
        seconds = _splice_voice(preview_file(ident), take, work / "voice.wav", start, end)
        delta = seconds - (end - start)
        duration = old["duration"] + delta
        if duration > MAX_SECONDS:
            raise ValueError(
                f"This retake would exceed {MAX_SECONDS} seconds. The previous preview is still available."
            )
        heard = tts.transcribe_many([take])[0]
        selected.update(end=start + seconds, spoken=spoken[index], heard=heard.get("text"),
                        issues=narration_review.compare(spoken[index], heard.get("text")))
        for line in old["lines"][index + 1:]:
            line.update(start=line["start"] + delta, end=line["end"] + delta)
        words = [word for word in old["words"] if word["start"] < start - 1e-7]
        words += [Word(word=word.word, start=start + word.start, end=start + word.end).model_dump()
                  for word in dub.map_words(lines[index], heard["words"], seconds)]
        words += [{**word, "start": word["start"] + delta, "end": word["end"] + delta}
                  for word in old["words"] if word["start"] >= end - 1e-7]
        old.update(source=fingerprint, duration=duration, words=words)
        _publish_preview(work, updated, old)
    return preview_view(updated)


def _splice_voice(original: Path, take: Path, destination: Path, start: float, end: float) -> float:
    with (wave.open(str(original), "rb") as source, wave.open(str(take), "rb") as replacement,
          wave.open(str(destination), "wb") as output):
        params = source.getparams()
        if replacement.getparams()[:3] != params[:3] or not replacement.getnframes():
            raise RuntimeError("The retake audio format does not match the current preview")
        rate = params.framerate
        output.setparams(params)
        output.writeframesraw(source.readframes(round(start * rate)))
        output.writeframesraw(replacement.readframes(replacement.getnframes()))
        source.setpos(min(round(end * rate), source.getnframes()))
        output.writeframesraw(source.readframes(source.getnframes() - source.tell()))
        return replacement.getnframes() / rate


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
    music_track = sfx.music_path(request.music)
    if request.music and music_track is None:
        raise ValueError("The selected music track is no longer available")
    preview_id, version, lines, spoken = _narration_settings(request)
    if request.scene_starts is not None and not request.preview_id:
        raise ValueError("Generate a narration preview before assigning scene timing")
    folder = root().resolve()  # FFmpeg runs from the staging directory.
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
            if narration.get("source", request.preview_id) != preview_id:
                raise ValueError(
                    "The script, voice or pronunciation changed. Generate a fresh preview before rendering."
                )
            shutil.copyfile(audio, work / "voice.wav")
        else:
            narration = _prepare_narration(work, request, version, lines, spoken)
        duration = narration["duration"]
        frames = _scene_frames(narration, len(request.assets), request.scene_starts)
        words = [Word.model_validate(word) for word in narration["words"]]
        graphics = script_graphics.make_ass(request.graphics, narration) if request.graphics else None
        _render(work, output, [library[key][0] for key in request.assets], words, frames, request,
                music_track, duration, graphics)
        metadata = {"id": ident, "title": request.title, "duration": round(duration, 2), "created": time.time(),
                    "voice": request.voice, "version": version, "music": request.music,
                    "music_gain_db": request.music_gain_db}
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


def preview_mix(ident: str, music_name: str, gain: float) -> bytes:
    narration = _preview_metadata(ident)
    track = sfx.music_path(music_name)
    if track is None:
        raise ValueError("The selected music track is no longer available")
    duration = narration["duration"]
    with TemporaryDirectory(prefix="clipforge-mix-") as directory:
        work = Path(directory)
        _ffmpeg(work, [
            "-i", str(preview_file(ident).resolve()), "-stream_loop", "-1", "-i", str(track.resolve()),
            "-filter_complex", sfx.audio_graph([], 1, None, duration, music_gain_db=gain),
            "-map", "[aout]", "-t", str(min(duration, 10)), "-c:a", "aac", "-b:a", "160k", "preview.m4a",
        ])
        return (work / "preview.m4a").read_bytes()


def _render(work: Path, output: Path, visuals: list[Path], words: list[Word], boundaries: list[int],
            options: ScriptCaptionOptions, music_track: Path | None, duration: float,
            graphics: tuple[str, str] | None) -> None:
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
    filters = []
    if options.caption_style != "none":
        preset = get_preset(options.caption_preset)
        kit = brand.load()
        if kit.enabled and kit.accent and kit.accent_captions:
            preset = replace(preset, active=brand.ass_colour(kit.accent))
        font = font_for("".join(word.word for word in words), preset.font)
        ass = captions.build_ass(words, [], preset=preset, font=font, position=options.caption_position,
                                 animate=options.caption_style == "karaoke")
        (work / "captions.ass").write_text(ass, encoding="utf-8")
        filters.append(f"ass=captions.ass{fontsdir_option(font)}")
    if graphics:
        ass, font = graphics
        (work / "graphics.ass").write_text(ass, encoding="utf-8")
        filters.insert(0, f"ass=graphics.ass{fontsdir_option(font)}")
    (output / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
    music_input = ["-stream_loop", "-1", "-i", str(music_track.resolve())] if music_track else []
    audio = (["-filter_complex", sfx.audio_graph([], 2, None, duration, voice_src="1:a",
                                                            music_gain_db=options.music_gain_db), "-map", "[aout]"]
             if music_track else ["-map", "1:a:0"])
    _ffmpeg(work, [
        "-f", "concat", "-safe", "1", "-i", "scenes.txt", "-i", "voice.wav", *music_input,
        "-map", "0:v:0", *audio, *(["-vf", ",".join(filters)] if filters else []),
        "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output / "short.mp4"),
    ])
