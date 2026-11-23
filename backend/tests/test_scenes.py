"""Scene index: cuts → spans → one keyframe each → vision Gemma → ON SCREEN lines in the selection prompt."""
import json

import pytest

from app.config import settings
from app.models import Segment, Word
from app.pipeline import highlights, scenes
from app.pipeline.scenes import Scene, SceneIndex


class VisionLLM:
    """Fake vision Gemma: answers per call from `answers` (cycled); an Exception item is raised."""

    def __init__(self, answers=None, caps=("completion", "vision")):
        self.answers = answers or [{"description": "A laptop shows the router settings page.", "kind": "screen"}]
        self.calls, self.caps, self.model = [], caps, "fake-vision"

    def capabilities(self):
        return list(self.caps)

    def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
        assert images and len(images) == 1
        self.calls.append(prompt)
        a = self.answers[(len(self.calls) - 1) % len(self.answers)]
        if isinstance(a, Exception):
            raise a
        return a

    def unload(self):
        pass


@pytest.fixture
def fake_video(monkeypatch, tmp_path):
    """No ffmpeg: a 10-minute 'video' with given cuts / face share."""
    state = {"cuts": [60.0, 62.0, 200.0, 420.0], "faces": 0.4, "duration": 600.0}
    monkeypatch.setattr(scenes, "probe_duration", lambda v: state["duration"])
    monkeypatch.setattr(scenes, "frame_signals", lambda v, d, p=None: ([], list(state["cuts"])))
    monkeypatch.setattr(scenes, "face_share", lambda v, d, n=16: state["faces"])
    monkeypatch.setattr(scenes, "keyframe", lambda v, t, width=448: b"jpeg")
    return state


# ------------------------------------------------------------------ spans / auto

def test_spans_merge_short_split_long_and_cap(monkeypatch):
    monkeypatch.setattr(settings, "scene_span", 30.0)
    monkeypatch.setattr(settings, "scene_min", 4.0)
    got = scenes.spans([10.0, 12.0, 100.0], 130.0)
    assert got[0] == (0.0, 12.0)  # 10-12 (2 s) joined to the scene before
    assert [round(b - a) for a, b in got] == [12, 29, 29, 29, 30]  # 12-100 split in 3, 100-130 one span
    assert got[-1][1] == 130.0 and all(a < b for a, b in got)
    monkeypatch.setattr(settings, "scene_max", 10)
    many = scenes.spans([float(t) for t in range(5, 3600, 5)], 3600.0)
    assert len(many) <= 10 and many[0][0] == 0 and many[-1][1] == 3600.0


@pytest.mark.parametrize("cuts, faces, want", [
    (0.2, 1.0, False),  # Stanford speech / interview: one camera, a face all the time
    (11.7, 0.38, True),  # screen-heavy tutorial
    (0.5, 0.4, True),  # few cuts but mostly no face: slides / screen recording
    (3.0, 0.95, True),  # edited podcast with b-roll cutaways
])
def test_auto_describes_more_than_talking_heads(cuts, faces, want):
    assert scenes.worth_describing(cuts, faces)[0] is want


# ------------------------------------------------------------------ build

def test_build_describes_caches_and_validates(fake_video, tmp_path):
    llm = VisionLLM([{"description": "  **Slide** titled 'Q3 revenue' with a bar chart. ", "kind": "slides"},
                     {"description": "A man talks to camera.", "kind": "talking_head"},
                     {"description": "Hands plug in a router.", "kind": "nonsense"},
                     ConnectionError("ollama hiccup"),
                     {"description": "", "kind": "screen"}])
    idx = scenes.build(tmp_path / "v.mp4", tmp_path, llm, title="Home network tips", mode="auto")
    assert idx.mode == "described" and idx.cuts_per_min == 0.4  # 4 cuts in 10 min
    first = idx.scenes[0]
    assert first.start == 0.0 and first.kind == "slides"
    assert first.description == "Slide titled 'Q3 revenue' with a bar chart."
    assert idx.scenes[2].kind == "other"  # unknown kind
    assert not idx.scenes[3].description and not idx.scenes[4].description  # failed / empty: left out
    assert [s.kind for s in idx.for_prompt()][:2] == ["slides", "other"]  # talking head not in the prompt
    assert 'video ("Home network tips"), at 0:' in llm.calls[0]
    saved = json.loads((tmp_path / "scenes.json").read_text())
    assert saved["mode"] == "described" and saved["version"] == scenes.VERSION

    calls = len(llm.calls)
    again = scenes.build(tmp_path / "v.mp4", tmp_path, llm, mode="auto")
    assert len(llm.calls) == calls and len(again.described) == len(idx.described)  # cached


def test_talking_head_is_skipped_without_asking_gemma(fake_video, tmp_path):
    fake_video.update(cuts=[300.0], faces=1.0)
    llm = VisionLLM()
    idx = scenes.build(tmp_path / "v.mp4", tmp_path, llm, mode="auto")
    assert idx.mode == "skipped" and "talking head" in idx.reason and not llm.calls and idx.for_prompt() == []
    forced = scenes.build(tmp_path / "v.mp4", tmp_path, llm, mode="on")  # on: describe anyway (cache not used)
    assert forced.mode == "described" and llm.calls


