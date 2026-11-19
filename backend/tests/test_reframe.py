import shutil
import subprocess

import pytest

from app.pipeline.reframe import ReframePlan, Sample, pick_face, plan_reframe, smooth, to_keyframes

cv2 = pytest.importorskip("cv2")


def S(t, cx, cut=False):
    return Sample(t, cx, cut)


def test_smooth_holds_inside_deadzone():
    out = smooth([S(0, 0.5), S(0.2, 0.52), S(0.4, 0.48), S(0.6, 0.53)], deadzone=0.05, alpha=0.5)
    assert all(s.cx == 0.5 for s in out)


def test_smooth_eases_toward_big_move():
    out = smooth([S(i * 0.2, 0.3 if i == 0 else 0.7) for i in range(8)], deadzone=0.05, alpha=0.4)
    xs = [s.cx for s in out]
    assert xs[0] == 0.3 and xs == sorted(xs) and 0.6 < xs[-1] <= 0.7  # gradual pan, no overshoot


def test_smooth_snaps_on_cut_and_fills_gaps():
    out = smooth([S(0, None), S(0.2, 0.3), S(0.4, None), S(0.6, 0.8, cut=True), S(0.8, 0.8)],
                 deadzone=0.05, alpha=0.3)
    assert out[0].cx == 0.3 and out[2].cx == 0.3  # leading gap + hole filled
    assert out[3].cx == 0.8  # snapped on camera cut, no slow pan


def test_smooth_no_faces():
    assert smooth([S(0, None), S(1, None)]) == []


def test_median_kills_single_frame_glitch():
    out = smooth([S(0, 0.5), S(0.2, 0.95), S(0.4, 0.5), S(0.6, 0.5)], deadzone=0.05, alpha=1.0)
    assert all(abs(s.cx - 0.5) < 1e-9 for s in out)


def test_keyframes_clamped_and_no_interp_across_cut():
    path = [S(0, 0.0), S(1.0, 0.0), S(1.0001, 1.0, cut=True)]
    keys = to_keyframes(path, src_w=1920, crop_w=606, duration=2.0, fps=10)
    xs = dict(keys)
    assert keys[0] == (0.0, 0)  # clamped at left edge
    assert max(x for _, x in keys) == 1920 - 606  # clamped at right edge
    assert len(keys) == 2  # jumps once (cut), nothing in between
    assert xs[keys[1][0]] == 1314


def test_pick_face_is_sticky():
    a, b = (0.3, 0.020, 0.9), (0.7, 0.025, 0.9)
    assert pick_face([a, b], None) == 0.7  # biggest first
    assert pick_face([a, b], prev=0.3) == 0.3  # stays on current speaker (similar size)
    assert pick_face([(0.3, 0.005, 0.9), b], prev=0.3) == 0.7  # switches if other is much bigger
    assert pick_face([], 0.5) is None


def test_plan_filters_and_sendcmd():
    p = ReframePlan("face", 1920, 1080, 606, [(0.0, 100), (0.5, 120)])
    assert p.filters("c.cmd") == ["sendcmd=f=c.cmd", "crop@track=606:1080:100:0"]
    assert p.sendcmd() == "0.000 crop@track x 100;\n0.500 crop@track x 120;\n"
    assert "crop='min(iw,ih*9/16)':ih" in ReframePlan("center", 1920, 1080, 606).filters(None)[0]


class NoFaces:
    def detect(self, img):
        return []


class FixedFace:
    def detect(self, img):
        return [(0.8, 0.03, 0.99)]


def _video(tmp_path, size):
    v = tmp_path / f"v_{size}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=25:duration=4", "-c:v", "libx264", str(v)], check=True)
    return v


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_plan_reframe_modes(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "reframe_mode", "face")
    land = _video(tmp_path, "1280x720")
    assert plan_reframe(land, 0, 3, detector=NoFaces()).mode == "center"
    face = plan_reframe(land, 0, 3, detector=FixedFace())
    assert face.mode == "face" and face.crop_w == 404
    assert face.keyframes[0][1] == 822  # 0.8*1280 - 404/2
    assert plan_reframe(_video(tmp_path, "720x1280"), 0, 3, detector=FixedFace()).mode == "none"
