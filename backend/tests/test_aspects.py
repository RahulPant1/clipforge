"""Feed versions: 4:5 and 1:1 renders next to the 9:16 short."""
import json
import shutil
import subprocess

import pytest

from app.config import settings
from app.models import BrandKit, Clip, RenderOptions
from app.pipeline import brand
from app.pipeline.captions import build_ass, build_hook_ass
from app.pipeline.presets import placement
from app.pipeline.reframe import Speaker, split_panels
from app.pipeline.render import aspect_file, extra_aspects

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_which_feed_versions_a_short_gets(monkeypatch):
    assert extra_aspects(RenderOptions()) == []  # default: just the 9:16
    monkeypatch.setattr(settings, "extra_aspects", " 1:1, 4:5 ,16:9")
    assert extra_aspects(RenderOptions()) == ["4:5", "1:1"]  # fixed order, unknown ones ignored
    assert extra_aspects(RenderOptions(aspects=[])) == []  # the short's own choice wins
    assert extra_aspects(RenderOptions(aspects=["1:1"])) == ["1:1"]
    assert aspect_file(3, "4:5") == "short_3.45.mp4" and aspect_file(3, "9:16") == "short_3.mp4"
    with pytest.raises(ValueError):
        RenderOptions(aspects=["2:3"])


def test_text_is_placed_for_the_canvas(segs):
    words = [w for s in segs for w in s.words]
    assert "PlayResY: 1350" in build_ass(words, [], height=1350)
    assert placement("bottom", 1080)[1] == round(settings.caption_margin_v * 1080 / 1920)
    hook = build_hook_ass("A hook", 5, height=1080)
    assert f"Hook,{settings.caption_font},{round(settings.hook_size * 0.75)}," in hook  # 75% on 1:1
    assert "PlayResY: 1080" in hook and f",8,110,110,{round(settings.hook_margin_v * 1080 / 1920)}," in hook
    assert ",2,110,110,1040," in build_hook_ass("A hook", 5, seam=True)  # 9:16 seam layout unchanged
    assert ",2,110,110,620," in build_hook_ass("A hook", 5, seam=True, height=1080)  # just above y=540


def test_square_split_panels_are_two_to_one():
    left, right = Speaker(0.25, 0.4, 0.2), Speaker(0.75, 0.4, 0.2)
    for w, h, x, y in split_panels(left, right, 1920, 1080, panel=(1080, 540)):
        assert abs(w / h - 2) < 0.02 and x + w <= 1920 and y + h <= 1080


def _probe(path):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                     "stream=codec_type,width,height,duration", "-of", "json", str(path)],
                                    capture_output=True, text=True).stdout)["streams"]
    return {s["codec_type"]: s for s in out}


@needs_ffmpeg
def test_render_makes_feed_versions_and_removes_them_when_switched_off(tmp_path, segs):
    from app.pipeline.render import render_clip

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25:d=4",
                    "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    brand.save(BrandKit(end_seconds=1.0, end_text="Follow for more"))  # end card on every format
    clip = Clip(index=1, start=0.0, end=4.0, title="t", hook="A hook", score=7,
                options=RenderOptions(jumpcut=False, reframe="center", aspects=["4:5", "1:1"]))
    assert render_clip(src, clip, segs, tmp_path) == "short_1.mp4"
    assert clip.formats == ["4:5", "1:1"] and clip.render_error is None and clip.out_duration == 5.0

    for f, (w, h) in [("short_1.mp4", (1080, 1920)), ("short_1.45.mp4", (1080, 1350)),
                      ("short_1.11.mp4", (1080, 1080))]:
        s = _probe(tmp_path / f)
        assert (s["video"]["width"], s["video"]["height"]) == (w, h)
        assert abs(float(s["video"]["duration"]) - 5.0) < 0.15 and abs(float(s["audio"]["duration"]) - 5.0) < 0.15
    assert "PlayResY: 1080" in (tmp_path / "short_1.11.ass").read_text()
    from PIL import Image

    assert Image.open(tmp_path / "short_1.45.end.png").size == (1080, 1350)

    clip.options = RenderOptions(jumpcut=False, reframe="center", aspects=["1:1"])
    render_clip(src, clip, segs, tmp_path)
    assert clip.formats == ["1:1"] and not (tmp_path / "short_1.45.mp4").exists()
    assert not (tmp_path / "short_1.45.ass").exists() and (tmp_path / "short_1.11.mp4").exists()


@needs_ffmpeg
def test_a_failed_feed_version_keeps_the_short(tmp_path, segs, monkeypatch):
    from app.pipeline import render

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=f=300:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    real = render._encode

    def flaky(video, clip, plan, job_dir, name, size, *a):
        if size == (1080, 1080):
            raise RuntimeError("boom")
        return real(video, clip, plan, job_dir, name, size, *a)

    monkeypatch.setattr(render, "_encode", flaky)
    clip = Clip(index=2, start=0.0, end=3.0, title="t", hook="", score=7,
                options=RenderOptions(jumpcut=False, reframe="center", caption_style="none", aspects=["4:5", "1:1"]))
    render.render_clip(src, clip, segs, tmp_path)
    assert clip.formats == ["4:5"] and "1:1 version failed: boom" in clip.render_error
    assert (tmp_path / "short_2.mp4").exists() and not clip.stale


def test_export_pack_puts_feed_versions_in_their_own_folder(tmp_path):
    import zipfile

    from app.models import Job
    from app.pipeline.export import build_pack

    for f in ("short_1.mp4", "short_1.45.mp4", "short_1.11.mp4"):
        (tmp_path / f).write_bytes(b"x")
    job = Job(id="j", source="s", clips=[Clip(index=1, start=0, end=5, title="t", hook="", score=5,
                                              file="short_1.mp4", formats=["4:5"])])
    names = zipfile.ZipFile(build_pack(job, tmp_path)).namelist()
    assert "shorts/short_1.mp4" in names and "feed/short_1.45.mp4" in names
    assert not any(n.endswith(".11.mp4") for n in names)  # not in clip.formats: an old file, left out


def test_api_feed_versions_option(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import JobStatus

    monkeypatch.setattr(settings, "extra_aspects", "4:5")
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    store.update(job.id, status=JobStatus.done,
                 clips=[Clip(index=1, start=0, end=30, title="T", hook="H", score=8, file="short_1.mp4")])
    with TestClient(app) as c:
        p = c.get("/api/caption-presets").json()
        assert p["aspects"] == ["4:5"] and p["feed_aspects"] == ["4:5", "1:1"]
        r = c.patch(f"/api/jobs/{job.id}/clips/1", json={"options": {"aspects": ["4:5", "1:1"]}})
        assert r.status_code == 200 and r.json()["clips"][0]["stale"]  # a new file to make → re-render
        assert c.patch(f"/api/jobs/{job.id}/clips/1", json={"options": {"aspects": ["16:9"]}}).status_code == 422
