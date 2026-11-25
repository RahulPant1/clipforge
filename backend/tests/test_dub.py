"""Replace voice (dub.py): sentences → new text → speech in a Voice Library voice → laid out on the clip's timeline
→ rendered instead of the original audio, captions from the new words."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Clip, DubLine, DubScript, RenderOptions, SceneAnchor, Segment, Word
from app.pipeline import dub, tts

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def talk(*sentences: str, start: float = 0.0, step: float = 0.4, gap: float = 0.3) -> list[Segment]:
    segs, t = [], start
    for text in sentences:
        ws = []
        for tok in text.split():
            ws.append(Word(start=round(t, 2), end=round(t + step * 0.8, 2), word=f" {tok}"))
            t += step
        segs.append(Segment(start=ws[0].start, end=ws[-1].end, text=text, words=ws))
        t += gap
    return segs


S = ["AI is changing work.", "Most teams feel it already.", "Here is what to do next."]


def tone(path, seconds, freq=440.0):
    t = np.arange(int(dub.SR * seconds)) / dub.SR
    pcm = (0.3 * np.sin(2 * np.pi * freq * t)).astype("<f4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(dub.SR), "-ac", "1", "-i", "-",
                    str(path)], input=pcm.tobytes(), check=True)


def line(i, start, seconds, text="x y z", file=None):
    return DubLine(i=i, start=start, end=start + 1, original=text, text=text, file=file or f"l{i}.wav",
                   said=text, voice="v@newest", seconds=seconds,
                   words=[Word(start=0.0, end=seconds / 2, word=" x"), Word(start=seconds / 2, end=seconds, word=" y")])


# ------------------------------------------------------------------ script

def test_draft_edit_and_what_needs_generating():
    segs = talk(*S)
    clip = Clip(index=2, start=0, end=segs[-1].end + 0.5, title="t", hook="h", score=7)
    s = dub.draft(segs, clip)
    assert [ln.original for ln in s.lines] == S and all(ln.text == ln.original for ln in s.lines)
    assert s.lines[1].start == segs[1].start and s.clip == 2
    s = dub.edit(s, voice="me", texts={0: "  AI is   transforming how businesses operate. "})
    assert s.lines[0].text == "AI is transforming how businesses operate." and s.voice == "me"
    assert all(dub.stale(s, ln) for ln in s.lines)  # nothing generated yet
    s.lines[1] = s.lines[1].model_copy(update={"file": "a.wav", "said": s.lines[1].text, "voice": "me@newest"})
    assert not dub.stale(s, s.lines[1])
    s2 = dub.edit(s.model_copy(deep=True), voice="me", version=2)
    assert dub.stale(s2, s2.lines[1])  # another version → speak it again
    s.lines[2].text = ""
    assert not dub.stale(s, s.lines[2])  # empty = silence there


def test_script_flags_clipped_thoughts_without_rewriting():
    segments = talk("Before the clip starts because the sentence continues.")
    clip = Clip(index=1, start=0.4, end=1.97, title="t", hook="", score=5)
    script = dub.draft(segments, clip)
    assert script.lines[0].text.endswith("because")
    warnings = dub.script_warnings(segments, clip, script)
    assert "mid-sentence" in warnings[0][0] and "connecting word" in warnings[0][1]
    dub.edit(script, texts={0: "This is the complete thought."})
    assert not dub.script_warnings(segments, clip, script)


def test_repair_uses_context_and_preserves_script_until_review():
    from fastapi import HTTPException

    from app.main import create_app
    from app.models import DubEdit, JobStatus

    class FakeNarrator:
        def unload(self):
            pass

        def chat_json(self, prompt, schema, **kwargs):
            context = json.loads(prompt.split("\n", 1)[1])
            assert context["source_before"] == "Before"
            assert context["source_after"] == "the sentence continues."
            return {"text": "The sentence continues after the clip starts."}

    app = create_app(start_worker=False, llm_factory=FakeNarrator)
    endpoints = {route.name: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    store = app.state.store
    job = store.create("source", 1)
    clip = Clip(index=1, start=0.4, end=1.97, title="t", hook="", score=5, approved=True)
    store.update(job.id, status=JobStatus.review, clips=[clip])
    segments = talk("Before the clip starts because the sentence continues.")
    directory = store.dir(job.id)
    (directory / "transcript.json").write_text(json.dumps([segment.model_dump() for segment in segments]))
    script = dub.save(directory, dub.draft(segments, clip))

    proposal = endpoints["suggest_dub_line"](job.id, 1, 0)
    assert proposal["original"] == script.lines[0].text
    assert dub.load(directory, 1) == script

    edit = DubEdit(texts={0: proposal["text"]}, expected_texts={0: proposal["original"]})
    saved = endpoints["put_dub"](job.id, 1, edit)
    assert saved["script"]["lines"][0]["text"] == proposal["text"]
    assert not saved["warnings"]
    with pytest.raises(HTTPException) as stale:
        endpoints["put_dub"](job.id, 1, edit)
    assert stale.value.status_code == 409


def test_captions_show_the_typed_words_timed_by_what_was_heard():
    heard = [{"start": 0.1, "end": 0.4, "word": " Ay"}, {"start": 0.5, "end": 0.9, "word": " eye"}]
    assert [(w.word, w.start) for w in dub.map_words("AI rocks", heard, 1.0)] == [(" AI", 0.1), (" rocks", 0.5)]
    spread = dub.map_words("one two three four", heard, 1.0)  # counts differ → spread over the speech
    assert [w.word for w in spread] == [" one", " two", " three", " four"]
    assert spread[0].start == 0.1 and spread[-1].end <= 0.9 + 1e-6


# ------------------------------------------------------------------ layout

def test_layout_borrows_time_speeds_up_and_cuts_last():
    s = DubScript(clip=1, voice="v", lines=[line(0, 10.0, 2.0), line(1, 12.0, 3.5), line(2, 14.0, 1.0)])
    lay = {ln.i: (round(at, 2), rate, round(kept, 2)) for ln, at, rate, kept in dub.layout(s, 10.0, 20.0)}
    assert lay[0] == (0.0, 1.0, 2.0)  # fits its own time
    at1, rate1, kept1 = lay[1]
    assert at1 == round(2.0 + dub.GAP, 2) and rate1 > 1.0 and kept1 <= (4.0 + dub.LATE) - at1 + dub.TOL
    assert lay[2][0] >= at1 + kept1  # pushed back, never overlapping
    tight = DubScript(clip=1, voice="v", lines=[line(0, 10.0, 6.0)])
    (_, at, rate, kept), = dub.layout(tight, 10.0, 13.0)
    assert rate == dub.MAX_RATE and kept == pytest.approx(3.0)  # clip end is hard: cut


@needs_ffmpeg
def test_track_is_laid_out_on_the_clip_and_relaid_after_a_trim(tmp_path):
    tone(tmp_path / "l0.wav", 1.0)
    tone(tmp_path / "l1.wav", 5.0)
    s = DubScript(clip=1, voice="v", lines=[line(0, 10.0, 1.0), line(1, 12.0, 5.0)])
    s = dub.assemble(tmp_path, s, 10.0, 15.0)
    assert s.track == "dub_1.wav" and s.span == (10.0, 15.0) and s.track_version == 1
    assert s.lines[0].fit == "ok" and s.lines[0].at == 0.0 and s.lines[1].fit == "too_long"
    assert s.lines[1].over == pytest.approx(0.3, abs=0.03) and s.lines[0].over is None  # 5 s / 1.35 in 3.4 s
    pcm = tts.read_pcm(tmp_path / "dub_1.wav")
    assert abs(len(pcm) / tts.SR - 5.0) < 0.05
    second = pcm[int(0.2 * tts.SR):int(0.8 * tts.SR)]
    gap = pcm[int(1.2 * tts.SR):int(1.55 * tts.SR)]  # line 1 needs room: starts EARLY, at 1.6
    assert np.abs(second).max() > 0.2 and np.abs(gap).max() < 0.01

    segs = dub.segments(s, 10.0, 15.0)
    assert [w.word for w in segs[0].words] == [" x", " y"] and segs[0].start == 10.0
    assert segs[1].words[-1].end <= 15.0 + 1e-6

    dub.save(tmp_path, s)
    clip = Clip(index=1, start=10.0, end=14.0, title="t", hook="h", score=7, options=RenderOptions(dub=True))
    track, _ = dub.track_for(tmp_path, clip)  # trimmed since: re-laid for 10-14
    assert dub.load(tmp_path, 1).span == (10.0, 14.0) and abs(len(tts.read_pcm(track)) / tts.SR - 4.0) < 0.05
    assert dub.track_for(tmp_path, clip.model_copy(update={"options": RenderOptions(dub=False)})) is None


# ------------------------------------------------------------------ generate (fake voice engine)

@needs_ffmpeg
def test_generate_speaks_only_what_changed_in_one_load(tmp_path, monkeypatch):
    v = tts.create("Me", consent=True)
    calls = []

    def fake_speak(vid, texts, language=None, out_dir=None, names=None, version=None):
        calls.append((tuple(texts), version))
        for n in names:
            tone(out_dir / n, 1.2)
        return [{"file": n, "seconds": 1.2, "took": 1.0} for n in names]

    monkeypatch.setattr(tts, "speak", fake_speak)
    monkeypatch.setattr(tts, "prompt_for", lambda v, version=None: tmp_path / "p.pt")
    monkeypatch.setattr(tts, "transcribe_many", lambda paths: [{"words": [{"start": 0.1, "end": 0.5, "word": " a"}],
                                                                "text": "a"} for _ in paths])
    segs = talk(*S)
    clip = Clip(index=1, start=0, end=segs[-1].end + 0.5, title="t", hook="h", score=7)
    s = dub.edit(dub.draft(segs, clip), voice=v["id"], texts={0: "AI is transforming how businesses operate."})
    s = dub.generate(tmp_path, s, clip)
    assert len(calls) == 1 and calls[0][0][0] == "AI is transforming how businesses operate."
    assert all(ln.file and (tmp_path / ln.file).exists() for ln in s.lines) and s.track == "dub_1.wav"
    old = s.lines[1].file
    s = dub.edit(s, texts={1: "Most teams already feel it."})
    s = dub.generate(tmp_path, s, clip)
    assert calls[-1][0] == ("Most teams already feel it.",) and not (tmp_path / old).exists()
    dub.generate(tmp_path, s, clip, only=[2])  # regenerate one line on purpose
    assert calls[-1][0] == (S[2],)
    with pytest.raises(ValueError):
        dub.generate(tmp_path, DubScript(clip=1), clip)


# ------------------------------------------------------------------ render + API

@needs_ffmpeg
def test_render_plays_the_dub_track_with_its_captions(tmp_path):
    from app.pipeline import render

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x303030:s=1080x1920:r=25:d=4",
                    "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    tone(tmp_path / "l0.wav", 2.0, freq=1000.0)
    s = DubScript(clip=1, voice="v", lines=[line(0, 0.5, 2.0, text="brand new words")])
    s.lines[0].words = [Word(start=0, end=0.6, word=" brand"), Word(start=0.6, end=1.2, word=" new"),
                        Word(start=1.2, end=2.0, word=" words")]
    dub.save(tmp_path, dub.assemble(tmp_path, s, 0.0, 4.0))
    clip = Clip(index=1, start=0, end=4, title="t", hook="", score=7,
                options=RenderOptions(dub=True, reframe="center", hook=False))
    render.render_clip(src, clip, talk("old words here"), tmp_path)
    assert "brand" in (tmp_path / "short_1.srt").read_text().lower()
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-t", "0.5", "-i", str(tmp_path / "short_1.mp4"),
                          "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, "<f4")
    spectrum = np.abs(np.fft.rfft(x))
    peak = np.fft.rfftfreq(len(x), 1 / 8000)[spectrum.argmax()]
    assert abs(peak - 1000) < 30  # the dub's 1 kHz tone, not the source's 300 Hz


@needs_ffmpeg
def test_dub_api(tmp_path, monkeypatch):
    from .test_api import _fake_steps, _wait_status

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)

    def fake_generate(job_dir, script, clip, only=None, on_progress=None):
        for ln in script.lines:
            tone(job_dir / f"g{ln.i}.wav", 0.5)
            ln.file, ln.said, ln.voice, ln.seconds = f"g{ln.i}.wav", ln.text, dub.voice_tag(script), 0.5
        return dub.assemble(job_dir, script, clip.start, clip.end)

    monkeypatch.setattr(dub, "generate", fake_generate)
    with TestClient(create_app(steps=steps)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1}).json()["id"]
        _wait_status(c, jid, "review")
        d = tmp_path / "jobs" / jid
        (d / "transcript.json").write_text(json.dumps([x.model_dump() for x in talk(*S, start=20.0)]))
        got = c.get(f"/api/jobs/{jid}/clips/1/dub").json()
        assert got["saved"] is False and [ln["original"] for ln in got["script"]["lines"]] == S
        assert c.put(f"/api/jobs/{jid}/clips/1/dub", json={"voice": "nobody"}).status_code == 404
        vid = tts.create("Me", consent=True)["id"]
        r = c.put(f"/api/jobs/{jid}/clips/1/dub", json={"voice": vid, "texts": {"0": "AI is transforming work."}})
        assert r.json()["saved"] and r.json()["script"]["lines"][0]["text"] == "AI is transforming work."
        assert r.json()["stale"] == [0, 1, 2]
        r = c.post(f"/api/jobs/{jid}/clips/1/dub/generate")
        assert r.status_code == 200 and r.json()["script"]["track"] == "dub_1.wav" and r.json()["stale"] == []
        assert c.get(f"/api/jobs/{jid}/files/dub_1.wav").status_code == 200

        c.patch(f"/api/jobs/{jid}/clips/1", json={"options": {"dub": True}})
        c.post(f"/api/jobs/{jid}/produce")
        _wait_status(c, jid, "done")
        job = c.delete(f"/api/jobs/{jid}/clips/1/dub").json()
        clip = job["clips"][0]
        assert clip["options"]["dub"] is None and clip["stale"] is True and not (d / "dub_1.wav").exists()


@needs_ffmpeg
def test_batch_drafts_preview_and_activation(monkeypatch):
    from app.main import create_app
    from app.models import DubBatchDraft, DubGenerate, JobStatus

    class NarrationLLM:
        def unload(self):
            pass

        def chat_json(self, prompt, schema, **kwargs):
            assert "Attribute personal experiences" in prompt
            ids = schema["properties"]["lines"]["items"]["properties"]["i"]["enum"]
            return {"lines": [{"i": i, "text": f"Suggested narration {i}."} for i in ids]}

    app = create_app(start_worker=False, llm_factory=NarrationLLM)
    endpoints = {route.name: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    store = app.state.store
    job = store.create("source", 2)
    clips = [
        Clip(index=i, start=0, end=5, title="t", hook="h", score=5, approved=True, file=f"short_{i}.mp4")
        for i in (1, 2)
    ]
    store.update(job.id, status=JobStatus.done, clips=clips)
    d = store.dir(job.id)
    segs = talk("I started a company.", "It grew quickly.")
    (d / "transcript.json").write_text(json.dumps([s.model_dump() for s in segs]))
    dub.save(d, dub.edit(dub.draft(segs, clips[0]), texts={0: "I manually rewrote this line."}))

    voice = tts.create("Me", consent=True)
    voice["builds"] = [{"version": 2, "prompt": "prompts/v2.pt"}]
    prompt = tts.voice_dir(voice["id"]) / "prompts" / "v2.pt"
    prompt.parent.mkdir(exist_ok=True)
    prompt.touch()
    tts.save(voice)

    request = DubBatchDraft(clips=[1, 2], voice=voice["id"], mode="rewrite")
    assert endpoints["draft_dubs"](job.id, request) == {"clips": [1, 2]}
    first, second = dub.load(d, 1), dub.load(d, 2)
    assert first.version == second.version == 2 and first.voice == second.voice == voice["id"]
    assert [line.text for line in first.lines] == ["I manually rewrote this line.", "Suggested narration 1."]
    assert second.lines[0].text == "Suggested narration 0."

    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=25:d=5",
        "-pix_fmt", "yuv420p", str(d / "source.mp4"),
    ], check=True)

    def speak(vid, texts, *, out_dir, names, version):
        assert version == 2
        for name in names:
            tone(out_dir / name, 0.5)
        return [{"seconds": 0.5} for _ in names]

    monkeypatch.setattr(tts, "speak", speak)
    monkeypatch.setattr(tts, "transcribe_many", lambda paths: [{"words": []} for _ in paths])
    response = endpoints["preview_dub"](job.id, 1)
    assert (d / response["file"]).stat().st_size > 0
    assert dub.load(d, 1) == first and not store.get(job.id).clips[0].options.dub

    endpoints["generate_dub"](job.id, 1, DubGenerate(activate=True))
    updated = store.get(job.id).clips
    assert updated[0].options.dub and updated[0].stale
    assert not updated[1].options.dub and not updated[1].stale
    assert all(not dub.stale(dub.load(d, 1), line) for line in dub.load(d, 1).lines)


@pytest.mark.parametrize("failure", ["synthesis", "alignment"])
def test_background_narration_retries_only_failed_clips(monkeypatch, failure):
    from app.main import create_app
    from app.models import JobStatus, NarrationQueue

    class LocalLLM:
        def unload(self):
            pass

    app = create_app(start_worker=False, llm_factory=LocalLLM)
    endpoints = {route.name: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    store, worker = app.state.store, app.state.worker
    job = store.create("source", 2)
    clips = [Clip(index=i, start=0, end=5, title="t", hook="h", score=5, approved=True) for i in (1, 2)]
    store.update(job.id, status=JobStatus.review, clips=clips)
    directory = store.dir(job.id)
    for clip in clips:
        dub.save(directory, DubScript(clip=clip.index, voice="v", lines=[line(0, 0, 2)]))
    attempts = []
    alignments = []
    monkeypatch.setattr(tts, "load", lambda _: {"name": "Saved voice"})
    monkeypatch.setattr(tts, "prompt_for", lambda *args: directory / "voice.pt")

    def speak(job, *, on_item):
        attempts.append([Path(item["out"]).name for item in job["items"]])
        results = []
        for i, item in enumerate(job["items"]):
            path = Path(item["out"])
            index = int(path.name.split("_")[1])
            on_item(i)
            status = store.get(dub_job_id).narration.items[index]
            assert status.status == "running" and status.progress == 0.65
            if failure == "synthesis" and len(attempts) == 1 and index == 2:
                results.append({"error": "Voice engine interrupted"})
            else:
                path.write_bytes(b"test take")
                results.append({"seconds": 1})
        return {"ok": True, "items": results}

    def align(paths, *, continue_on_error):
        assert continue_on_error
        alignments.append(len(paths))
        if failure == "alignment" and len(alignments) == 1:
            return [{"words": []}, {"error": "Alignment failed"}]
        return [{"words": []} for _ in paths]

    def assemble(directory, script, start, end, *, pacing):
        assert pacing == "natural"
        return script

    dub_job_id = job.id
    monkeypatch.setattr(tts, "run_worker", speak)
    monkeypatch.setattr(tts, "transcribe_many", align)
    monkeypatch.setattr(dub, "assemble", assemble)
    queued = endpoints["queue_narration"](job.id, NarrationQueue(clips=[1, 2], pacing="natural"))
    assert queued.status == JobStatus.queued
    kind, job_id, kwargs = worker.q.get_nowait()
    worker._run(kind, job_id, **kwargs)
    current = store.get(job.id)
    assert {index: item.status for index, item in current.narration.items.items()} == {1: "ready", 2: "failed"}
    assert current.clips[0].options.dub and not current.clips[1].options.dub

    endpoints["retry_narration"](job.id)
    kind, job_id, kwargs = worker.q.get_nowait()
    worker._run(kind, job_id, **kwargs)
    assert [len(batch) for batch in attempts] == [2, 1] and attempts[1] == [attempts[0][1]]
    assert alignments == ([1, 1] if failure == "synthesis" else [2, 1])
    assert all(item.status == "ready" for item in store.get(job.id).narration.items.values())


@needs_ffmpeg
def test_natural_dub_extends_the_picture_and_mixes_ambience(tmp_path, monkeypatch):
    from app.pipeline import ambience, render

    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=s=160x90:r=25:d=3",
        "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    stem = tmp_path / "room.wav"
    tone(stem, 3, freq=200)
    tone(tmp_path / "l0.wav", 5, freq=1000)
    monkeypatch.setattr(ambience, "track_for", lambda *args: stem)
    monkeypatch.setitem(render.ASPECTS, "9:16", ((180, 320), ""))
    clip = Clip(index=1, start=0, end=3, title="t", hook="", score=7, options=RenderOptions(
        dub=True, dub_pacing="natural", dub_ambience=True, sfx=False, hook=False, aspects=[],
    ))
    dub.save(tmp_path, dub.assemble(tmp_path, DubScript(clip=1, voice="v", lines=[line(0, 0, 5)]), 0, 3))
    render.render_clip(source, clip, [], tmp_path)

    script = dub.load(tmp_path, 1)
    assert clip.out_duration == script.track_duration == 7
    assert script.lines[0].fit == "ok" and script.lines[0].rate == 1
    assert "00:00:05,000" in (tmp_path / "short_1.srt").read_text()
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=duration", "-of", "csv=p=0",
        str(tmp_path / "short_1.mp4"),
    ], capture_output=True, text=True, check=True)
    durations = [float(s) for s in result.stdout.splitlines()]
    assert len(durations) == 2 and all(abs(seconds - 7) < 0.08 for seconds in durations)
    raw = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "1", "-t", "1", "-i", str(tmp_path / "short_1.mp4"),
        "-ac", "1", "-ar", "8000", "-f", "f32le", "-",
    ], capture_output=True, check=True).stdout
    spectrum = np.abs(np.fft.rfft(np.frombuffer(raw, "<f4")))
    assert spectrum[1000] > spectrum[200] > spectrum[300] * 10


def test_ambience_separates_each_clip_range_once(tmp_path, monkeypatch):
    from app.pipeline import ambience

    calls = []

    def run(command, failure, timeout):
        calls.append(command)
        if command[0] == "/test/bin/demucs":
            folder = Path(command[command.index("-o") + 1]) / ambience.MODEL / "source"
            folder.mkdir(parents=True)
            (folder / "no_vocals.wav").write_bytes(b"ambient audio")

    monkeypatch.setattr(ambience.shutil, "which", lambda _: "/test/bin/demucs")
    monkeypatch.setattr(ambience, "_run", run)
    source = tmp_path / "source.mp4"
    source.touch()
    clip = Clip(index=1, start=2, end=6, title="t", hook="", score=7)
    stem = ambience.track_for(source, tmp_path, clip)
    assert stem.read_bytes() == b"ambient audio"
    assert ambience.track_for(source, tmp_path, clip) == stem
    assert len(calls) == 2 and "--two-stems" in calls[1]

    trimmed = ambience.track_for(source, tmp_path, clip.model_copy(update={"end": 5}))
    assert trimmed != stem and trimmed.is_file() and len(calls) == 4


@needs_ffmpeg
def test_export_preview_uses_scene_pacing_without_replacing_published_files(tmp_path, monkeypatch):
    from app.pipeline import ambience, render, scenes

    source = tmp_path / "source.mp4"
    pattern = ("color=c=red:s=160x90:r=25:d=2[a];color=c=blue:s=160x90:r=25:d=2[b];"
               "[a][b]concat=n=2:v=1:a=0")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", pattern, "-pix_fmt", "yuv420p", str(source),
    ], check=True)
    tone(tmp_path / "l0.wav", 3, freq=1000)
    tone(tmp_path / "l1.wav", 1, freq=1000)
    room = tmp_path / "room.wav"
    tone(room, 4, freq=200)
    monkeypatch.setattr(ambience, "track_for", lambda *args: room)
    scenes.save(scenes.SceneIndex(mode="skipped", cuts=[2]), tmp_path)
    script = DubScript(clip=1, voice="v", lines=[line(0, 0, 3), line(1, 2, 1)])
    dub.save(tmp_path, dub.assemble(tmp_path, script, 0, 4))
    clip = Clip(index=1, start=0, end=4, title="t", hook="", score=7, stale=True, options=RenderOptions(
        dub=True, dub_pacing="scene", dub_ambience=True, dub_ambience_level=0.3, sfx=False, hook=False,
    ))
    export = tmp_path / "short_1.mp4"
    captions = tmp_path / "short_1.srt"
    export.write_bytes(b"previous export")
    captions.write_text("previous captions")

    preview = tmp_path / render.render_clip(source, clip, [], tmp_path, preview=True)
    assert dub.scene_timing(tmp_path, clip, 6) == [(0, 0), (2, 4), (4, 6)]
    assert export.read_bytes() == b"previous export" and captions.read_text() == "previous captions"
    assert clip.stale and clip.version == 0

    def pixel(seconds):
        return subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(preview), "-frames:v", "1",
            "-vf", "crop=2:2:180:240,format=rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True).stdout[:3]

    red = pixel(3.4)
    blue = pixel(4.3)
    assert red[0] > 200 and red[2] < 30
    assert blue[2] > 200 and blue[0] < 30

    clip.options.dub_anchors = [SceneAnchor(source=2, narration=3.2)]
    assert dub.scene_timing(tmp_path, clip, 6) == [(0, 0), (2, 3.2), (4, 6)]
    render.render_clip(source, clip, [], tmp_path, preview=True, preview_range=(3.4, 0.8))
    sample = pixel(0)
    assert sample[2] > 200 and sample[0] < 30
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(preview),
    ], capture_output=True, text=True, check=True)
    assert abs(float(result.stdout) - 0.8) < 0.08
    with pytest.raises(ValueError, match="Preview start"):
        render.render_clip(source, clip, [], tmp_path, preview=True, preview_range=(12, 1))
    assert export.read_bytes() == b"previous export" and captions.read_text() == "previous captions"


def test_manual_scene_anchors_validate_timeline_order():
    with pytest.raises(ValueError, match="must advance"):
        RenderOptions(dub_anchors=[SceneAnchor(source=1, narration=2), SceneAnchor(source=3, narration=1)])
    options = RenderOptions(dub_anchors=[SceneAnchor(source=3, narration=4), SceneAnchor(source=1, narration=2)])
    assert [anchor.source for anchor in options.dub_anchors] == [1, 3]
