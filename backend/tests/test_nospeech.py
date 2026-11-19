import json
import shutil
import subprocess

import numpy as np
import pytest

from app.config import settings
from app.models import Clip, RenderOptions, Stage
from app.pipeline import nospeech as ns
from app.pipeline import orchestrator
from app.pipeline.content import clip_text

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _quiet_video(tmp_path, seconds=90):
    """Calm grey shots that hard-cut at 30 s and 60 s; at 50-56 s a loud burst with lots of motion."""
    v = tmp_path / "quiet.mp4"
    shots = "[0:v][1:v][2:v]concat=n=3:v=1:a=0[bg]"
    burst = "[3:v]noise=alls=80:allf=t+u[n];[bg][n]overlay=0:0:enable='between(t,50,56)'[v]"
    audio = "[4:a]volume='if(between(t,50,56),1,0.03)':eval=frame[a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=0x303030:s=320x180:r=25:d=30",
                    "-f", "lavfi", "-i", "color=c=0xd0d0d0:s=320x180:r=25:d=30",
                    "-f", "lavfi", "-i", "color=c=0x606060:s=320x180:r=25:d=30",
                    "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=90",
                    "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:a=0.5",
                    "-filter_complex", f"{shots};{burst};{audio}", "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(v)], check=True)
    return v