def test_no_vision_model_or_every_frame_failing_means_no_descriptions(fake_video, tmp_path):
    idx = scenes.build(tmp_path / "v.mp4", tmp_path, VisionLLM(caps=("completion",)), mode="on")
    assert idx.mode == "skipped" and "can't see images" in idx.reason
    broken = VisionLLM([ValueError("bad json")])
    idx = scenes.build(tmp_path / "v.mp4", tmp_path, broken, mode="on")
    assert idx.mode == "skipped" and len(broken.calls) == scenes.GIVE_UP  # stopped asking
    assert scenes.build(tmp_path / "v.mp4", tmp_path, broken, mode="off").mode == "off"


# ------------------------------------------------------------------ selection prompt

def _segs() -> list[Segment]:
    out = []
    for i in range(12):
        s = i * 10.0
        out.append(Segment(start=s, end=s + 9.5, text=f"sentence number {i} about routers.",
                           words=[Word(start=s, end=s + 9.5, word=f" s{i}")]))
    return out


def test_build_prompt_puts_on_screen_lines_in_time_order():
    shots = [Scene(15.0, 40.0, 27.5, "A laptop shows the router settings page.", "screen"),
             Scene(200.0, 230.0, 215.0, "Outside the window.", "outdoor")]  # outside this window
    p = highlights.build_prompt(_segs()[:5], 2, scenes=shots)
    body = p.split("Transcript:\n", 1)[1].splitlines()
    assert body[1] == "[10.0-19.5] sentence number 1 about routers."
    assert body[2] == "  ↳ on screen (screen): A laptop shows the router settings page."  # no times: not a clip edge
    assert body[3].startswith("[20.0-29.5]")
    assert "are not speech" in p and "Outside the window" not in p
    assert "on screen" not in highlights.build_prompt(_segs()[:5], 2)  # no scenes → prompt unchanged
    assert "on screen" not in highlights.build_prompt(_segs()[:5], 2, scenes=shots[1:])  # none in this window


def test_windows_count_scene_lines_against_the_budget():
    segs = _segs()
    plain = list(highlights.windows(segs, 10_000, max_chars=200))
    shots = [Scene(s.start, s.end, s.start, "x" * 60, "screen") for s in segs]
    with_scenes = list(highlights.windows(segs, 10_000, max_chars=200, scenes=shots))
    assert len(with_scenes) > len(plain)


def test_selection_sees_scenes_and_trace_says_so():
    class Picker:
        prompts = []

        def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
            self.prompts.append(prompt)
            return {"clips": [{"start": 10.0, "end": 39.5, "title": "Router fix", "hook": "Do this", "score": 8,
                               "keywords": ["router"]}]}

    llm, trace = Picker(), {}
    shots = [Scene(15.0, 40.0, 27.5, "A laptop shows the router settings page.", "screen")]
    clips = highlights.select_highlights(_segs(), 1, llm, trace=trace, scenes=shots, think=None)
    assert clips and "↳ on screen (screen)" in llm.prompts[0] and trace["scenes"] == 1


# ------------------------------------------------------------------ prepare / API / eval

def test_scene_lines_never_fail_prepare(monkeypatch, tmp_path):
    from app.pipeline import orchestrator

    monkeypatch.setattr(settings, "scene_index", "off")
    assert orchestrator.scene_lines(tmp_path / "v.mp4", tmp_path, VisionLLM(), lambda *a: None, {}) is None
    monkeypatch.setattr(settings, "scene_index", "auto")

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(scenes, "build", boom)
    trace = {}
    assert orchestrator.scene_lines(tmp_path / "v.mp4", tmp_path, VisionLLM(), lambda *a: None, trace) == []
    assert trace["scene_index"]["mode"] == "failed"


def test_scenes_endpoint_and_eval_variants(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import eval as ev

    from .test_api import fake_pipeline

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    with TestClient(create_app(pipeline=fake_pipeline, start_worker=False)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1}).json()["id"]
        assert c.get(f"/api/jobs/{jid}/scenes").json() == {"mode": "none", "scenes": []}
        idx = SceneIndex(mode="described", reason="r", model="m",
                         scenes=[Scene(0, 30, 15, "A chart.", "slides"), Scene(30, 60, 45)])
        scenes.save(idx, tmp_path / "jobs" / jid)
        got = c.get(f"/api/jobs/{jid}/scenes").json()
        assert got["mode"] == "described" and [s["description"] for s in got["scenes"]] == ["A chart."]
        assert [s.description for s in ev.job_scenes(jid)] == ["A chart."]

    v = ev.Variant(model="m", prompt_name="default", prompt="p", think="default", scenes="on")
    assert v.label == "m · default · think=default · scenes"


def test_a_tail_too_short_for_a_clip_joins_the_window_before():
    from app.pipeline.highlights import _line

    segs = _segs()  # 12 × 10 s
    wins = list(highlights.windows(segs, 100.0))  # 0-100 s, then 110-119.5 s alone → one window
    assert len(wins) == 1 and len(wins[0]) == 12
    per_line = len(_line(segs[0])) + 1
    budget = 9 * per_line  # 9 lines fit: 9 + a 3-line (30 s) tail → split evenly 6 + 6, both in budget
    wins = list(highlights.windows(segs, 10_000, max_chars=budget))
    assert len(wins) == 2 and len(wins[1]) > 3  # balanced by chars (7 + 5 here), not 9 + a 3-line tail
    assert all(sum(len(_line(x)) + 1 for x in w) <= budget for w in wins)
    long = [Segment(start=i * 10.0, end=i * 10.0 + 9.5, text="x", words=[]) for i in range(40)]
    wins = list(highlights.windows(long, 150.0))  # 400 s: 4 windows of ~150 s / 100 s tail → kept apart
    assert all(w[-1].end - w[0].start >= 2 * settings.max_clip_seconds - 20 for w in wins)
