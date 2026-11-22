"""Shots without a face (screen recordings, slides, action) are shown whole over a blurred copy, not centre-cropped."""
import shutil
import subprocess

import pytest

from app.config import settings
from app.models import Clip
from app.pipeline.reframe import ReframePlan, Sample, no_face_spans, plan_reframe

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
FACE = (0.5, 0.03, 0.9)


def _shot(t0, n, face, cut=True):
    return [Sample(t=t0 + k * 0.2, cx=0.5 if face else None, cut=cut and k == 0, faces=[FACE] if face else [])
            for k in range(n)]


def test_no_face_spans_are_whole_shots():
    samples = _shot(0, 10, True, cut=False) + _shot(2.0, 10, False) + _shot(4.0, 10, True)
    assert no_face_spans(samples, 6.0) == [(2.0, 4.0)]
    assert no_face_spans(_shot(0, 10, False, cut=False) + _shot(2.0, 10, False), 4.0) == [(0.0, 4.0)]  # merged


def test_a_few_missed_detections_or_a_false_cut_do_not_flicker():
    patchy = _shot(0, 10, True, cut=False)
    for s in patchy[::3]:
        s.faces, s.cx = [], None  # face found in 6 of 10 samples: still a face shot
    blip = _shot(2.0, 2, False)  # 0.4 s "shot" (a false cut) without faces: takes the shot before it
    assert no_face_spans(patchy + blip + _shot(2.4, 10, True), 4.4) == []


def test_another_persons_face_still_counts_as_a_face_shot():
    other = [Sample(t=k * 0.2, cx=None, faces=[(0.2, 0.03, 0.9)]) for k in range(10)]  # followed person absent
    assert no_face_spans(other, 2.0) == []


def test_fit_filters():
    assert "boxblur" in ReframePlan("fit", 1920, 1080, 606).filters(None)[0]
    plan = ReframePlan("face", 1920, 1080, 606, [(0.0, 100)], fit=[(2.0, 4.0), (6.5, 7.0)])
    (graph,) = plan.filters("c.cmd")
    assert "sendcmd=f=c.cmd,crop@track=606:1080:100:0,scale=1080:1920" in graph
    assert graph.endswith("overlay=enable='between(t,2.000,4.000)+between(t,6.500,7.000)'")
    assert ReframePlan("face", 1920, 1080, 606, [(0.0, 100)]).filters("c.cmd")[0] == "sendcmd=f=c.cmd"  # no fit


def _two_shots(tmp_path):
    """2 s of test pattern (the 'speaker') then 2 s of solid red (the 'slide')."""
    v = tmp_path / "two_shots.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:d=2",
                    "-f", "lavfi", "-i", "color=c=red:size=1280x720:rate=25:d=2", "-f", "lavfi",
                    "-i", "sine=f=300:d=4", "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]", "-map", "[v]",
                    "-map", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)],
                   check=True)
    return v


class FaceUnlessRed:
    def detect(self, img):
        b, g, r = img.reshape(-1, 3).mean(axis=0)
        return [] if r > 150 and g < 60 else [(0.8, 0.03, 0.99)]


@needs_ffmpeg
def test_plan_reframe_fits_only_the_shot_without_a_face(tmp_path):
    plan = plan_reframe(_two_shots(tmp_path), 0, 4, FaceUnlessRed(), mode="face")
    assert plan.mode == "face" and len(plan.fit) == 1
    a, b = plan.fit[0]
    assert abs(a - 2.0) < 0.25 and b == 4.0


@needs_ffmpeg
def test_render_shows_the_slide_whole_over_a_blur(tmp_path, segs, monkeypatch):
    import numpy as np

    from app.pipeline import reframe
    from app.pipeline.render import render_clip

    monkeypatch.setattr(settings, "reframe_mode", "face")
    monkeypatch.setattr(reframe, "_detector", FaceUnlessRed())
    clip = Clip(index=1, start=0.0, end=4.0, title="t", hook="h", score=7)
    from app.models import RenderOptions

    clip.options = RenderOptions(jumpcut=False, caption_style="none", hook=False)
    render_clip(_two_shots(tmp_path), clip, segs, tmp_path)
    assert clip.reframe == "face" and 1.5 < clip.fit_seconds <= 2.0

    def frame(t):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(tmp_path / "short_1.mp4"),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    face, slide = frame(1.0), frame(3.0)
    assert face[100:300].std() > 30  # cropped test pattern fills the top: detail, not a blur
    red_band, blur_top = slide[700:1200].mean(axis=(0, 1)), slide[50:500].mean(axis=(0, 1))
    assert red_band[0] > 200 and red_band[1] < 40  # the whole slide, full brightness, in the middle
    assert red_band[0] - blur_top[0] > 15 and slide[50:500, :, 0].std() < 12  # darker, smooth blurred fill above it
