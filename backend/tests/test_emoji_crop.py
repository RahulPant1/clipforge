"""Colour emoji overlays + hand-set crop keys."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from app.config import settings
from app.models import Clip, CropKey, RenderOptions, Segment, Word
from app.pipeline import emoji
from app.pipeline.reframe import manual_path, plan_reframe, tracked_path

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _w(text, start, end=None):
    return Word(word=f" {text}", start=start, end=end if end is not None else start + 0.4)


@pytest.fixture
def fake_font(tmp_path, monkeypatch):
    """A stand-in for Noto Color Emoji: every emoji draws as a solid red square."""
    from PIL import Image

    font = tmp_path / "fake.ttf"
    font.write_bytes(b"x")
    monkeypatch.setattr(emoji, "font_path", lambda download=True: font)
    monkeypatch.setattr(emoji, "_render", lambda e, f: Image.new("RGBA", (120, 120), (255, 0, 0, 255)))
    return font


# ------------------------------------------------------------------ emoji

def test_clusters_keep_flags_skin_tones_and_joined_emoji_whole():
    assert emoji.clusters("go 🇮🇳 team 👍🏽 and 👨‍👩‍👧 x 🔥🔥 1️⃣ ❤️") == ["🇮🇳", "👍🏽", "👨‍👩‍👧", "🔥", "🔥", "1️⃣", "❤️"]
    assert emoji.clusters("no emoji here, 100% sure") == []


def test_words_map_to_emoji_with_stems_and_your_own_map():
    assert emoji.for_word(" Money,") == "💰" and emoji.for_word("investing") == "📈"
    assert emoji.for_word("salaries") == "💸" and emoji.for_word("dreams") is None
    assert emoji.for_word(" paisa") == "💰"  # Hinglish
    assert emoji.for_word("no") is None and emoji.for_word("time") is None  # too common to mean anything
    assert emoji.for_word("gold🏅") == "🏅"  # typed into the transcript wins
    (settings.data_dir).mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "emoji_map.json").write_text(json.dumps({"Dreams": "🌠", "money": ""}))
    assert emoji.for_word("dreams") == "🌠" and emoji.for_word("money") is None  # "" switches a word off


def test_caption_events_one_per_line_spaced_typed_first():
    chunks = [[_w("my", 0.0), _w("money", 0.4), _w("plan", 0.8)],
              [_w("more", 1.3), _w("money", 1.7)],  # < SPACING after the first: skipped
              [_w("stay", 3.0), _w("🚀", 3.4)],  # typed emoji: always shown
              [_w("gym", 6.0), _w("love", 6.4)],
              [_w("nothing", 9.0), _w("here", 9.4)]]
    ev = emoji.caption_events(chunks, keywords=["love"])
    assert [(s, e, x, i) for s, e, x, i in ev] == [(0.0, 1.3, "💰", 0), (3.0, 3.8, "🚀", 2), (6.0, 6.8, "❤️", 3)]


def test_png_is_drawn_once_at_the_asked_height(fake_font):
    p = emoji.png("🔥", 80)
    from PIL import Image

    assert p.name == "1f525-80.png" and Image.open(p).size == (80, 80)
    assert emoji.png("❤️", 50).name == "2764-50.png"  # variation selector left out of the name
    assert emoji.ready()


def test_no_font_means_no_emoji():
    assert emoji.font_path() is None and emoji.png("🔥", 80) is None and not emoji.ready()


def test_graph_overlays_each_emoji_for_its_time():
    g, out = emoji.graph("b", [emoji.Placed(0.0, 1.3, "💰", 480, 1300, 113)], first_input=2)
    assert g == "[2:v]format=rgba[em0];[b][em0]overlay=480:1300:enable='between(t,0.000,1.299)'[emv0]"
    assert out == "emv0"


def _src(tmp_path, d=4):
    """1280x720: left half red, right half blue — so a crop shows which side it's on."""
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s=640x720:r=25:d={d}",
                    "-f", "lavfi", "-i", f"color=c=blue:s=640x720:r=25:d={d}", "-f", "lavfi", "-i",
                    f"sine=f=300:d={d}", "-filter_complex", "[0:v][1:v]hstack[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


def _frame(path, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)


@needs_ffmpeg
def test_render_puts_the_emoji_above_the_caption_line(tmp_path, fake_font):
    from app.pipeline.render import render_clip

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x303030:s=1080x1920:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    words = [_w("save", 0.2), _w("your", 0.6), _w("money", 1.0, 1.6), _w("now", 2.0, 2.6)]
    segs = [Segment(start=0, end=3, text="save your money now", words=words)]
    clip = Clip(index=1, start=0, end=3, title="t", hook="Rich by 30? 🤑", score=7, keywords=["money"],
                options=RenderOptions(jumpcut=False, reframe="center", emoji=True))
    render_clip(src, clip, segs, tmp_path)

    f = _frame(tmp_path / "short_1.mp4", 0.5)
    red = (f[..., 0] > 200) & (f[..., 1] < 60) & (f[..., 2] < 60)
    ys = np.where(red.any(axis=1))[0]
    caption_top = 1920 - settings.caption_margin_v - 120  # a line of captions ends margin_v above the bottom
    lower = ys[ys > 960]
    assert len(lower) and lower.max() < caption_top and lower.max() > caption_top - 120  # just above the text
    upper = ys[ys < 400]
    assert len(upper) and upper.max() < settings.hook_margin_v  # hook emoji: above the hook card
    assert red[:, :400].sum() == 0 or red[:, 400:680].sum() > red[:, :400].sum()  # centred
    late = _frame(tmp_path / "short_1.mp4", 2.4)[960:]  # "now" has no emoji (the hook's stays its 3 s up top)
    assert ((late[..., 0] > 200) & (late[..., 1] < 60) & (late[..., 2] < 60)).sum() == 0

    clip.options = RenderOptions(jumpcut=False, reframe="center", emoji=False)
    render_clip(src, clip, segs, tmp_path)
    off = _frame(tmp_path / "short_1.mp4", 0.5)
    assert ((off[..., 0] > 200) & (off[..., 1] < 60) & (off[..., 2] < 60)).sum() == 0


