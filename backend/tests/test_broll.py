import shutil
import subprocess
from io import BytesIO

import numpy as np
import pytest

from app.models import BrollPlacement, Clip, RenderOptions
from app.pipeline import broll, render

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def video(path, color, seconds, frequency):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color={color}:s=160x90:r=25:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f={frequency}:d={seconds}", "-pix_fmt", "yuv420p", "-shortest", str(path),
    ], check=True)


def test_local_broll_replaces_picture_in_range_and_keeps_main_audio(tmp_path):
    source, cutaway = tmp_path / "source.mp4", tmp_path / "cutaway.mp4"
    video(source, "red", 4, 400)
    video(cutaway, "blue", 0.6, 1200)
    with cutaway.open("rb") as stream:
        asset = broll.save(tmp_path, 1, stream)
    original = asset.read_bytes()
    with pytest.raises(ValueError, match="readable MP4"):
        broll.save(tmp_path, 1, BytesIO(b"invalid file"))
    assert asset.read_bytes() == original

    clip = Clip(index=1, start=0, end=4, title="test", hook="", score=5, options=RenderOptions(
        broll=BrollPlacement(start=1, end=3, revision=1), jumpcut=False, caption_style="none",
        reframe="center", sfx=False, hook=False, end_card=False, aspects=[],
    ))
    output = tmp_path / render.render_clip(source, clip, [], tmp_path, preview=True)

    def center(seconds):
        data = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(output), "-frames:v", "1",
            "-vf", "crop=2:2:180:320,format=rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True).stdout
        return data[0], data[2]

    assert center(0.4)[0] > 200
    assert center(1.2)[1] > 200
    assert center(2.6)[1] > 200  # longer than the upload, so it has looped
    assert center(3.4)[0] > 200
    audio = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "1.2", "-t", "1", "-i", str(output),
        "-ac", "1", "-ar", "8000", "-f", "f32le", "-",
    ], capture_output=True, check=True).stdout
    spectrum = np.abs(np.fft.rfft(np.frombuffer(audio, "<f4")))
    assert spectrum[400] > 100 * spectrum[1200]


def test_library_suggests_relevant_files_and_attaches_only_the_chosen_asset(tmp_path, monkeypatch):
    from app.config import settings
    from app.main import create_app
    from app.models import JobStatus

    folder = tmp_path / "library"
    folder.mkdir()
    monkeypatch.setattr(settings, "broll_dir", folder)
    expected = folder / "interview-practice.mp4"
    video(expected, "blue", 1, 400)
    (folder / "office.mp4").write_bytes(b"weaker scene match")
    (folder / "forest.mp4").write_bytes(b"unrelated")
    other = tmp_path / "outside.mp4"
    other.write_bytes(b"external")
    (folder / "interview-outside.mp4").symlink_to(other)

    app = create_app(start_worker=False)
    endpoints = {route.name: route.endpoint for route in app.routes if hasattr(route, "endpoint")}
    store = app.state.store
    job = store.create("source", 1)
    clip = Clip(index=1, start=0, end=5, title="Interview practice", hook="", score=5, approved=True)
    store.update(job.id, status=JobStatus.review, clips=[clip])
    suggestions = endpoints["suggest_broll"](job.id, 1)
    assert [item["name"] for item in suggestions["items"]] == [expected.name]
    assert [item["name"] for item in broll.suggest(clip.title, "office")] == [expected.name, "office.mp4"]
    assert not store.get(job.id).clips[0].options.broll

    asset = suggestions["items"][0]["id"]
    assert endpoints["preview_broll_asset"](asset).path == expected
    updated = endpoints["use_broll_asset"](job.id, 1, asset)
    first = updated.clips[0].options.cutaways()[0]
    assert broll.path(store.dir(job.id), 1, first.asset).read_bytes() == expected.read_bytes()
    attached = endpoints["use_broll_asset"](job.id, 1, asset).clips[0].options.cutaways()
    assert attached[0] == first and attached[1].asset != first.asset
    assert [(item.start, item.end) for item in attached] == [(0, 4), (4, 5)]


def test_render_combines_legacy_and_new_cutaways_at_distinct_times(tmp_path):
    source = tmp_path / "source.mp4"
    video(source, "red", 5, 400)
    legacy = broll.path(tmp_path, 1)
    new = broll.path(tmp_path, 1, "21ab33445566")
    video(legacy, "blue", 0.6, 1200)
    video(new, "lime", 0.7, 1600)
    first = BrollPlacement(start=0.8, end=1.8, revision=1)
    second = BrollPlacement(start=3, end=4, revision=2, asset="21ab33445566")
    with pytest.raises(ValueError, match="cannot overlap"):
        RenderOptions(broll=first, brolls=[second.model_copy(update={"start": 1})])
    clip = Clip(index=1, start=0, end=5, title="test", hook="", score=5, options=RenderOptions(
        broll=first, brolls=[second], jumpcut=False, caption_style="none", reframe="center",
        sfx=False, hook=False, end_card=False, aspects=[],
    ))
    preview = tmp_path / render.render_clip(source, clip, [], tmp_path, preview=True)
    for time, channel in [(0.4, 0), (1.2, 2), (2.4, 0), (3.4, 1), (4.5, 0)]:
        pixels = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(time), "-i", str(preview), "-frames:v", "1",
            "-vf", "crop=2:2:180:320,format=rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True).stdout
        assert pixels[channel] > 200
