import shutil
import subprocess

import pytest

from app.pipeline.reframe import (
    PATCH_MARGIN,
    ReframePlan,
    Sample,
    active_speaker_path,
    patch_change,
    pick_face,
    plan_reframe,
    smooth,
    speaker_activity,
    split_panels,
    to_keyframes,
    two_speakers,
)

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


# ------------------------------------------------------------------ two speakers

def _pair(t, left=0.25, right=0.75, size=0.03):
    # (cx, area, score, cy, fh)
    return Sample(t=t, cx=left, faces=[(left, size, 0.9, 0.4, 0.2), (right, size * 0.8, 0.9, 0.45, 0.18)])


def test_two_speakers_needs_two_far_apart_faces_most_of_the_time():
    crop = 606 / 1920
    both = [_pair(i / 5) for i in range(10)]
    left, right = two_speakers(both, crop)
    assert (left.cx, right.cx) == (0.25, 0.75) and left.cy == 0.4 and right.fh == 0.18
    solo = [Sample(t=i / 5, cx=0.5, faces=[(0.5, 0.03, 0.9)]) for i in range(10)]
    assert two_speakers(both[:5] + solo, crop) is None  # 50% < 60%
    assert two_speakers(both[:5] + solo, crop, min_share=0.15) is not None
    close = [_pair(i / 5, left=0.45, right=0.55) for i in range(10)]
    assert two_speakers(close, crop) is None  # both fit in one 9:16 crop: follow-face handles it
    tiny = [_pair(i / 5, size=0.03) for i in range(10)]
    for s in tiny:
        s.faces[1] = (0.75, 0.003, 0.9, 0.4, 0.05)  # a face on a poster in the background
    assert two_speakers(tiny, crop) is None


def test_split_panels_frame_each_speaker_without_the_other():
    from app.pipeline.reframe import Speaker

    (w0, h0, x0, y0), (w1, h1, x1, y1) = split_panels(Speaker(0.25, 0.4, 0.2), Speaker(0.75, 0.45, 0.2), 1920, 1080)
    assert (w0, h0) == (w1, h1) and abs(w0 / h0 - 1080 / 960) < 0.01  # same 9:8 panel for both
    assert h0 == 720  # face 0.2*1080 = 216 px = 30% of the crop height
    assert x0 + w0 <= x1  # left crop ends before the right one starts
    assert abs((x0 + w0 / 2) - 480) <= 1 and abs((y0 + 0.4 * h0) - 432) <= 1  # face centred, 40% down
    # faces closer together -> crops shrink to the gap; never leave the frame
    (w, h, x, y), (_, _, x2, _) = split_panels(Speaker(0.35, 0.4, 0.3), Speaker(0.65, 0.4, 0.3), 1920, 1080)
    assert w <= 576 and x + w <= x2 and y >= 0 and y + h <= 1080


def test_split_filter_stacks_two_panels():
    p = ReframePlan("split", 1920, 1080, 606, panels=[(810, 720, 75, 144), (810, 720, 1035, 160)])
    f = p.filters(None)
    assert f == ["split[sp0][sp1];[sp0]crop=810:720:75:144,scale=1080:960,setsar=1[spt];"
                 "[sp1]crop=810:720:1035:160,scale=1080:960,setsar=1[spb];[spt][spb]vstack"]


