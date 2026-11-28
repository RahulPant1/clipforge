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
from contextlib import contextmanager
from dataclasses import replace
from difflib import Match, SequenceMatcher
from io import BytesIO
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
from . import brand, broll, captions, dub, kokoro, narration_review, script_graphics, sfx, studio, tts
from .fonts import font_for, fontsdir_option
from .presets import get_preset

MAX_SECONDS = 60
FPS = 24
SIZE = "720:1280"
_ID = re.compile(r"[0-9a-f]{10}")
_PREVIEW_ID = re.compile(r"[0-9a-f]{32}")
_MAX_PREVIEWS = 12
_CUT_LIMIT = 4
_CUT_TTL = 3600
_CUT_SESSION = uuid.uuid4().hex


def root() -> Path:
    return settings.data_dir / "script_shorts"


def file(ident: str, name: str) -> Path:
    files = ("short.mp4", "captions.srt", "thumbnail.jpg", "youtube.txt", "youtube-package.zip")
    if not _ID.fullmatch(ident) or name not in files:
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


def _narration_settings(request: ScriptNarrationRequest) -> tuple[str, int | None, list[str], list[str], str]:
    if kokoro.is_preset(request.voice):
        lines = script_lines(request.text)
        if not lines:
            raise ValueError("Enter a script to narrate")
        dictionary = dub.pronunciation_dictionary()
        spoken = [dub.spoken_text(
            DubScript(clip=0), DubLine(i=index, start=0, end=0, original=line, text=line), dictionary,
        ) for index, line in enumerate(lines)]
        identity = [request.voice, kokoro.fingerprint(request.voice)]
        payload = json.dumps([*identity, lines, spoken], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:32], None, lines, spoken, _voice_key(identity)

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
    identity = [request.voice, version, stamp, voice.get("language") or settings.tts_language, settings.tts_model]
    payload = json.dumps([*identity, lines, spoken], ensure_ascii=False)
    ident = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return ident, version, lines, spoken, _voice_key(identity)


def _voice_key(identity: list) -> str:
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _take_id(line: dict, ident: str, index: int) -> str:
    return line.get("take_id") or f"{ident}:{index}"


def _prior_take(line: dict, ident: str, index: int) -> dict:
    return {"preview_id": ident, "line": index, "text": line["text"],
            "take_id": _take_id(line, ident, index)}


def _history(line: dict, ident: str, index: int, current: str) -> list[dict]:
    result = []
    seen = {current}
    for item in [_prior_take(line, ident, index), *line.get("history", [])]:
        if item["take_id"] not in seen:
            seen.add(item["take_id"])
            result.append(item)
    return result[:4]


def _line_words(metadata: dict, line: dict) -> list[dict]:
    start, end = line["start"], line["end"]
    return [{**word, "start": max(0, word["start"] - start), "end": word["end"] - start}
            for word in metadata["words"] if start - 1e-7 <= word["start"] < end - 1e-7]


def _write_line_audio(source, line: dict, output) -> None:
    rate = source.getframerate()
    start, end = round(line["start"] * rate), round(line["end"] * rate)
    source.setpos(start)
    output.setparams(source.getparams())
    output.writeframes(source.readframes(end - start))


def line_audio(ident: str, index: int) -> bytes:
    lines = _preview_metadata(ident)["lines"]
    if not 0 <= index < len(lines):
        raise KeyError(index)
    data = BytesIO()
    with wave.open(str(preview_file(ident)), "rb") as source, wave.open(data, "wb") as output:
        _write_line_audio(source, lines[index], output)
    return data.getvalue()


