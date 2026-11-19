import shutil
import subprocess

import pytest

from app.config import settings
from app.pipeline import waveform

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _tone_gap_tone(path):
    """3 s loud tone, 2 s silence, 3 s quiet tone (−30 dB), with a video track like a real source."""
    vol = "volume='if(lt(t,3),1,if(lt(t,5),0,0.0316))':eval=frame"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8", "-af", vol, "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


@needs_ffmpeg
def test_levels_follow_the_audio_and_are_cached(tmp_path):
    _tone_gap_tone(tmp_path / "source.mp4")
    data = waveform.load(tmp_path)
    lv, r = data["levels"], data["rate"]
    assert r == waveform.RATE and abs(len(lv) - 8 * r) <= 2
    loud, gap, quiet = lv[r:2 * r], lv[int(3.5 * r):int(4.5 * r)], lv[6 * r:7 * r]
    assert min(loud) > 50 and max(gap) < 5  # lavfi sine is ~−21 dBFS RMS -> 65
    assert sum(loud) / len(loud) - sum(quiet) / len(quiet) == pytest.approx(50, abs=3)  # 30 dB = half the scale
    (tmp_path / "source.mp4").unlink()
    assert waveform.load(tmp_path) == data  # cached in waveform.json


def test_no_audio_is_empty_and_not_cached(tmp_path):
    assert waveform.load(tmp_path)["levels"] == []  # source.mp4 missing: ffmpeg fails
    assert not (tmp_path / "waveform.json").exists()


@needs_ffmpeg
def test_waveform_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = create_app(pipeline=lambda *a: [], start_worker=False)
    job = app.state.store.create("https://youtube.com/watch?v=x", 1)
    with TestClient(app) as c:
        assert c.get(f"/api/jobs/{job.id}/waveform").status_code == 404  # not downloaded yet
        _tone_gap_tone(app.state.store.dir(job.id) / "source.mp4")
        body = c.get(f"/api/jobs/{job.id}/waveform").json()
        assert body["rate"] == waveform.RATE and len(body["levels"]) > 100
        assert c.get("/api/jobs/nope/waveform").status_code == 404