class TwoFaces:
    def detect(self, img):
        return [(0.25, 0.03, 0.95, 0.4, 0.2), (0.75, 0.03, 0.95, 0.4, 0.2)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_plan_reframe_auto_split_and_face(tmp_path, monkeypatch):
    from app.config import settings

    land = _video(tmp_path, "1280x720")
    monkeypatch.setattr(settings, "reframe_mode", "auto")
    assert plan_reframe(land, 0, 3, detector=TwoFaces()).mode == "split"
    assert plan_reframe(land, 0, 3, detector=FixedFace()).mode == "face"  # one person: follow them
    assert plan_reframe(land, 0, 3, detector=TwoFaces(), mode="face").mode == "face"  # forced single
    assert plan_reframe(land, 0, 3, detector=TwoFaces(), mode="center").mode == "center"



# ------------------------------------------------------------------ active speaker

def _talk_samples(talker_by_time, n=40, fps=5):
    """Two faces; the talker's mouth patch changes every sample, everyone else's stays put."""
    import numpy as np

    rng = np.random.default_rng(0)
    shape = (12 + 2 * PATCH_MARGIN, 24 + 2 * PATCH_MARGIN)  # thumbnail + alignment margin
    still = np.zeros(shape, np.float32)
    out = []
    for i in range(n):
        t = i / fps
        talker = talker_by_time(t)
        patches = []
        for side in (0, 1):
            mouth = rng.normal(0, 40, shape).astype(np.float32) if side == talker else still
            patches.append((mouth, still))
        out.append(Sample(t=t, cx=0.25, faces=[(0.25, 0.03, 0.9, 0.4, 0.2), (0.75, 0.03, 0.9, 0.4, 0.2)],
                          patches=patches))
    return out


def _speakers():
    from app.pipeline.reframe import Speaker

    return Speaker(0.25, 0.4, 0.2), Speaker(0.75, 0.4, 0.2)


def test_speaker_activity_sees_the_moving_mouth():
    import numpy as np

    left, right = _speakers()
    samples = _talk_samples(lambda t: 1)
    acts = speaker_activity(samples, left, right)
    assert acts[0] == (0.0, 0.0)  # nothing to compare with yet
    assert all(r > 30 and lft == 0 for lft, r in acts[1:])
    # head sway / face-box jitter shifts the whole patch: aligned away, not talking
    rng = np.random.default_rng(1)
    face = rng.normal(0, 40, (40, 60)).astype(np.float32)
    for i, smp in enumerate(samples):
        dx, dy = (i % 3) - 1, (i % 2)  # wobble by up to a pixel or two each sample
        win = face[5 + dy:5 + dy + 18, 5 + dx:5 + dx + 30]
        smp.patches[0] = (win, win)
    assert all(lft == 0 for lft, _ in speaker_activity(samples, left, right))


def test_patch_change_ignores_shifts_but_not_shape_changes():
    import numpy as np

    rng = np.random.default_rng(2)
    big = rng.normal(0, 40, (30, 40)).astype(np.float32)
    a, shifted = big[5:23, 5:35], big[7:25, 4:34]  # same content moved 2 px down, 1 px left
    assert patch_change(shifted, a) < 1e-6
    assert patch_change(rng.normal(0, 40, a.shape).astype(np.float32), a) > 20


def test_active_speaker_path_cuts_to_the_talker_and_holds():
    left, right = _speakers()
    samples = _talk_samples(lambda t: 0 if t < 4 else 1)  # left talks 0-4 s, then right
    path = active_speaker_path(samples, speaker_activity(samples, left, right), left, right)
    assert path[0].cx == 0.25 and path[-1].cx == 0.75
    cuts = [p.t for p in path if p.cut]
    assert len(cuts) == 1 and 3.6 <= cuts[0] <= 4.8  # one hard cut, shortly after the turn change


def test_active_speaker_ignores_quick_interjections_and_silence():
    left, right = _speakers()
    # right says "mm-hm" for 0.6 s in the middle of left's 8 s turn -> camera stays on left
    samples = _talk_samples(lambda t: 1 if 3.0 <= t < 3.6 else 0)
    path = active_speaker_path(samples, speaker_activity(samples, left, right), left, right)
    assert {p.cx for p in path} == {0.25} and not any(p.cut for p in path)
    # nobody talks: stay on the first (bigger/left) speaker, no cuts
    quiet = _talk_samples(lambda t: None)
    path = active_speaker_path(quiet, speaker_activity(quiet, left, right), left, right)
    assert {p.cx for p in path} == {0.25} and not any(p.cut for p in path)


def _talking_video(tmp_path):
    """1280x720 grey; a noise "mouth" flickers under the left face for 0-3 s, then under the right one."""
    v = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=gray:s=1280x720:r=25:d=6",
                    "-f", "lavfi", "-i", "color=gray:s=200x100:r=25:d=6",
                    "-filter_complex", "[1:v]noise=alls=100:allf=t+u,split[n1][n2];"
                    "[0:v][n1]overlay=220:300:enable='lt(t,3)'[a];[a][n2]overlay=860:300:enable='gte(t,3)'",
                    "-c:v", "libx264", "-qp", "0", str(v)], check=True)
    return v


class TwoTalkers:
    def detect(self, img):
        # area 0.05 with face height 0.3 -> mouth box ~x 0.21-0.29, y 0.445-0.535 (on the noise patch)
        return [(0.25, 0.05, 0.95, 0.4, 0.3), (0.75, 0.05, 0.95, 0.4, 0.3)]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_plan_reframe_speaker_mode_follows_the_talker(tmp_path, monkeypatch):
    from app.config import settings

    video = _talking_video(tmp_path)
    plan = plan_reframe(video, 0, 6, detector=TwoTalkers(), mode="speaker")
    assert plan.mode == "speaker" and plan.crop_w == 404
    xs = dict(plan.keyframes)
    assert plan.keyframes[0][1] == 118  # 0.25*1280 - 404/2: on the left speaker
    switch = [t for t, x in plan.keyframes if x == 758]  # 0.75*1280 - 404/2
    assert switch and 2.8 <= switch[0] <= 4.2, xs
    assert plan.filters("c.cmd")[1].startswith("crop@track=404:720:118:0")
    # auto keeps stacking unless TWO_SPEAKER_LAYOUT=speaker
    monkeypatch.setattr(settings, "reframe_mode", "auto")
    assert plan_reframe(video, 0, 6, detector=TwoTalkers()).mode == "split"
    monkeypatch.setattr(settings, "two_speaker_layout", "speaker")
    assert plan_reframe(video, 0, 6, detector=TwoTalkers()).mode == "speaker"
