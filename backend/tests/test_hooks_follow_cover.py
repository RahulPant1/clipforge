"""L: hook variants · D: follow the person you choose · K: reel cover image."""
import json
import shutil
import subprocess
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.jobs import GPU_LOCK
from app.models import Clip, JobStatus, Post, RenderOptions, Segment, Word
from app.pipeline import cover, hooks
from app.pipeline.reframe import people, pick_face, plan_reframe

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _video(tmp_path, size="1280x720", seconds=4):
    v = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=25:duration={seconds}", "-c:v", "libx264", str(v)], check=True)
    return v


class Hooks:
    def __init__(self, variants):
        self.variants, self.prompts = variants, []

    def chat_json(self, prompt, schema, **kw):
        self.prompts.append((prompt, schema, kw))
        return {"variants": self.variants}

    def unload(self):
        pass


CLIP = Clip(index=1, start=0, end=30, title="Your resume is read by a robot", hook="Robots read resumes", score=8)


# ------------------------------------------------------------------ L: hook variants

def test_variants_are_cleaned_deduped_and_differ_from_the_current_hook():
    llm = Hooks([
        {"style": "question", "title": " Is a robot  reading your resume? ", "hook": "“Who reads your resume?”"},
        {"style": "bold_claim", "title": "x", "hook": "robots read resumes"},  # = current hook
        {"style": "number", "title": "75% never seen", "hook": "WHO READS YOUR RESUME?"},  # dupe of the first
        {"style": "made_up", "title": "Stop", "hook": "Stop writing resumes for humans"},
        {"style": "pov", "title": "", "hook": "no title"},
        {"style": "curiosity", "title": "t", "hook": " ".join(["word"] * 20)},
        "garbage",
    ])
    out = hooks.write_variants(CLIP, "they scan your resume for keywords", llm, "Hiring managers")
    assert [(v.style, v.title, v.hook) for v in out] == [
        ("question", "Is a robot reading your resume?", "Who reads your resume?"),
        ("other", "Stop", "Stop writing resumes for humans")]
    prompt, schema, kw = llm.prompts[0]
    assert "they scan your resume" in prompt and "Robots read resumes" in prompt and "Never invent" in prompt
    assert all(k in prompt for k in hooks.STYLES) and kw["think"] == settings.content_think
    with pytest.raises(ValueError):
        hooks.write_variants(CLIP, "x", Hooks([]))


def _app(tmp_path, llm=None):
    from app.main import create_app

    app = create_app(pipeline=lambda *a: [], start_worker=False, llm_factory=lambda: llm)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    d = store.dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    words = [Word(start=1, end=1.5, word=" robots"), Word(start=1.6, end=2, word=" read")]
    (d / "transcript.json").write_text(json.dumps([Segment(start=1, end=2, text="robots read",
                                                           words=words).model_dump()]))
    store.update(job.id, status=JobStatus.review, clips=[CLIP.model_copy(update={"end": 3.5})])
    return app, store, job.id, d


def test_api_hooks_saves_variants_and_refuses_when_busy(tmp_path):
    llm = Hooks([{"style": "question", "title": "Who reads it?", "hook": "Who reads your resume?"}])
    app, store, jid, _ = _app(tmp_path, llm)
    with TestClient(app) as c:
        r = c.post(f"/api/jobs/{jid}/clips/1/hooks")
        assert r.status_code == 200 and r.json()["clips"][0]["variants"][0]["hook"] == "Who reads your resume?"
        assert "robots read" in llm.prompts[0][0]
        with GPU_LOCK:
            assert c.post(f"/api/jobs/{jid}/clips/1/hooks").status_code == 409
        llm.variants = []
        assert c.post(f"/api/jobs/{jid}/clips/1/hooks").status_code == 502
        assert store.get(jid).clips[0].variants  # a failed retry keeps the old ones
        assert c.post(f"/api/jobs/{jid}/clips/9/hooks").status_code == 404


# ------------------------------------------------------------------ D: follow a chosen person

