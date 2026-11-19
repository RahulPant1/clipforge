import shutil
import subprocess

import pytest

from app.config import settings
from app.models import Clip, Segment, Word
from app.pipeline.render import render_clip


def _probe(path, entries):
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout.strip()


def _video(tmp_path, seconds=30):
    video = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=25:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video)], check=True)
    return video


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@needs_ffmpeg
@pytest.mark.parametrize("style", ["karaoke", "simple"])
def test_render_vertical_with_captions(tmp_path, segs, monkeypatch, style):
    monkeypatch.setattr(settings, "caption_style", style)
    video = _video(tmp_path)
    clip = Clip(index=1, start=8.0, end=24.0, title="t", hook="h", score=7, keywords=["w1_0"])
    name = render_clip(video, clip, segs, tmp_path)
    out = tmp_path / name
    assert out.exists() and (tmp_path / "short_1.jpg").exists() and (tmp_path / "short_1.srt").exists()
    assert _probe(out, "stream=width,height").splitlines()[0] == "1080,1920"


@needs_ffmpeg
def test_jumpcut_shortens_output_and_keeps_av_in_sync(tmp_path):
    # two words, then 6 s of silence, then two words -> silence should be cut
    words = [Word(start=1.0, end=1.5, word=" hello"), Word(start=1.6, end=2.0, word=" there"),
             Word(start=8.0, end=8.5, word=" big"), Word(start=8.6, end=9.0, word=" news")]
    segs = [Segment(start=1.0, end=9.0, text="hello there big news", words=words)]
    video = _video(tmp_path, 12)
    clip = Clip(index=1, start=0.0, end=10.0, title="t", hook="h", score=5)
    render_clip(video, clip, segs, tmp_path)

    assert clip.cut_seconds > 5
    dur = float(_probe(tmp_path / "short_1.mp4", "format=duration"))
    assert abs(dur - clip.out_duration) < 0.3
    v = float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=duration", "-of", "csv=p=0", str(tmp_path / "short_1.mp4")],
                             capture_output=True, text=True).stdout.strip())
    a = float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "stream=duration", "-of", "csv=p=0", str(tmp_path / "short_1.mp4")],
                             capture_output=True, text=True).stdout.strip())
    assert abs(v - a) < 0.15


class _FaceRight:
    def detect(self, img):
        return [(0.85, 0.03, 0.99)]


@needs_ffmpeg
def test_render_with_face_tracking_sendcmd(tmp_path, segs, monkeypatch):
    pytest.importorskip("cv2")
    from app.pipeline import reframe

    monkeypatch.setattr(settings, "reframe_mode", "face")
    monkeypatch.setattr(reframe, "_detector", _FaceRight())
    video = _video(tmp_path, 12)
    clip = Clip(index=1, start=0.0, end=10.0, title="t", hook="h", score=7)
    render_clip(video, clip, segs, tmp_path)
    assert clip.reframe == "face"
    assert (tmp_path / "short_1.crop.cmd").read_text().startswith("0.000 crop@track x ")
    assert _probe(tmp_path / "short_1.mp4", "stream=width,height").splitlines()[0] == "1080,1920"


def test_cuda_preload_never_breaks_transcription(tmp_path, monkeypatch, caplog):
    """No GPU needed: a missing wheel or unloadable .so must log and fall back (to LD_LIBRARY_PATH), not raise."""
    import logging
    import sys

    from app.pipeline import transcribe

    pkg = tmp_path / "fakenvidia" / "lib"  # namespace package like nvidia.cublas.lib (no __init__.py)
    pkg.mkdir(parents=True)
    (pkg / "libcublas.so.12").write_bytes(b"not an ELF file")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(transcribe, "_CUDA_WHEELS", {"fakenvidia.lib": ("libcublas.so.*",), "missing.pkg": ("x",)})
    monkeypatch.setattr(transcribe, "_cuda_preloaded", False)
    with caplog.at_level(logging.WARNING):
        assert transcribe.preload_cuda_wheels() == []
    assert "libcublas.so.12" in caplog.text
    assert transcribe.preload_cuda_wheels() == []  # only attempted once per process
    sys.modules.pop("fakenvidia.lib", None)
    sys.modules.pop("fakenvidia", None)
