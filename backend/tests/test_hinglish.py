"""Hindi transcript in Devanagari → Hinglish (Latin letters), word by word with timings kept."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Clip, JobStatus, Segment, Word
from app.pipeline import hinglish, transcript_edit
from app.pipeline.hinglish import to_latin


@pytest.mark.parametrize("hindi,latin", [
    ("जब लैला रूठी, मजनू की किस्मत फूटी।", "jab laila ruthi, majnu ki kismat phuti."),
    ("क्या", "kya"), ("नहीं", "nahin"), ("में", "mein"), ("हैं", "hain"), ("हाँ", "haan"), ("हूँ", "hoon"),
    ("आप", "aap"), ("बात", "baat"), ("प्यार", "pyaar"), ("ठीक", "theek"),  # one syllable: long vowels kept
    ("तुम्हारा", "tumhara"), ("ज़्यादा", "zyada"), ("सरकार", "sarkar"),  # longer words: short
    ("समझना", "samajhna"), ("कमला", "kamla"), ("लड़की", "ladki"), ("उसने", "usne"), ("ज़िंदगी", "zindgi"),
    ("नमस्ते", "namaste"), ("समय", "samay"), ("दोस्त", "dost"),  # schwa deletion
    ("अच्छा", "accha"), ("कुछ", "kuch"), ("मिंटो फ्रेश", "minto fresh"), ("इंप्रेस", "impres"),
    ("हम 2024 में Google पर", "ham 2024 mein Google par"),  # digits / English untouched
    ("२०२४", "2024"), ("न", "na"),
])
def test_to_latin(hindi, latin):
    assert to_latin(hindi) == latin


def _segs():
    words = [Word(start=1.0, end=1.4, word=" जब"), Word(start=1.5, end=2.0, word=" लैला"),
             Word(start=2.1, end=2.6, word=" रूठी,"), Word(start=3.0, end=3.5, word=" OK")]
    return [Segment(start=1.0, end=3.5, text="जब लैला रूठी, OK", words=words)]


def test_romanize_keeps_every_word_and_timing_and_can_go_back(tmp_path):
    transcript_edit.save(tmp_path, _segs())
    assert hinglish.state(tmp_path) == "devanagari"
    assert hinglish.romanize(tmp_path) == [(1.0, 2.6)]
    segs = transcript_edit.load(tmp_path)
    assert [(w.start, w.end, w.word) for w in segs[0].words] == [
        (1.0, 1.4, " jab"), (1.5, 2.0, " laila"), (2.1, 2.6, " ruthi,"), (3.0, 3.5, " OK")]
    assert segs[0].text == "jab laila ruthi, OK" and hinglish.state(tmp_path) == "hinglish"
    assert hinglish.romanize(tmp_path) == []  # already Latin: nothing to do
    assert hinglish.back_to_native(tmp_path) == [(1.0, 3.5)]
    assert transcript_edit.load(tmp_path)[0].words[1].word == " लैला" and hinglish.state(tmp_path) == "devanagari"


def test_english_transcripts_are_left_alone(tmp_path):
    transcript_edit.save(tmp_path, [Segment(start=0, end=1, text="hello there",
                                            words=[Word(start=0, end=1, word=" hello")])])
    assert hinglish.romanize(tmp_path) == [] and hinglish.state(tmp_path) == ""
    assert not (tmp_path / hinglish.NATIVE).exists()


def test_prepare_romanizes_before_selection(tmp_path, monkeypatch):
    from app.pipeline import orchestrator

    def fake_transcribe(video, job_dir, cb):
        transcript_edit.save(job_dir, _segs())
        return _segs()

    seen = []
    monkeypatch.setattr(orchestrator, "transcribe", fake_transcribe)
    monkeypatch.setattr(orchestrator, "words_per_minute", lambda s, d=0: 150)
    monkeypatch.setattr(orchestrator, "select_highlights",
                        lambda segs, *a, **k: seen.append(segs[0].text) or [Clip(start=1, end=3, title="t", hook="h",
                                                                                   score=5)])
    src = tmp_path / "ad.mp4"
    src.write_bytes(b"x")

    class LLM:
        def unload(self):
            pass

    orchestrator.prepare(str(src), tmp_path / "job", 1, lambda *a: None, llm=LLM(), romanize=True)
    assert seen == ["jab laila ruthi, OK"]  # Gemma picks clips from the Hinglish text
    orchestrator.prepare(str(src), tmp_path / "job2", 1, lambda *a: None, llm=LLM())
    assert seen[-1] == "जब लैला रूठी, OK"


def test_api_default_and_switching_marks_shorts_stale(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "hinglish_default", True)
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    with TestClient(app) as c:
        job = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x"}).json()
        assert job["hinglish"] is True
        assert c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=y", "hinglish": False}).json()[
            "hinglish"] is False
        jid = job["id"]
        transcript_edit.save(store.dir(jid), _segs())
        store.update(jid, status=JobStatus.done, hinglish=False,
                     clips=[Clip(index=1, start=0, end=5, title="a", hook="h", score=7, file="short_1.mp4")])
        assert c.get(f"/api/jobs/{jid}/script").json()["state"] == "devanagari"
        job = c.post(f"/api/jobs/{jid}/script", json={"hinglish": True}).json()
        assert job["hinglish"] and job["clips"][0]["stale"]
        assert c.get(f"/api/jobs/{jid}/transcript").json()[0]["text"] == "jab laila ruthi, OK"
        assert c.get(f"/api/jobs/{jid}/script").json()["state"] == "hinglish"
        job = c.post(f"/api/jobs/{jid}/script", json={"hinglish": False}).json()
        assert not job["hinglish"] and "लैला" in c.get(f"/api/jobs/{jid}/transcript").json()[0]["text"]
        store.update(jid, status=JobStatus.running)
        assert c.post(f"/api/jobs/{jid}/script", json={"hinglish": True}).status_code == 409
    assert json.loads((store.dir(jid) / "transcript.json").read_text())[0]["words"][0]["word"] == " जब"


@pytest.mark.parametrize("duration,expect", [(30.0, True), (300.0, False)])
def test_a_video_that_fits_in_a_short_becomes_the_short(tmp_path, monkeypatch, duration, expect):
    """30 s ad: every pick inside it was under the 20 s minimum → the whole ad, with Gemma's best title/hook."""
    from app.pipeline import orchestrator

    def fake_select(segs, n, llm, cb, keep=None, trace=None, topic=""):
        trace["items"] = [{"raw": {"start": 0.4, "end": 6.7, "title": "Bad luck", "hook": "Fate changed", "score": 8,
                                   "keywords": ["majnu"]}, "outcome": "too_short"},
                          {"raw": {"start": 20, "end": 24, "title": "Weaker", "hook": "x", "score": 6},
                           "outcome": "too_short"}]
        return []

    monkeypatch.setattr(orchestrator, "transcribe", lambda v, d, cb: _segs())
    monkeypatch.setattr(orchestrator, "words_per_minute", lambda s, d=0: 150)
    monkeypatch.setattr(orchestrator, "probe_duration", lambda v: duration)
    monkeypatch.setattr(orchestrator, "select_highlights", fake_select)
    src = tmp_path / "ad.mp4"
    src.write_bytes(b"x")

    class LLM:
        def unload(self):
            pass

    run = lambda: orchestrator.prepare(str(src), tmp_path / "job", 1, lambda *a: None, llm=LLM(),  # noqa: E731
                                       topic="minto fresh")
    if not expect:
        with pytest.raises(RuntimeError, match="No moments about"):
            run()
        return
    (clip,) = run()
    assert (clip.start, clip.end, clip.title, clip.hook, clip.score, clip.keywords) == (
        0.0, 30.0, "Bad luck", "Fate changed", 8, ["majnu"])
    assert json.loads((tmp_path / "job" / "candidates.json").read_text())["whole_video"] is True
