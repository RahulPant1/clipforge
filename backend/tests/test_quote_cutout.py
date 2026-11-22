"""Cutout covers (speaker cut out on a brand-tinted blur) and quote cards (a line said in the clip, 1:1 + 4:5)."""
import json
import shutil
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.models import Clip, JobStatus, Segment, Word
from app.pipeline import cover, cutout, quote

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _blob_mask(rgb):
    """Fake segmentation: a 'person' standing in the right third, cut off by the bottom of the frame."""
    h, w = rgb.shape[:2]
    m = np.zeros((h, w), np.uint8)
    m[int(h * 0.25):int(h * 0.45), int(w * 0.7):int(w * 0.75)] = 255  # head
    m[int(h * 0.45):, int(w * 0.6):int(w * 0.85)] = 255  # shoulders down
    return m


@pytest.fixture
def person(monkeypatch):
    calls = []
    monkeypatch.setattr(cutout, "_mask", lambda rgb: calls.append(1) or _blob_mask(rgb))
    cutout._cache.clear()
    return calls


def test_pick_person_takes_the_followed_one_and_ignores_specks():
    m = np.zeros((100, 200), np.uint8)
    m[20:, 10:60] = 255  # left person (bigger)
    m[40:, 140:180] = 255  # right person
    m[0:3, 100:103] = 255  # speck
    assert np.nonzero(cutout.pick_person(m, None))[1].max() < 60  # biggest by default
    assert np.nonzero(cutout.pick_person(m, 0.8))[1].min() >= 140  # the one we follow
    tiny = np.zeros((100, 200), np.uint8)
    tiny[0:5, 0:5] = 255
    assert cutout.pick_person(tiny, None) is None


def test_cutout_is_cropped_soft_and_cached(person):
    frame = np.full((360, 640, 3), 90, np.uint8)
    a = cutout.cutout(frame, 0.7, key=("v", 1.0))
    b = cutout.cutout(frame, 0.7, key=("v", 1.0))
    assert a is b and len(person) == 1  # cover + quote cards: one segmentation
    assert a.mode == "RGBA" and a.size == (160, 270) and a.info["touches_bottom"]


def test_no_face_or_a_boxy_text_card_is_not_a_person(monkeypatch):
    frame = np.full((360, 640, 3), 90, np.uint8)
    monkeypatch.setattr(cutout, "_mask", _blob_mask)
    assert cutout.cutout(frame, None) is None  # no face found in the frame
    card = np.zeros((360, 640), np.uint8)
    card[100:300, 300:620] = 255  # on-screen text card over a blurred face: a filled box
    monkeypatch.setattr(cutout, "_mask", lambda rgb: card)
    assert cutout.cutout(frame, 0.7) is None


def test_cutout_failure_means_no_cutout(monkeypatch):
    monkeypatch.setattr(cutout, "_mask", lambda rgb: (_ for _ in ()).throw(RuntimeError("no model")))
    assert cutout.cutout(np.zeros((90, 160, 3), np.uint8), 0.5) is None


def test_cutout_cover_puts_the_person_below_the_text(person, tmp_path):
    rng = np.random.default_rng(1)
    frame = (rng.random((720, 1280, 3)) * 255).astype(np.uint8)
    assert cover.draw_cutout_cover(frame, 0.72, "Robots read resumes", tmp_path / "c.jpg")
    img = np.asarray(Image.open(tmp_path / "c.jpg").convert("L"), int)
    assert img.shape == (1920, 1080)
    # the person (sharp noise) fills the lower part; the background above is a smooth blur
    assert img[1500:1900, 300:780].std() > 40 and img[60:220, 40:300].std() < 25


@needs_ffmpeg
def test_make_cover_cutout_or_frame(person, tmp_path, monkeypatch):
    from .test_hooks_follow_cover import CLIP, Face, _video

    v = _video(tmp_path)
    clip = CLIP.model_copy(update={"end": 3.5}, deep=True)
    cover.make_cover(v, clip, tmp_path, detector=Face())
    assert clip.cover_cutout and clip.cover == "short_1.cover.jpg"
    clip.cover_style = "frame"
    cover.make_cover(v, clip, tmp_path, detector=Face())
    assert not clip.cover_cutout
    monkeypatch.setattr(cutout, "_mask", lambda rgb: np.zeros(rgb.shape[:2], np.uint8))  # nobody in frame
    cutout._cache.clear()
    clip.cover_style = None
    cover.make_cover(v, clip, tmp_path, detector=Face())
    assert not clip.cover_cutout  # fell back to the frame cover