def test_pick_face_with_follow_sticks_to_that_person():
    big_right, small_left = (0.75, 0.08, 0.99), (0.3, 0.02, 0.9)
    assert pick_face([big_right, small_left], None) == 0.75  # default: the biggest
    assert pick_face([big_right, small_left], None, follow=0.28) == 0.3
    assert pick_face([big_right, (0.36, 0.02, 0.9)], 0.3, follow=0.28) == 0.36  # they moved a bit: still them
    assert pick_face([big_right], 0.3, follow=0.28) is None  # not in frame: hold, don't jump to the other person


class LeftSmallRightBig:
    def detect(self, img):
        return [(0.25, 0.01, 0.9, 0.4, 0.15), (0.75, 0.06, 0.99, 0.4, 0.3)]


class OnlyRight:
    def detect(self, img):
        return [(0.75, 0.06, 0.99, 0.4, 0.3)]


@needs_ffmpeg
def test_plan_reframe_follows_the_chosen_face(tmp_path):
    v = _video(tmp_path)
    crop = 404  # 720 * 9/16
    assert plan_reframe(v, 0, 3, LeftSmallRightBig(), mode="face").keyframes[0][1] == round(0.75 * 1280 - crop / 2)
    left = plan_reframe(v, 0, 3, LeftSmallRightBig(), mode="auto", follow=0.27)  # auto would split 2 people
    assert left.mode == "face" and left.keyframes[0][1] == round(0.25 * 1280 - crop / 2)
    gone = plan_reframe(v, 0, 3, OnlyRight(), mode="face", follow=0.27)  # never seen: stay on where they were
    assert gone.mode == "face" and gone.keyframes == [(0.0, round(0.27 * 1280 - crop / 2))]
    assert plan_reframe(v, 0, 3, LeftSmallRightBig(), mode="center", follow=0.27).mode == "center"


class Flicker:
    """Two people every frame, plus a 'poster' face in only one of the eight frames."""
    def __init__(self):
        self.n = 0

    def detect(self, img):
        self.n += 1
        faces = [(0.24 + 0.01 * (self.n % 2), 0.02, 0.9, 0.4, 0.2), (0.76, 0.05, 0.9, 0.45, 0.25)]
        return faces + ([(0.5, 0.004, 0.7, 0.2, 0.05)] if self.n == 3 else [])


@needs_ffmpeg
def test_people_groups_faces_into_persons(tmp_path):
    found = people(_video(tmp_path), 0, 3.5, Flicker())
    assert [p["x"] for p in found] == [pytest.approx(0.245, abs=0.01), 0.76]
    assert all(p["share"] == 1.0 and p["jpeg"][:2] == b"\xff\xd8" for p in found)  # JPEG thumbnails


# ------------------------------------------------------------------ K: cover image

def test_cover_text_precedence():
    c = CLIP.model_copy(deep=True)
    assert cover.default_text(c) == "Robots read resumes"
    c.content = {"instagram": Post(platform="instagram", angle="a", text="t", extra={"cover_text": "Robot recruiters"})}
    assert cover.default_text(c) == "Robot recruiters"
    c.cover_text = "Mine"
    assert cover.default_text(c) == "Mine"


def test_score_prefers_a_sharp_frame_with_a_big_frontal_face():
    import numpy as np

    rng = np.random.default_rng(0)
    sharp = (rng.random((360, 640, 3)) * 255).astype("uint8")
    flat = np.full((360, 640, 3), 128, dtype="uint8")
    face, turned = (0.5, 0.05, 0.9, 0.4, 0.2, 0.0), (0.5, 0.05, 0.9, 0.4, 0.2, 0.9)
    assert cover.score_frame(sharp, [face])[0] > cover.score_frame(flat, [face])[0]
    assert cover.score_frame(sharp, [face])[0] > cover.score_frame(sharp, [turned])[0] > cover.score_frame(sharp, [])[0]
    assert cover.score_frame(np.zeros((360, 640, 3), "uint8"), [])[0] < cover.score_frame(flat, [])[0] + 1e-9