def _reuse_lines(request, voice_key, lines, spoken, takes):
    ident = request.previous_id
    if not ident:
        return {}
    try:
        old = _preview_metadata(ident)
    except KeyError:
        return {}
    old_key = old.get("voice_key")
    if old_key is None:
        # Older takes can still be reused if their original fingerprint matches the current voice settings.
        original = request.model_copy(update={"text": "\n".join(line["text"] for line in old["lines"])})
        source, _, _, _, verified_key = _narration_settings(original)
        if old.get("source", ident) == source:
            old_key = verified_key
    if old_key != voice_key:
        return {}
    previous = [(item["text"], item["spoken"]) for item in old["lines"]]
    current = list(zip(lines, spoken, strict=True))
    reused = {}
    with wave.open(str(preview_file(ident)), "rb") as source:
        same_length = len(previous) == len(current)
        if same_length and sum(a != b for a, b in zip(previous, current, strict=True)) == 1:
            blocks = [Match(i, i, 1) for i, item in enumerate(current) if item == previous[i]]
        else:
            blocks = SequenceMatcher(None, previous, current, autojunk=False).get_matching_blocks()
        for old_start, new_start, count in blocks:
            for offset in range(count):
                old_index = old_start + offset
                line = old["lines"][old_index]
                with wave.open(str(takes[new_start + offset]), "wb") as output:
                    _write_line_audio(source, line, output)
                reused[new_start + offset] = (
                    {**line, "take_id": _take_id(line, ident, old_index)}, _line_words(old, line),
                )
    return reused


def _speak(request: ScriptNarrationRequest, lines: list[str], work: Path, names: list[str],
           version: int | None) -> None:
    if kokoro.is_preset(request.voice):
        kokoro.speak(request.voice, lines, work, names)
    else:
        tts.speak(request.voice, lines, out_dir=work, names=names, version=version)


def _prepare_narration(work: Path, request: ScriptNarrationRequest, version: int | None,
                       lines: list[str], spoken: list[str], voice_key: str) -> dict:
    names = [f"voice-{i}.wav" for i in range(len(lines))]
    takes = [work / name for name in names]
    reused = _reuse_lines(request, voice_key, lines, spoken, takes)
    missing = [i for i in range(len(lines)) if i not in reused]
    if missing:
        _speak(request, [spoken[i] for i in missing], work, [names[i] for i in missing], version)
    timing = _join_voice(takes, work / "voice.wav")
    duration = sum(timing[-1])
    if duration > MAX_SECONDS:
        raise ValueError(f"Narration runs {duration:.1f} seconds. Shorten the script to fit {MAX_SECONDS} seconds.")
    aligned = dict(zip(missing, tts.transcribe_many([takes[i] for i in missing]) if missing else [], strict=True))
    metadata, words = [], []
    previous = None
    if request.previous_id:
        try:
            candidate = _preview_metadata(request.previous_id)
            if candidate.get("voice_key") == voice_key and len(candidate["lines"]) == len(lines):
                previous = candidate
        except KeyError:
            pass
    for i, (start, seconds) in enumerate(timing):
        if i in reused:
            line, relative_words = reused[i]
        else:
            heard = aligned[i]
            take_id = uuid.uuid4().hex
            line = {"take_id": take_id, "text": lines[i], "spoken": spoken[i], "heard": heard.get("text"),
                    "issues": narration_review.compare(spoken[i], heard.get("text"))}
            if previous:
                line["history"] = _history(previous["lines"][i], request.previous_id, i, take_id)
            relative_words = [word.model_dump() for word in dub.map_words(lines[i], heard["words"], seconds)]
        metadata.append({**line, "start": start, "end": start + seconds})
        words.extend({**word, "start": start + word["start"], "end": start + word["end"]}
                     for word in relative_words)
    return {"duration": duration, "version": version, "voice_key": voice_key, "words": words,
            "lines": metadata, "reused_lines": len(reused)}


def _preview_metadata(ident: str) -> dict:
    path = preview_file(ident).parent / "narration.json"
    return json.loads(path.read_text(encoding="utf-8"))


