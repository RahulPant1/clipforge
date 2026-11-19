import json

import pytest

from app import eval as ev
from app.models import Segment, Word
from app.pipeline import orchestrator
from app.pipeline.highlights import PROMPT, check_prompt, select_highlights

from .conftest import FakeLLM, make_segs

PICKS = [  # what the fake model "returns": kept, overlapping (lower score), too short, invalid
    {"start": 0, "end": 31.8, "title": "A", "hook": "h", "score": 9, "keywords": []},
    {"start": 8, "end": 39.8, "title": "B", "hook": "h", "score": 5, "keywords": []},
    {"start": 64, "end": 71.8, "title": "C", "hook": "h", "score": 7, "keywords": []},
    {"start": 80, "end": 111.8, "title": "D", "hook": "h", "score": "lots", "keywords": []},
]


def test_trace_records_every_pick_and_its_fate(segs):
    trace: dict = {}
    clips = select_highlights(segs, 1, FakeLLM(PICKS), keep=2, trace=trace)
    assert [c.title for c in clips] == ["A"]
    assert [i["outcome"] for i in trace["items"]] == ["kept", "overlap", "too_short", "invalid"]
    assert trace["items"][0]["start"] == 0 and trace["items"][0]["score"] == 9
    assert trace["windows"][0]["returned"] == 4 and "seconds" in trace["windows"][0]
    assert trace["clips"][0]["title"] == "A" and trace["clips"][0]["approved"] is True
    assert len(trace["prompt_id"]) == 10 and trace["asked_total"] == 2