class VisionLLM:
    """Scores by where the clip is (the prompt doesn't say, so use call order); records images."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.images = []
        self.calls = 0
        self.model = "fake-vision"

    def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
        self.calls += 1
        self.images.append(images)
        r = self.replies.pop(0) if self.replies else {}
        if isinstance(r, Exception):
            raise r
        return r

    def unload(self):
        pass


@needs_ffmpeg
def test_analyse_finds_cuts_loudness_and_motion(tmp_path):
    sig = ns.analyse(_quiet_video(tmp_path), cache=tmp_path / "signals.json")
    assert 89 < sig.duration < 91 and len(sig.loud) == len(sig.motion) == pytest.approx(180, abs=2)
    assert any(abs(c - 30) < 0.5 for c in sig.cuts) and any(abs(c - 60) < 0.5 for c in sig.cuts)
    loud = np.array(sig.loud)
    assert loud[106:110].mean() > loud[20:40].mean() + 20  # the burst is ~30 dB louder
    assert np.mean(sig.motion[102:110]) > 10 * (np.mean(sig.motion[10:50]) + 0.1)
    assert ns.analyse(tmp_path / "missing.mp4", cache=tmp_path / "signals.json") == sig  # cached


def _sig(n=240, peak=(100, 112), cuts=()):
    loud = [-40.0] * n
    motion = [1.0] * n
    for i in range(*peak):
        loud[i], motion[i] = -10.0, 20.0
    return ns.Signals(duration=n * ns.HOP, loud=loud, motion=motion, cuts=list(cuts))


def test_excitement_peaks_where_it_gets_loud_and_busy():
    exc = ns.excitement(_sig())
    assert 98 <= int(np.argmax(exc)) <= 113 and exc[105] > exc[30] + 1


def test_peak_window_builds_up_to_the_payoff_and_snaps_to_cuts(monkeypatch):
    monkeypatch.setattr(settings, "no_speech_clip_seconds", 30)
    sig = _sig()  # peak at 50-56 s
    (s, e, _), *_ = ns.peak_windows(sig, ns.excitement(sig), 1)
    assert e - s == pytest.approx(30) and s < 50 and e > 56 and (53 - s) / 30 > 0.5  # peak past the middle
    cut = _sig(cuts=[s + 2.0, e + 2.5])  # cuts near both edges
    (s2, e2, _), = ns.peak_windows(cut, ns.excitement(cut), 1)
    assert (s2, e2) == (s + 2.0, e + 2.5)
    far = _sig(cuts=[s - 4.0])  # too far to snap to
    assert ns.peak_windows(far, ns.excitement(far), 1)[0][:2] == (s, e)


def test_peak_windows_never_overlap_and_respect_limits(monkeypatch):
    monkeypatch.setattr(settings, "no_speech_clip_seconds", 300)  # clamped to max_clip_seconds
    sig = _sig(n=1200)
    wins = ns.peak_windows(sig, ns.excitement(sig), 6)
    assert wins and all(settings.min_clip_seconds <= e - s <= settings.max_clip_seconds + 1e-6 for s, e, _ in wins)
    spans = sorted((s, e) for s, e, _ in wins)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert all(0 <= s and e <= sig.duration for s, e, _ in wins)
    tiny = _sig(n=20, peak=(5, 8))
    assert ns.peak_windows(tiny, ns.excitement(tiny), 3) == []  # 10 s video


def _fake_analysis(monkeypatch, sig):
    monkeypatch.setattr(ns, "analyse", lambda video, cache=None, on_progress=None: sig)
    monkeypatch.setattr(ns, "keyframes", lambda video, s, e, width=448: [b"jpg1", b"jpg2"])


def test_select_no_speech_scores_with_vision_and_keeps_going_on_errors(monkeypatch):
    sig = _sig(n=1200, peak=(100, 112))
    for i in range(500, 512):  # a second, weaker peak at 250 s
        sig.loud[i], sig.motion[i] = -20.0, 10.0
    _fake_analysis(monkeypatch, sig)
    llm = VisionLLM([
        {"mostly_text": False, "score": 6, "title": "Big jump", "hook": "Watch this", "description": "A jump."},
        {"mostly_text": True, "score": 9, "title": "Credits", "hook": "x", "description": "Scrolling text."},
        RuntimeError("model does not support images"),
    ])
    trace: dict = {}
    clips = ns.select_no_speech("v.mp4", 1, llm, keep=3, title="Game", trace=trace)
    assert llm.images[0] == [b"jpg1", b"jpg2"]
    by_title = {c.title: c for c in clips}
    jump = by_title["Big jump"]
    assert jump.approved and jump.visual == "A jump." and jump.score == 6
    assert "Credits" not in by_title  # scored 9 but mostly text: capped to TEXT_SCORE, so it lost its place
    assert trace["clips"] and all(c["score"] > ns.TEXT_SCORE for c in trace["clips"])
    fallback = [c for c in clips if c.title.startswith("Highlight at ")]
    assert fallback and not fallback[0].visual  # failed call: signal score + generic title, job continues
    assert all(c.options.jumpcut is False and c.options.caption_style == "none" for c in clips)
    assert [c.start for c in clips] == sorted(c.start for c in clips)
    assert [c.index for c in clips] == list(range(1, len(clips) + 1))  # distinct short_N files
    assert trace["mode"] == "no_speech" and "error" in trace["items"][2] and trace["items"][1]["vision"]["mostly_text"]


def test_no_vision_model_means_signal_only(monkeypatch):
    _fake_analysis(monkeypatch, _sig(n=1200))

    class TextOnly(VisionLLM):
        def capabilities(self):
            return ["completion", "thinking"]

    llm = TextOnly([])
    clips = ns.select_no_speech("v.mp4", 2, llm, keep=3)
    assert llm.calls == 0 and clips and all(c.title.startswith("Highlight at ") for c in clips)
    assert sum(c.approved for c in clips) == 2
    monkeypatch.setattr(settings, "no_speech_vision", False)
    llm2 = VisionLLM([])
    ns.select_no_speech("v.mp4", 1, llm2)
    assert llm2.calls == 0


@needs_ffmpeg
def test_prepare_switches_to_no_speech_mode(tmp_path, monkeypatch):
    src = _quiet_video(tmp_path)
    monkeypatch.setattr(orchestrator, "transcribe", lambda video, job_dir, cb: [])  # music: nothing said
    monkeypatch.setattr(settings, "no_speech_clip_seconds", 25)
    stages = []
    llm = VisionLLM([{"mostly_text": False, "score": 8, "title": "Static burst", "hook": "Wait for it",
                      "description": "Grey noise fills the screen."}])
    job_dir = tmp_path / "job"
    clips = orchestrator.prepare(str(src), job_dir, 1, lambda st, p, m: stages.append((st, m)), llm=llm,
                                 candidates=1)
    assert len(clips) == 1 and clips[0].title == "Static burst" and clips[0].start < 50 and clips[0].end > 56
    assert len(llm.images[0]) == len(ns.KEYFRAMES) and llm.images[0][0][:2] == b"\xff\xd8"  # real JPEGs
    assert (job_dir / "signals.json").exists()
    assert json.loads((job_dir / "candidates.json").read_text())["mode"] == "no_speech"
    assert any("speech" in m for st, m in stages if st == Stage.selecting)
    monkeypatch.setattr(settings, "no_speech_mode", False)
    with pytest.raises(RuntimeError, match="Not enough speech"):
        orchestrator.prepare(str(src), job_dir, 1, lambda *a: None, llm=llm)


@needs_ffmpeg
def test_render_without_captions(tmp_path):
    from app.pipeline.render import render_clip

    src = _quiet_video(tmp_path)
    clip = Clip(index=1, start=40, end=62, title="t", hook="Wait for it", score=8,
                options=RenderOptions(jumpcut=False, caption_style="none"))
    assert render_clip(src, clip, [], tmp_path) == "short_1.mp4"
    assert not (tmp_path / "short_1.ass").exists() and clip.out_duration == pytest.approx(22, abs=0.1)


def test_posts_get_the_visual_description_when_nothing_is_said(segs):
    quiet = Clip(start=0, end=30, title="t", hook="h", score=7, visual="A skateboarder lands a kickflip.",
                 options=RenderOptions(caption_style="none"))
    assert "kickflip" in clip_text(segs, quiet) and "No speech" in clip_text(segs, quiet)
    talky = Clip(start=0, end=30, title="t", hook="h", score=7)
    assert clip_text(segs, talky).startswith("w0_0")


def test_ollama_client_sends_images_base64(monkeypatch):
    from app.pipeline import llm as llm_mod

    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"message": {"content": "{}"}, "capabilities": ["completion", "vision"]}

    def post(url, json=None, timeout=None):
        sent[url.rsplit("/", 1)[-1]] = json
        return Resp()

    monkeypatch.setattr(llm_mod.requests, "post", post)
    c = llm_mod.OllamaClient(url="http://x", model="m")
    c.chat_json("p", {}, images=[b"\xff\xd8abc"])
    assert sent["chat"]["messages"][0]["images"] == ["/9hhYmM="]
    c.chat_json("p", {})
    assert "images" not in sent["chat"]["messages"][0]
    assert c.capabilities() == ["completion", "vision"]


def test_custom_clip_in_no_speech_job_has_no_captions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import JobStatus

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = create_app(pipeline=lambda *a: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    (store.dir(job.id) / "signals.json").write_text("{}")
    store.update(job.id, status=JobStatus.review)
    with TestClient(app) as c:
        r = c.post(f"/api/jobs/{job.id}/clips", json={"start": 10, "end": 40, "title": "Mine"})
        opts = r.json()["clips"][0]["options"]
        assert opts["caption_style"] == "none" and opts["jumpcut"] is False