@needs_ffmpeg
def test_emoji_on_without_a_font_renders_as_before(tmp_path, segs):
    from app.pipeline.render import render_clip

    src = _src(tmp_path, 3)
    clip = Clip(index=1, start=0, end=3, title="t", hook="Hi 🔥", score=7, keywords=["money"],
                options=RenderOptions(jumpcut=False, reframe="center", emoji=True))
    render_clip(src, clip, segs, tmp_path)
    assert (tmp_path / "short_1.mp4").exists() and clip.render_error is None


# ------------------------------------------------------------------ hand-set crop

def test_manual_path_holds_cuts_and_pans():
    keys = [CropKey(t=8, x=0.2), CropKey(t=12, x=0.8), CropKey(t=14, x=0.4, pan=True), CropKey(t=30, x=0.9)]
    path = manual_path(keys, start=10, end=20)  # clip 10-20 s: the 8 s key sets where it begins
    assert [(s.t, s.cx, s.cut) for s in path] == [(0.0, 0.2, False), (2.0, 0.8, True), (4.0, 0.4, False)]
    assert manual_path([CropKey(t=15, x=0.7)], 10, 20)[0].cx == 0.7  # first key inside: from the start
    assert manual_path([CropKey(t=25, x=0.7)], 10, 20) == []  # nothing for this clip → tracker decides


@needs_ffmpeg
def test_manual_mode_skips_face_detection_and_follows_the_keys(tmp_path):
    class Boom:
        def detect(self, img):
            raise AssertionError("no face detection for a hand-set crop")

    v = _src(tmp_path)
    p = plan_reframe(v, 0, 4, Boom(), mode="manual", keys=[CropKey(t=0, x=0.1), CropKey(t=2, x=0.9)])
    crop = 404  # 720 * 9/16
    assert p.mode == "manual" and p.keyframes[0] == (0.0, 0) and p.keyframes[-1] == (2.0, 1280 - crop)
    assert plan_reframe(v, 0, 4, mode="manual", keys=[]).mode == "center"  # (tests run with REFRAME_MODE=center)


@needs_ffmpeg
def test_render_follows_hand_set_keys_and_the_editor_reads_them_back(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import JobStatus
    from app.pipeline.render import render_clip

    src = _src(tmp_path)
    clip = Clip(index=1, start=0, end=4, title="t", hook="", score=7,
                options=RenderOptions(jumpcut=False, caption_style="none", reframe="manual",
                                      crop_keys=[CropKey(t=0, x=0.1), CropKey(t=2, x=0.9)]))
    render_clip(src, clip, [], tmp_path)
    assert clip.reframe == "manual"
    left, right = _frame(tmp_path / "short_1.mp4", 1.0), _frame(tmp_path / "short_1.mp4", 3.0)
    assert left[960, 540, 0] > 200 and left[960, 540, 2] < 60  # red side
    assert right[960, 540, 2] > 200 and right[960, 540, 0] < 60  # blue side after the 2 s cut
    tracked = tracked_path(tmp_path, "short_1", 1280, 404, 0.0)
    assert tracked[0] == (0.0, round(202 / 1280, 4)) and tracked[-1][0] == 2.0

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    shutil.copy(src, store.dir(job.id) / "source.mp4")
    shutil.copy(tmp_path / "short_1.crop.cmd", store.dir(job.id) / "short_1.crop.cmd")
    clip.file = "short_1.mp4"
    store.update(job.id, status=JobStatus.done, clips=[clip])
    with TestClient(app) as c:
        r = c.get(f"/api/jobs/{job.id}/clips/1/crop").json()
        assert (r["src_w"], r["src_h"]) == (1280, 720) and r["crop"]["9:16"] == round(404 / 1280, 4)
        assert r["crop"]["1:1"] == round(720 / 1280, 4) and r["mode"] == "manual"
        assert r["keys"][1] == {"t": 2.0, "x": 0.9, "pan": False} and r["tracked"][-1][0] == 2.0
        body = {"options": {"reframe": "manual", "crop_keys": [{"t": 1, "x": 0.5}]}}
        assert c.patch(f"/api/jobs/{job.id}/clips/1", json=body).json()["clips"][0]["stale"]
        bad = {"options": {"reframe": "manual", "crop_keys": [{"t": 1, "x": 1.5}]}}
        assert c.patch(f"/api/jobs/{job.id}/clips/1", json=bad).status_code == 422
        p = c.get("/api/caption-presets").json()
        assert p["emoji"] is False and p["emoji_ready"] is False