def test_failed_window_is_in_the_trace(segs, monkeypatch):
    monkeypatch.setattr("app.config.settings.window_seconds", 100)

    class Flaky(FakeLLM):
        def chat_json(self, prompt, schema, temperature=None, think=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return {"clips": []}

    trace: dict = {}
    select_highlights(segs, 1, Flaky([]), trace=trace)
    assert trace["windows"][0]["error"] == "boom" and "error" not in trace["windows"][1]


def test_custom_prompt_and_explicit_think_reach_the_model(segs):
    seen = []

    class Spy(FakeLLM):
        def chat_json(self, prompt, schema, temperature=None, think=None):
            seen.append((prompt, think))
            return {"clips": []}

    tpl = "MY PROMPT {n} {min_s} {max_s}\n{transcript}"
    select_highlights(segs, 1, Spy([]), prompt=tpl, think=False)
    assert seen[0][0].startswith("MY PROMPT ") and seen[0][1] is False


def test_check_prompt_needs_the_placeholders():
    check_prompt(PROMPT)
    with pytest.raises(ValueError, match="transcript"):
        check_prompt("pick {n} clips {min_s} {max_s}")


def test_prepare_saves_candidates_json(tmp_path, segs, monkeypatch):
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(orchestrator, "transcribe", lambda video, job_dir, cb: segs)
    monkeypatch.setattr(orchestrator, "words_per_minute", lambda s: 150)
    job_dir = tmp_path / "job"
    orchestrator.prepare(str(src), job_dir, 1, lambda *a: None, llm=FakeLLM(PICKS), candidates=2)
    saved = json.loads((job_dir / "candidates.json").read_text())
    assert [i["outcome"] for i in saved["items"]] == ["kept", "overlap", "too_short", "invalid"]


def test_iou_and_matching():
    assert ev.iou((0, 10), (5, 15)) == pytest.approx(5 / 15)
    assert ev.iou((0, 10), (20, 30)) == 0
    assert ev.matched([(0, 30), (100, 130)], [(2, 31), (200, 230)]) == 1


def test_summarize_metrics():
    v = ev.Variant(model="m", prompt_name="default", prompt=PROMPT, think="off", seconds=12.34, trace={
        "windows": [{"window": 0}, {"window": 1, "error": "x"}],
        "items": [{"outcome": "kept"}, {"outcome": "kept"}, {"outcome": "overlap"}, {"outcome": "too_short"}],
        "clips": [{"start": 0, "end": 30, "score": 8}, {"start": 100, "end": 140, "score": 6}],
    })
    row = ev.summarize(v, picks=[(1, 31), (300, 330)], baseline=[(100, 141), (500, 530)])
    assert row["usable_rate"] == 0.75 and row["too_short"] == 1 and row["windows_failed"] == 1
    assert row["median_s"] == 35 and row["mean_score"] == 7 and row["seconds"] == 12.3
    assert row["picks_found"] == "1/2" and row["agree_baseline"] == 0.5


class ModelLLM(FakeLLM):
    """Two 'models': the good one returns usable clips, 'tiny' only too-short ones, 'missing' isn't pulled."""
    calls_by_model: dict = {}
    unloaded_models: list = []

    def __init__(self, model=None, url=None):
        super().__init__([])
        self.model = model

    def chat_json(self, prompt, schema, temperature=None, think=None):
        ModelLLM.calls_by_model[self.model] = ModelLLM.calls_by_model.get(self.model, 0) + 1
        if self.model == "missing":
            raise RuntimeError("Ollama 404: model 'missing' not found")
        if self.model == "tiny":
            return {"clips": [{"start": 0, "end": 7.8, "title": "t", "hook": "h", "score": 9, "keywords": []}]}
        return {"clips": PICKS[:1]}

    def unload(self):
        ModelLLM.unloaded_models.append(self.model)


def test_eval_cli_end_to_end(tmp_path, monkeypatch, capsys):
    job = tmp_path / "jobs" / "abc"
    job.mkdir(parents=True)
    segs = make_segs()
    (job / "transcript.json").write_text(json.dumps([s.model_dump() for s in segs]))
    (job / "job.json").write_text(json.dumps({"num_clips": 1, "clips": [
        {"start": 0, "end": 31.8, "approved": True}, {"start": 200, "end": 230, "approved": False}]}))
    custom = tmp_path / "short_prompt.txt"
    custom.write_text("Be brief. {n} clips, {min_s}-{max_s} s.\n{transcript}")
    monkeypatch.setattr(ev, "OllamaClient", ModelLLM)
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    ModelLLM.calls_by_model, ModelLLM.unloaded_models = {}, []

    ev.main(["abc", "--model", "good", "--model", "tiny", "--model", "missing",
             "--prompt", "default", "--prompt", str(custom), "--out", str(tmp_path / "out")])

    res = json.loads((tmp_path / "out" / "results.json").read_text())
    rows = {r["label"]: r for r in res["rows"]}
    assert len(rows) == 6 and res["picks"] == [[0, 31.8]] and res["candidates"] == 2
    base = rows["good · default · think=default"]
    assert base["candidates"] == 1 and base["picks_found"] == "1/1" and "agree_baseline" not in base
    assert rows["good · short_prompt · think=default"]["agree_baseline"] == 1.0
    tiny = rows["tiny · default · think=default"]
    assert tiny["candidates"] == 0 and tiny["too_short"] >= 1 and tiny["picks_found"] == "0/1"
    missing = rows["missing · default · think=default"]
    assert "not found" in missing["error"] and missing["windows_failed"] == missing["windows"] == 1
    assert ModelLLM.unloaded_models == ["good", "tiny", "missing"]  # one model in VRAM at a time
    md = (tmp_path / "out" / "report.md").read_text()
    assert "| variant |" in md and "**Failed:** Gemma failed on every" in md and "404" in md and "✓ `0:00–0:31`" in md


def test_eval_rejects_prompt_without_placeholders(tmp_path, monkeypatch):
    job = tmp_path / "j"
    job.mkdir()
    (job / "transcript.json").write_text(json.dumps([Segment(start=0, end=1, text="hi",
                                                             words=[Word(start=0, end=1, word="hi")]).model_dump()]))
    bad = tmp_path / "bad.txt"
    bad.write_text("pick some clips")
    with pytest.raises(SystemExit, match="missing placeholders"):
        ev.main([str(job), "--prompt", str(bad)])


def test_print_prompt(capsys):
    ev.main(["--print-prompt"])
    assert capsys.readouterr().out.strip() == PROMPT.strip()