def preview_view(ident: str) -> dict:
    metadata = _preview_metadata(ident)
    return {"id": ident, "duration": metadata["duration"], "version": metadata["version"],
            "lines": [{**line, "take_id": _take_id(line, ident, index)}
                      for index, line in enumerate(metadata["lines"])],
            "reused_lines": metadata.get("reused_lines", 0)}


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
    pinned = {draft.get(key) for draft in drafts() for key in ("preview_id", "previous_id")}
    retained = [metadata]
    for pinned_id in pinned - {None, ident}:
        try:
            retained.append(_preview_metadata(pinned_id))
        except KeyError:
            continue
    pinned.update(item["preview_id"] for take in retained for line in take["lines"]
                  for item in line.get("history", []))
    previous = sorted((p for p in directory.iterdir() if p.is_dir() and _PREVIEW_ID.fullmatch(p.name)
                       and p != saved and p.name not in pinned), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in previous[_MAX_PREVIEWS - 1:]:
        shutil.rmtree(old)


def preview(request: ScriptNarrationRequest) -> dict:
    source, version, lines, spoken, voice_key = _narration_settings(request)
    ident = source if not request.previous_id else _voice_key([source, request.previous_id])[:32]
    directory = _preview_root()
    directory.mkdir(parents=True, exist_ok=True)
    saved = directory / ident
    if saved.is_dir():
        os.utime(saved)
    else:
        with TemporaryDirectory(prefix=".preview-", dir=directory) as staging:
            work = Path(staging)
            metadata = _prepare_narration(work, request, version, lines, spoken, voice_key)
            _publish_preview(work, ident, {**metadata, "source": source})
    return preview_view(ident)


def retake(ident: str, index: int, request: ScriptNarrationRequest,
           restore: tuple[str, int] | None = None) -> dict:
    old = _preview_metadata(ident)
    fingerprint, version, lines, spoken, voice_key = _narration_settings(request)
    if old.get("source", ident) != fingerprint:
        raise ValueError("The script, voice or pronunciation changed. Generate a fresh preview first.")
    if not 0 <= index < len(lines):
        raise ValueError("Choose a line in this narration preview")
    for line_index, line in enumerate(old["lines"]):
        line.setdefault("take_id", _take_id(line, ident, line_index))
    selected = old["lines"][index]
    start, end = selected["start"], selected["end"]
    updated = uuid.uuid4().hex
    with TemporaryDirectory(prefix=".retake-", dir=_preview_root()) as staging:
        work = Path(staging)
        take = work / "take.wav"
        take_id = uuid.uuid4().hex
        if restore:
            source_id, source_index = restore
            source = _preview_metadata(source_id)
            if not 0 <= source_index < len(source["lines"]):
                raise KeyError(source_index)
            former = source["lines"][source_index]
            if (source.get("voice_key") != voice_key or former["text"] != lines[index]
                    or former["spoken"] != spoken[index]):
                raise ValueError("Only takes with the same wording and voice can be restored")
            take.write_bytes(line_audio(source_id, source_index))
            take_id = _take_id(former, source_id, source_index)
            line_details = {"heard": former.get("heard"), "issues": former.get("issues", [])}
            relative_words = _line_words(source, former)
        else:
            _speak(request, [spoken[index]], work, [take.name], version)
            heard = tts.transcribe_many([take])[0]
            line_details = {"heard": heard.get("text"),
                            "issues": narration_review.compare(spoken[index], heard.get("text"))}
            with wave.open(str(take), "rb") as stream:
                seconds = stream.getnframes() / stream.getframerate()
            relative_words = [word.model_dump() for word in dub.map_words(lines[index], heard["words"], seconds)]
        seconds = _splice_voice(preview_file(ident), take, work / "voice.wav", start, end)
        delta = seconds - (end - start)
        duration = old["duration"] + delta
        if duration > MAX_SECONDS:
            raise ValueError(
                f"This retake would exceed {MAX_SECONDS} seconds. The previous preview is still available."
            )
        selected.update(end=start + seconds, spoken=spoken[index], take_id=take_id,
                        history=_history(selected, ident, index, take_id), **line_details)
        for line in old["lines"][index + 1:]:
            line.update(start=line["start"] + delta, end=line["end"] + delta)
        words = [word for word in old["words"] if word["start"] < start - 1e-7]
        words += [{**word, "start": start + word["start"], "end": start + word["end"]}
                  for word in relative_words]
        words += [{**word, "start": word["start"] + delta, "end": word["end"] + delta}
                  for word in old["words"] if word["start"] >= end - 1e-7]
        old.update(source=fingerprint, duration=duration, words=words, voice_key=voice_key, reused_lines=len(lines) - 1)
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


@contextmanager
def _render_cut(request: ScriptShortRequest, scene_index: int | None = None):
    if scene_index is not None and not 0 <= scene_index < len(request.assets):
        raise ValueError("Choose a scene in this short")
    library = broll.library_assets()
    if any(ident not in library for ident in request.assets):
        raise ValueError("One of the selected B-roll files is no longer available")
    music_track = sfx.music_path(request.music)
    if request.music and music_track is None:
        raise ValueError("The selected music track is no longer available")
    preview_id, version, lines, spoken, voice_key = _narration_settings(request)
    if request.scene_starts is not None and not request.preview_id:
        raise ValueError("Generate a narration preview before assigning scene timing")
    folder = root().resolve()  # FFmpeg runs from the staging directory.
    folder.mkdir(parents=True, exist_ok=True)
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
            narration = _prepare_narration(work, request, version, lines, spoken, voice_key)
        duration = narration["duration"]
        frames = _scene_frames(narration, len(request.assets), request.scene_starts)
        words = [Word.model_validate(word) for word in narration["words"]]
        graphics = script_graphics.make_ass(request.graphics, narration) if request.graphics else None
        visuals = [library[key][0] for key in request.assets]
        span = None
        if scene_index is not None:
            first, last = frames[scene_index:scene_index + 2]
            span = first / FPS, last / FPS
            visuals = [visuals[scene_index]]
            frames = [0, last - first]
        _render(work, output, visuals, words, frames, request, music_track, duration, graphics, span=span)
        yield output, duration, version, frames


def _cut_key(request: ScriptShortRequest) -> str | None:
    if not request.preview_id:
        return None
    try:
        library = broll.library_assets()
        paths = [library[key][0] for key in request.assets]
        audio = preview_file(request.preview_id)
        paths.extend((audio, audio.with_name("narration.json")))
        if request.music:
            track = sfx.music_path(request.music)
            if track is None:
                return None
            paths.append(track)
        stamps = [(str(path.resolve()), _file_stamp(path)) for path in paths]
        narration_source = _narration_settings(request)[0]
        mix = request.model_dump(mode="json", exclude={"cut_token", "title", "publish", "draft_id", "previous_id"})
        style = [brand.load().model_dump(mode="json"), repr(get_preset(request.caption_preset))]
        return _voice_key([_CUT_SESSION, mix, stamps, narration_source, style])
    except (OSError, ValueError, KeyError):
        return None


def _file_stamp(path: Path) -> tuple[int, int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _cut_cache() -> Path:
    return root().resolve() / "_cuts"


def _read_cut(token: str) -> tuple[Path, dict] | None:
    cached = _cut_cache() / token
    try:
        if time.time() - cached.stat().st_mtime > _CUT_TTL:
            return None
        record = json.loads((cached / "cut.json").read_text(encoding="utf-8"))
        if not {"duration", "version", "frames"} <= record.keys():
            return None
        if all((cached / name).is_file() for name in ("short.mp4", "captions.srt")):
            return cached, record
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _save_cut(output: Path, token: str, duration: float, version: int | None, frames: list[int]) -> None:
    cache = _cut_cache()
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / token
    if destination.exists():
        shutil.rmtree(destination)
    record = {"duration": duration, "version": version, "frames": frames}
    (output / "cut.json").write_text(json.dumps(record), encoding="utf-8")
    output.replace(destination)
    entries = sorted((item for item in cache.iterdir() if item.is_dir()),
                     key=lambda item: item.stat().st_mtime, reverse=True)
    for index, item in enumerate(entries):
        if index >= _CUT_LIMIT or time.time() - item.stat().st_mtime > _CUT_TTL:
            shutil.rmtree(item, ignore_errors=True)


def preview_scene(request: ScriptShortRequest, index: int) -> bytes:
    with _render_cut(request, scene_index=index) as (output, _, _, _):
        return (output / "short.mp4").read_bytes()


def preview_cut(request: ScriptShortRequest) -> tuple[bytes, list[float], str | None]:
    token = _cut_key(request)
    cached = _read_cut(token) if token else None
    if cached:
        path, record = cached
        try:
            video = (path / "short.mp4").read_bytes()
            if token == _cut_key(request):
                return video, [frame / FPS for frame in record["frames"]], token
        except OSError:
            pass
        token = _cut_key(request)
    with _render_cut(request) as (output, duration, version, frames):
        video = (output / "short.mp4").read_bytes()
        if token and token == _cut_key(request):
            try:
                _save_cut(output, token, duration, version, frames)
            except OSError:
                token = None
        else:
            token = None
        return video, [frame / FPS for frame in frames], token


@contextmanager
def _export_cut(request: ScriptShortRequest):
    token = request.cut_token
    cached = _read_cut(token) if token and token == _cut_key(request) else None
    if cached:
        path, record = cached
        with TemporaryDirectory(prefix=".script-export-", dir=root().resolve()) as directory:
            output = Path(directory) / "output"
            try:
                output.mkdir()
                for name in ("short.mp4", "captions.srt"):
                    shutil.copyfile(path / name, output / name)
            except OSError:
                pass
            else:
                if _cut_key(request) == token:
                    yield output, record["duration"], record["version"], True
                    return
    with _render_cut(request) as (output, duration, version, _):
        yield output, duration, version, False


def create(request: ScriptShortRequest) -> dict:
    ident = uuid.uuid4().hex[:10]
    with _export_cut(request) as (output, duration, version, reused):
        if request.publish:
            studio.package(output, request.publish, request.title, duration)
        metadata = {"id": ident, "title": request.title, "youtube_package": request.publish is not None,
                    "duration": round(duration, 2), "created": time.time(), "reused_cut": reused,
                    "voice": request.voice, "version": version, "music": request.music,
                    "music_gain_db": request.music_gain_db}
        if request.draft_id:
            metadata["draft_id"] = request.draft_id
        (output / "short.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        output.replace(root().resolve() / ident)
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
            graphics: tuple[str, str] | None, *, span: tuple[float, float] | None = None) -> None:
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
    if span is None:
        (output / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
    elif filters and span[0]:
        # Evaluate ASS events on the full-cut clock, then restore scene-local timestamps.
        filters = [f"setpts=PTS+{span[0]:.9f}/TB", *filters, "setpts=PTS-STARTPTS"]
    music_input = ["-stream_loop", "-1", "-i", str(music_track.resolve())] if music_track else []
    trim = f"atrim=start={span[0]:.9f}:end={span[1]:.9f},asetpts=PTS-STARTPTS" if span else ""
    if music_track:
        graph = sfx.audio_graph([], 2, None, duration, voice_src="1:a", music_gain_db=options.music_gain_db)
        # Trim after ducking so music phase, fades, and the compressor state match the full cut.
        audio = ["-filter_complex", f"{graph};[aout]{trim}[scene_audio]" if trim else graph,
                 "-map", "[scene_audio]" if trim else "[aout]"]
    else:
        audio = ["-map", "1:a:0", *(["-af", trim] if trim else [])]
    _ffmpeg(work, [
        "-f", "concat", "-safe", "1", "-i", "scenes.txt", "-i", "voice.wav", *music_input,
        "-map", "0:v:0", *audio, *(["-vf", ",".join(filters)] if filters else []),
        "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output / "short.mp4"),
    ])
