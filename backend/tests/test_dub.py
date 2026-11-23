"""Replace voice (dub.py): sentences → new text → speech in a Voice Library voice → laid out on the clip's timeline
→ rendered instead of the original audio, captions from the new words."""
import json
import shutil
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Clip, DubLine, DubScript, RenderOptions, Segment, Word
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