class Face:
    def detect(self, img):
        return [(0.7, 0.05, 0.9, 0.4, 0.2, 0.0)]


@needs_ffmpeg
def test_make_cover_writes_a_vertical_jpeg_with_text_in_the_grid_band(tmp_path):
    from PIL import Image

    v = _video(tmp_path)
    clip = CLIP.model_copy(update={"end": 3.5, "removed": [(0, 1.5)]}, deep=True)
    best = cover.candidates(v, clip, Face(), n=4)
    assert best and all(c["t"] >= 1.5 for c in best) and best[0]["x"] == 0.7  # never a struck moment
    assert all(abs(a["t"] - b["t"]) >= 1 for a in best for b in best if a is not b)
    name = cover.make_cover(v, clip, tmp_path, detector=Face())
    img = Image.open(tmp_path / name)
    assert name == "short_1.cover.jpg" and img.size == (1080, 1920) and clip.cover_version == 1
    assert clip.cover_t is None  # automatic stays automatic
    bare = tmp_path / "bare.jpg"
    cover.draw_cover(cover._read(__import__("cv2").VideoCapture(str(v)), best[0]["t"]), 0.7, "", bare)
    import numpy as np

    diff = np.abs(np.asarray(img.convert("L"), int) - np.asarray(Image.open(bare).convert("L"), int))
    text_rows = np.flatnonzero(diff.max(axis=1) > 120)
    assert text_rows.size and min(text_rows) > cover.GRID_TOP and max(text_rows) < cover.GRID_BOTTOM


@needs_ffmpeg
def test_api_cover_frames_edit_and_export(tmp_path, monkeypatch):
    from app.pipeline import reframe

    monkeypatch.setattr(reframe, "_detector", Face())
    app, store, jid, d = _app(tmp_path)
    _video(d)
    with TestClient(app) as c:
        frames = c.get(f"/api/jobs/{jid}/clips/1/cover/frames").json()
        assert frames and frames[0]["img"].startswith("data:image/jpeg;base64,")
        assert c.post(f"/api/jobs/{jid}/clips/1/cover", json={"t": 99}).status_code == 400
        clip = c.post(f"/api/jobs/{jid}/clips/1/cover", json={"text": " Robot  HR ", "t": 2.0}).json()["clips"][0]
        assert (clip["cover"], clip["cover_text"], clip["cover_t"], clip["cover_version"]) == \
            ("short_1.cover.jpg", "Robot HR", 2.0, 1)
        clip = c.post(f"/api/jobs/{jid}/clips/1/cover", json={"auto": True}).json()["clips"][0]
        assert clip["cover_t"] is None and clip["cover_text"] == "Robot HR" and clip["cover_version"] == 2
        assert c.get(f"/api/jobs/{jid}/files/short_1.cover.jpg").status_code == 200
        ppl = c.get(f"/api/jobs/{jid}/clips/1/people").json()
        assert [p["x"] for p in ppl] == [0.7] and ppl[0]["img"].startswith("data:image/jpeg")
        store.update(jid, status=JobStatus.done)
        zipped = zipfile.ZipFile(app.state.store.dir(jid) / c.get(f"/api/jobs/{jid}/export.zip").headers[
            "content-disposition"].split("filename=")[1].strip('"'))
        assert "covers/short_1.cover.jpg" in zipped.namelist()


def test_follow_marks_a_rendered_short_stale(tmp_path):
    app, store, jid, _ = _app(tmp_path)
    store.update_clip(jid, 1, lambda c: setattr(c, "file", "short_1.mp4"))
    with TestClient(app) as c:
        clip = c.patch(f"/api/jobs/{jid}/clips/1", json={"options": RenderOptions(follow=0.3, reframe="face")
                                                         .model_dump()}).json()["clips"][0]
        assert clip["options"]["follow"] == 0.3 and clip["stale"]
