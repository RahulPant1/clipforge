"""Prompt-based clipping: an optional topic steers which moments Gemma picks."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import eval as ev
from app.config import settings
from app.models import Clip, JobStatus, Stage
from app.pipeline import highlights as hl
from app.pipeline import nospeech as ns
from app.pipeline import orchestrator

from .conftest import FakeLLM
from .test_nospeech import VisionLLM, _fake_analysis, _sig


class Spy(FakeLLM):
    def __init__(self, clips):
        super().__init__(clips)
        self.prompts, self.schemas = [], []

    def chat_json(self, prompt, schema, **kw):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return super().chat_json(prompt, schema, **kw)


def _pick(start, end, score, relevance=None, title="t"):
    d = {"start": start, "end": end, "title": title, "hook": "h", "score": score, "keywords": []}
    if relevance is not None:
        d["relevance"] = relevance
    return d


def test_clean_topic():
    assert hl.clean_topic('  salary\n "negotiation"  ') == "salary negotiation"
    assert hl.clean_topic(None) == "" and hl.clean_topic(" “ ” ") == ""
    assert len(hl.clean_topic("x" * 500)) == hl.MAX_TOPIC


def test_no_topic_leaves_the_prompt_and_schema_alone(segs):
    llm = Spy([_pick(0, 39.5, 8)])
    trace: dict = {}
    clips = hl.select_highlights(segs, 1, llm, trace=trace)
    assert "Topic:" not in llm.prompts[0] and "{focus}" not in llm.prompts[0]
    assert all(s is hl.CLIP_SCHEMA for s in llm.schemas)
    assert clips[0].relevance is None and trace["topic"] == ""


def test_topic_steers_the_prompt_drops_off_topic_and_ranks_by_relevance(segs):
    llm = Spy([
        _pick(0, 39.5, 9, relevance=3, title="viral but unrelated"),
        _pick(48, 87.5, 6, relevance=9, title="on topic"),
        _pick(96, 135.5, 8, relevance=5, title="touches on it"),
        _pick(144, 183.5, 7, title="no relevance field"),
    ])
    trace: dict = {}
    clips = hl.select_highlights(segs, 1, llm, keep=4, trace=trace, topic=' salary "negotiation" ')
    assert 'moments about "salary negotiation"' in llm.prompts[0]
    assert llm.prompts[0].index("Topic:") < llm.prompts[0].index("Transcript:")
    assert all(s is hl.TOPIC_SCHEMA for s in llm.schemas)
    assert "relevance" in hl.TOPIC_SCHEMA["properties"]["clips"]["items"]["required"]
    titles = {c.title for c in clips}
    assert titles == {"on topic", "touches on it"}  # off-topic and field-less picks are gone
    best = next(c for c in clips if c.approved)
    assert best.title == "on topic" and best.relevance == 9  # 6+9 beats 8+5, though 8 is more "viral"
    assert trace["topic"] == "salary negotiation"
    outcomes = [i.get("outcome") for i in trace["items"] if i["window"] == 0]
    assert outcomes[:4] == ["off_topic", "kept", "kept", "invalid"]
    assert {c["relevance"] for c in trace["clips"]} == {9, 5}


def test_custom_prompt_without_focus_still_gets_the_topic(segs):
    llm = Spy([])
    hl.select_highlights(segs[:3], 1, llm, prompt="pick {n} {min_s} {max_s}\n{transcript}", topic="goals")
    assert llm.prompts[0].startswith("pick ") and '"goals"' in llm.prompts[0]
    assert llm.prompts[0].index('"goals"') < llm.prompts[0].index("[0.0-")


def test_topic_with_nothing_found_explains_itself(tmp_path, segs, monkeypatch):
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(orchestrator, "transcribe", lambda video, job_dir, cb: segs)
    monkeypatch.setattr(orchestrator, "words_per_minute", lambda s, d=0: 150)
    with pytest.raises(RuntimeError, match="No moments about “cats” found"):
        orchestrator.prepare(str(src), tmp_path / "job", 1, lambda *a: None,
                             llm=FakeLLM([_pick(0, 39.5, 9, relevance=1)]), topic="cats")
    saved = json.loads((tmp_path / "job" / "candidates.json").read_text())
    assert saved["topic"] == "cats" and saved["items"][0]["outcome"] == "off_topic"


def test_no_speech_ranks_by_relevance_but_drops_nothing(monkeypatch):
    sig = _sig(n=1200, peak=(100, 112))
    for i in range(500, 512):
        sig.loud[i], sig.motion[i] = -20.0, 10.0
    _fake_analysis(monkeypatch, sig)
    seen = []

    class Rec(VisionLLM):
        def chat_json(self, prompt, schema, **kw):
            seen.append((prompt, schema))
            return super().chat_json(prompt, schema, **kw)

    llm = Rec([
        {"mostly_text": False, "score": 8, "title": "Crowd", "hook": "h", "description": "d", "relevance": 2},
        {"mostly_text": False, "score": 6, "title": "Goal", "hook": "h", "description": "d", "relevance": 9},
    ])
    trace: dict = {}
    clips = ns.select_no_speech("v.mp4", 1, llm, keep=3, trace=trace, topic="goals")
    assert 'moments about "goals"' in seen[0][0] and seen[0][1] is ns.VISION_TOPIC_SCHEMA
    by = {c.title: c for c in clips}
    assert by["Goal"].approved and not by["Crowd"].approved and by["Crowd"].relevance == 2
    assert trace["topic"] == "goals"
    _fake_analysis(monkeypatch, sig)
    plain = Rec([{"mostly_text": False, "score": 5, "title": "x", "hook": "h", "description": "d"}])
    seen.clear()
    ns.select_no_speech("v.mp4", 1, plain, keep=1)
    assert "moments about" not in seen[0][0] and seen[0][1] is ns.VISION_SCHEMA


def test_eval_takes_the_jobs_topic(tmp_path):
    job = tmp_path / "j1"
    job.mkdir()
    (job / "transcript.json").write_text("[]")
    (job / "job.json").write_text(json.dumps({"num_clips": 2, "topic": "pricing", "clips": []}))
    assert ev.load_target(str(job))[3:] == (2, "pricing")
    v = ev.Variant(model="m", prompt_name="default", prompt=hl.PROMPT, think="off", topic="pricing", trace={
        "items": [{"outcome": "kept"}, {"outcome": "off_topic"}],
        "clips": [{"start": 0, "end": 30, "score": 7, "relevance": 8, "title": "a", "hook": "h"}]})
    row = ev.summarize(v, [], None)
    assert row["off_topic"] == 1 and row["usable_rate"] == 0.5
    md = ev.report_md("j1", [row], [v], 2, 4)
    assert "topic “pricing”" in md and "on topic 8/10" in md


def _wait(c, jid, status, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/api/jobs/{jid}").json()
        if job["status"] == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job never reached {status}")


def test_api_passes_the_topic_to_selection_only_when_set(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app
    from app.pipeline.orchestrator import Steps

    seen = []

    def prepare(source, job_dir, num_clips, report, candidates=None, **kw):
        seen.append(kw)
        report(Stage.selecting, 1.0, "fake")
        return [Clip(index=1, start=0, end=30, title="c", hook="h", score=7)]

    def legacy_run(source, job_dir, num_clips, report):  # written before topics: no topic kwarg
        return prepare(source, job_dir, num_clips, report)

    steps = Steps(prepare=prepare, produce=lambda d, c, r: c, render_one=lambda d, c: c, run=legacy_run)
    with TestClient(create_app(steps=steps)) as c:
        url = "https://youtube.com/watch?v=x"
        r = c.post("/api/jobs", json={"url": url, "num_clips": 1, "topic": ' salary  "negotiation" '}).json()
        assert r["topic"] == "salary negotiation"
        _wait(c, r["id"], JobStatus.review.value)
        assert seen[-1] == {"topic": "salary negotiation"}

        r = c.post("/api/jobs/upload", files={"file": ("talk.mp4", b"data", "video/mp4")},
                   data={"num_clips": "1", "topic": "pricing"}).json()
        assert r["topic"] == "pricing"
        _wait(c, r["id"], JobStatus.review.value)
        assert seen[-1] == {"topic": "pricing"}

        jid = c.post("/api/jobs", json={"url": url, "num_clips": 1, "mode": "auto"}).json()["id"]
        _wait(c, jid, JobStatus.done.value)  # no topic → legacy callables still work
        assert seen[-1] == {}
        assert c.post("/api/jobs", json={"url": url, "topic": "x" * 201}).status_code == 422


def test_retry_can_change_or_clear_the_topic(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app
    from app.pipeline.orchestrator import Steps

    seen = []

    def prepare(source, job_dir, num_clips, report, candidates=None, topic=""):
        seen.append(topic)
        if topic == "cats":
            raise RuntimeError("No moments about “cats” found")
        return [Clip(index=1, start=0, end=30, title="c", hook="h", score=7)]

    steps = Steps(prepare=prepare, produce=lambda d, c, r: c, render_one=lambda d, c: c)
    with TestClient(create_app(steps=steps)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1,
                                        "topic": "cats"}).json()["id"]
        _wait(c, jid, JobStatus.failed.value)
        c.post(f"/api/jobs/{jid}/retry")  # no body: same topic, fails again
        _wait(c, jid, JobStatus.failed.value)
        r = c.post(f"/api/jobs/{jid}/retry", json={"topic": ' pets "and" animals '}).json()
        assert r["topic"] == "pets and animals"
        _wait(c, jid, JobStatus.review.value)
        assert seen == ["cats", "cats", "pets and animals"]
