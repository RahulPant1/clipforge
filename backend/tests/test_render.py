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
def _stream(path, sel: str) -> tuple[float, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", sel, "-count_packets", "-show_entries",
                          "stream=duration,nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    dur, _, packets = out.partition(",")
    return float(dur), packets


def test_jumpcut_shortens_output_and_keeps_av_in_sync(tmp_path, monkeypatch):
    # two words, then 6 s of silence, then two words -> silence should be cut
    from app.pipeline import render as render_mod

    words = [Word(start=1.0, end=1.5, word=" hello"), Word(start=1.6, end=2.0, word=" there"),
             Word(start=8.0, end=8.5, word=" big"), Word(start=8.6, end=9.0, word=" news")]
    segs = [Segment(start=1.0, end=9.0, text="hello there big news", words=words)]
    video = _video(tmp_path, 12)
    clip = Clip(index=1, start=0.0, end=10.0, title="t", hook="h", score=5)
    cmds: list[list[str]] = []
    real_run = render_mod.subprocess.run
    monkeypatch.setattr(render_mod.subprocess, "run", lambda cmd, **kw: cmds.append(cmd) or real_run(cmd, **kw))
    render_clip(video, clip, segs, tmp_path)

    out = tmp_path / "short_1.mp4"
    dur = float(_probe(out, "format=duration"))
    (v, v_packets), (a, a_packets) = _stream(out, "v:0"), _stream(out, "a:0")
    version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    diag = (f"\n{version}\nexpected {clip.out_duration}s; got format={dur}s video={v}s ({v_packets} pkts) "
            f"audio={a}s ({a_packets} pkts)\nrender cmd: "
            f"{' '.join(next(c for c in cmds if c[0] == 'ffmpeg' and '-vf' in c))}")
    assert clip.cut_seconds > 5, diag
    assert abs(dur - clip.out_duration) < 0.3, diag
    assert abs(v - a) < 0.15, diag


def _frame(path, t: float) -> bytes:
    """One 108x192 grayscale frame at output time t."""
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=108:192,format=gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout


def _diff(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


@needs_ffmpeg
def test_zoom_punches_in_on_alternate_segments_only(tmp_path, monkeypatch):
    from app.models import RenderOptions

    monkeypatch.setattr(settings, "reframe_mode", "center")
    words = [Word(start=1.0, end=1.5, word=" hello"), Word(start=1.6, end=2.0, word=" there"),
             Word(start=8.0, end=8.5, word=" big"), Word(start=8.6, end=9.0, word=" news")]
    segs = [Segment(start=1.0, end=9.0, text="", words=words)]
    video = _video(tmp_path, 12)
    outs = {}
    for zoom in (False, True):
        d = tmp_path / str(zoom)
        d.mkdir()
        clip = Clip(index=1, start=0.0, end=10.0, title="t", hook="h", score=5, options=RenderOptions(zoom=zoom))
        outs[zoom] = d / render_clip(video, clip, segs, d)
    # keeps ~[0.92, 2.08] and [7.92, 9.08] -> segment 0 plays 0-1.17 s, segment 1 (zoomed) 1.17-2.33 s
    first, second = 0.5, 1.8
    assert _diff(_frame(outs[False], first), _frame(outs[True], first)) < 1  # untouched (measured 0.02)
    assert _diff(_frame(outs[False], second), _frame(outs[True], second)) > 3  # zoomed (measured ~5)
    assert abs(float(_probe(outs[True], "format=duration")) - float(_probe(outs[False], "format=duration"))) < 0.05


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