def _segs():
    text = ("So, um, here is the thing. Hiring managers use robots to read every resume first. "
            "And that changes how you write it. 75 percent of resumes never reach a human. Okay.")
    words, t = [], 0.0
    for tok in text.split():
        words.append(Word(start=t, end=t + 0.4, word=" " + tok))
        t += 0.5
    return [Segment(start=0, end=t, text=text, words=words)]


CLIP = Clip(index=1, start=0, end=40, title="Robots read resumes", hook="Robots read your resume",
            score=8, keywords=["resume", "robots", "human"])


def test_pick_quote_is_a_real_sentence_with_the_clips_keywords():
    q = quote.pick_quote(_segs(), CLIP)
    assert q == "Hiring managers use robots to read every resume first."
    struck = CLIP.model_copy(update={"removed": [(4.9, 9.6)]})  # strike that sentence: never quote cut words
    assert quote.pick_quote(_segs(), struck) == "75 percent of resumes never reach a human."
    assert quote.in_clip("every resume first", _segs(), CLIP) and not quote.in_clip("robots are evil", _segs(), CLIP)
    assert quote.pick_quote([], CLIP) == ""


def test_cards_are_square_and_portrait_and_keep_your_own_line(tmp_path, person):
    from .test_hooks_follow_cover import Face, _video

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    v = _video(tmp_path)
    clip = CLIP.model_copy(update={"end": 3.5}, deep=True)
    segs = _segs()
    names = quote.make_cards(v, clip, tmp_path, segs, {"title": "Resume tips", "channel": "Alex"}, detector=Face())
    assert names == ["short_1.quote.png", "short_1.quote45.png"] and clip.quote_version == 1
    assert Image.open(tmp_path / names[0]).size == (1080, 1080) and Image.open(tmp_path / names[1]).size == (1080, 1350)
    assert clip.quote  # picked from the clip
    clip.quote, clip.quote_custom = "My own line", True
    quote.make_cards(v, clip, tmp_path, segs, detector=Face())
    assert clip.quote == "My own line"
    clip.quote, clip.quote_custom = "a line from another clip", False
    quote.make_cards(v, clip, tmp_path, segs, detector=Face())
    assert clip.quote != "a line from another clip"  # not said here any more: picked again


def test_no_speech_clip_gets_no_cards(tmp_path):
    clip = CLIP.model_copy(deep=True)
    assert quote.make_cards(tmp_path / "none.mp4", clip, tmp_path, []) == [] and clip.quote_cards == []


@needs_ffmpeg
def test_api_quote_and_cover_style_and_export(tmp_path, monkeypatch, person):
    from app.main import create_app
    from app.pipeline import reframe

    from .test_hooks_follow_cover import Face, _video

    monkeypatch.setattr(reframe, "_detector", Face())
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    d = store.dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    _video(d)
    (d / "transcript.json").write_text(json.dumps([s.model_dump() for s in _segs()]))
    store.update(job.id, status=JobStatus.done, clips=[CLIP.model_copy(update={"end": 3.5, "file": "short_1.mp4"})])
    (d / "short_1.mp4").write_bytes(b"x")
    with TestClient(app) as c:
        u = f"/api/jobs/{job.id}/clips/1"
        clip = c.post(f"{u}/quote", json={"text": "  “Robots read first”  "}).json()["clips"][0]
        assert (clip["quote"], clip["quote_custom"], clip["quote_cards"]) == (
            "Robots read first", True, ["short_1.quote.png", "short_1.quote45.png"])
        clip = c.post(f"{u}/quote", json={"text": ""}).json()["clips"][0]
        assert not clip["quote_custom"] and clip["quote"] != "Robots read first" and clip["quote_version"] == 2
        assert c.post(f"{u}/cover", json={"style": "sparkles"}).status_code == 400
        clip = c.post(f"{u}/cover", json={"style": "frame"}).json()["clips"][0]
        assert clip["cover_style"] == "frame" and not clip["cover_cutout"]
        clip = c.post(f"{u}/cover", json={"style": "cutout"}).json()["clips"][0]
        assert clip["cover_cutout"]
        name = c.get(f"/api/jobs/{job.id}/export.zip").headers["content-disposition"].split("filename=")[1].strip('"')
    assert {"quotes/short_1.quote.png", "quotes/short_1.quote45.png"} <= set(zipfile.ZipFile(d / name).namelist())


def test_cutout_runs_on_the_cpu_only():
    assert cutout.MODEL == "u2net_human_seg"
    src = open(cutout.__file__).read()
    assert '"CPUExecutionProvider"' in src and "CUDAExecutionProvider" not in src
