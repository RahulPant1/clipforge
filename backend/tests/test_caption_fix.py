"""Caption editor (fix misheard words, replace all) and the Whisper vocabulary."""
import json
import sys
import types

import pytest

from app.config import settings
from app.models import Clip, JobStatus, Segment, Word
from app.pipeline import transcript_edit as te
from app.pipeline import vocabulary
from app.pipeline.captions import build_ass
from app.pipeline.jumpcut import plan_cuts


def _w(t, word):
    return Word(start=t, end=t + 0.4, word=" " + word)


SEGS = [
    Segment(start=0, end=2.4, text="I use open ai, every day.", words=[
        _w(0, "I"), _w(0.4, "use"), _w(0.8, "open"), _w(1.2, "ai,"), _w(1.6, "every"), _w(2.0, "day.")]),
    Segment(start=40, end=41.2, text="Open AI rocks.", words=[_w(40, "Open"), _w(40.4, "AI"), _w(40.8, "rocks.")]),
    Segment(start=80, end=80.8, text="Rahul Punt here.", words=[_w(80, "Rahul"), _w(80.4, "Punt here.")]),
]


@pytest.fixture
def job_dir(tmp_path):
    (tmp_path / "transcript.json").write_text(json.dumps([s.model_dump() for s in SEGS]))
    return tmp_path


def test_set_word_rewrites_the_word_and_its_segment_text(job_dir):
    assert te.set_word(job_dir, 2, 1, "  Pant here. ") == pytest.approx((80.4, 80.8))
    seg = te.load(job_dir)[2]
    assert seg.words[1].word == " Pant here." and seg.text == "Rahul Pant here."
    with pytest.raises(ValueError):
        te.set_word(job_dir, 0, 0, "   ")
    with pytest.raises(KeyError):
        te.set_word(job_dir, 9, 0, "x")


def test_replace_all_merges_phrases_and_keeps_punctuation(job_dir):
    assert len(te.replace_all(job_dir, "open AI", "OpenAI", dry_run=True)) == 2
    assert te.load(job_dir)[0].text == "I use open ai, every day."  # dry run changed nothing
    spans = te.replace_all(job_dir, "open ai", "OpenAI")
    assert [x for s in spans for x in s] == pytest.approx([0.8, 1.6, 40.0, 40.8])
    a, b = te.load(job_dir)[:2]
    assert a.text == "I use OpenAI, every day." and [w.word for w in a.words][2] == " OpenAI,"
    assert (a.words[2].start, a.words[2].end) == pytest.approx((0.8, 1.6)) and len(a.words) == 5
    assert b.text == "OpenAI rocks."
    assert te.replace_all(job_dir, "open ai", "OpenAI") == []  # nothing left to change
    with pytest.raises(ValueError):
        te.replace_all(job_dir, " ,, ", "x")


def test_replace_keeps_each_occurrences_own_punctuation(job_dir):
    te.replace_all(job_dir, "rocks.", "ROCKS,")  # what you type after the word is ignored
    assert te.load(job_dir)[1].text == "Open AI ROCKS."
    te.replace_all(job_dir, "day", "night")
    assert te.load(job_dir)[0].text == "I use open ai, every night."
    with pytest.raises(ValueError):
        te.replace_all(job_dir, "day", "...")


def test_fixed_words_reach_the_captions(job_dir):
    te.replace_all(job_dir, "open ai", "OpenAI")
    plan = plan_cuts(te.load(job_dir), 0, 2.4, enabled=False)
    ass = build_ass(plan.words, [])
    assert "OPENAI," in ass and "OPEN" not in ass.replace("OPENAI", "")


def test_vocabulary_is_cleaned_and_given_to_whisper(monkeypatch, tmp_path):
    assert vocabulary.hotwords() is None
    assert vocabulary.save(["OpenAI", " openai ", "Rahul  Pant", ""]) == ["OpenAI", "Rahul Pant"]
    assert vocabulary.add("ClipForge") == ["OpenAI", "Rahul Pant", "ClipForge"]
    assert vocabulary.hotwords() == "OpenAI, Rahul Pant, ClipForge"
    (settings.data_dir / "vocabulary.json").write_text("{broken")
    assert vocabulary.load() == []

    vocabulary.save(["OpenAI"])
    seen = {}

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, **kw):
            seen.update(kw)
            return iter([]), types.SimpleNamespace(duration=1.0, language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    from app.pipeline import transcribe as tr

    monkeypatch.setattr(settings, "whisper_device", "cpu")
    monkeypatch.setattr(tr, "load_audio", lambda video: [])
    (tmp_path / "job").mkdir()
    tr.transcribe(tmp_path / "v.mp4", tmp_path / "job")
    assert seen["hotwords"] == "OpenAI" and seen["initial_prompt"]  # fillers prompt still sent


def test_api_edit_replace_stale_and_vocabulary(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(pipeline=lambda *a: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    d = store.dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(json.dumps([s.model_dump() for s in SEGS]))
    clips = [Clip(index=1, start=0, end=30, title="a", hook="h", score=7, file="short_1.mp4"),
             Clip(index=2, start=70, end=100, title="b", hook="h", score=7, file="short_2.mp4")]
    store.update(job.id, status=JobStatus.running, clips=clips)
    with TestClient(app) as c:
        words = c.get(f"/api/jobs/{job.id}/words", params={"start": 70, "end": 100}).json()
        assert [w["word"] for w in words] == ["Rahul", "Punt here."] and words[1]["seg"] == 2
        assert c.patch(f"/api/jobs/{job.id}/words", json={"seg": 2, "i": 1, "word": "Pant"}).status_code == 409
        store.update(job.id, status=JobStatus.done)
        r = c.patch(f"/api/jobs/{job.id}/words", json={"seg": 2, "i": 1, "word": "Pant here."})
        assert [x["stale"] for x in r.json()["clips"]] == [False, True]  # only the short covering the word
        assert c.patch(f"/api/jobs/{job.id}/words", json={"seg": 2, "i": 7, "word": "x"}).status_code == 404
        assert c.post(f"/api/jobs/{job.id}/transcript/replace",
                      json={"find": "open ai", "replace": "OpenAI", "dry_run": True}).json() == \
            {"matches": 2, "job": None}
        r = c.post(f"/api/jobs/{job.id}/transcript/replace",
                   json={"find": "open ai", "replace": "OpenAI", "remember": True}).json()
        assert r["matches"] == 2 and all(x["stale"] for x in r["job"]["clips"])
        assert c.get("/api/vocabulary").json() == {"terms": ["OpenAI"]}
        assert c.put("/api/vocabulary", json={"terms": ["A", "a", "B"]}).json() == {"terms": ["A", "B"]}
        transcript = c.get(f"/api/jobs/{job.id}/transcript").json()
        assert transcript[0]["text"] == "I use OpenAI, every day."
