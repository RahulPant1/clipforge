import array
import shutil
import subprocess

import pytest

from app.config import settings
from app.models import Segment, Word
from app.pipeline.jumpcut import audio_cut_graph, is_filler, plan_cuts, snap_to_frames, video_select_expr


def _segs(words):
    return [Segment(start=words[0].start, end=words[-1].end, text="", words=words)]


W = [
    Word(start=10.0, end=10.4, word=" So"),
    Word(start=10.5, end=10.9, word=" um,"),       # filler -> removed
    Word(start=11.0, end=11.4, word=" money"),
    Word(start=14.0, end=14.5, word=" grows"),     # 2.6 s pause before -> cut
    Word(start=14.55, end=15.0, word=" fast."),
]


def test_is_filler():
    assert is_filler(" Um,") and is_filler("uh") and not is_filler("umbrella")


def test_plan_removes_filler_and_long_pause():
    plan = plan_cuts(_segs(W), 10.0, 15.2, enabled=True)
    assert len(plan.keeps) >= 2
    assert plan.removed > 2.5
    texts = [w.word.strip() for w in plan.words]
    assert "um," not in texts and texts[0] == "So" and texts[-1] == "fast."
    # remapped words are monotonic and inside the output duration
    starts = [w.start for w in plan.words]
    assert starts == sorted(starts) and plan.words[-1].end <= plan.duration + 1e-6


def test_short_pauses_are_kept():
    words = [Word(start=0.0, end=0.4, word=" a"), Word(start=0.6, end=1.0, word=" b")]
    plan = plan_cuts(_segs(words), 0.0, 1.0, enabled=True)
    assert len(plan.keeps) == 1


def test_disabled_keeps_everything():
    plan = plan_cuts(_segs(W), 10.0, 15.2, enabled=False)
    assert plan.keeps == [(0.0, 5.2)] and plan.removed == 0
    assert len(plan.words) == len(W)


def test_remap_snaps_gap_to_next_keep():
    plan = plan_cuts(_segs(W), 10.0, 15.2, enabled=True)
    s0, e0 = plan.keeps[0]
    assert plan.remap(e0 + 0.5) == round(e0 - s0, 3) or plan.remap(e0 + 0.5) >= e0 - s0


def test_cuts_land_on_the_frame_grid():
    plan = plan_cuts(_segs(W), 10.0, 15.2, enabled=True)
    for s, e in plan.keeps:  # every cut is a whole number of output frames
        assert abs(s * 30 - round(s * 30)) < 1e-9 and abs(e * 30 - round(e * 30)) < 1e-9


def test_snap_to_frames_merges_drops_and_clamps():
    assert snap_to_frames([(0.01, 1.0), (1.01, 2.0)], 30, 5) == [(0.0, 2.0)]  # rounding made them touch
    assert snap_to_frames([(1.0, 1.01)], 30, 5) == []  # shorter than a frame
    assert snap_to_frames([(4.5, 9.0)], 30, 5.0) == [(4.5, 5.0)]  # never past the clip end


def test_video_select_counts_exact_frames():
    expr = video_select_expr([(0.0, 1.5), (2.0, 3.0)], 30)
    assert expr == "between(t,-0.016667,1.483333)+between(t,1.983333,2.983333)"
    # frames at n/30: 0..44 (45 frames = 1.5 s) and 60..89 (30 frames = 1.0 s) — no off-by-one either side
    kept = [n for n in range(200) if any(lo <= n / 30 <= hi for lo, hi in
                                         [(-1 / 60, 1.5 - 1 / 60), (2 - 1 / 60, 3 - 1 / 60)])]
    assert len(kept) == 75 and kept[0] == 0 and kept[44] == 44 and kept[45] == 60


def test_audio_cut_graph_avoids_aselect():
    # aselect is a no-op in FFmpeg 8.0/8.0.1 — audio must be cut with atrim
    one = audio_cut_graph([(1.0, 2.5)])
    assert one == "atrim=start=1.000000:end=2.500000,asetpts=PTS-STARTPTS"
    two = audio_cut_graph([(0.0, 1.5), (2.0, 3.0)])
    assert "aselect" not in two and two.startswith("asplit=2[a0][a1];")
    assert two.endswith("[b0][b1]concat=n=2:v=0:a=1") and "atrim=start=2.000000:end=3.000000" in two


def test_crossfade_graph_borrows_from_the_removed_gap():
    g = audio_cut_graph([(0.0, 1.5), (2.0, 3.0), (3.5, 4.0)], crossfade=0.01)
    assert "aselect" not in g and "concat" not in g
    # every segment but the last runs 10 ms into the gap after it; the overlap gives that time back
    assert "atrim=start=0.000000:end=1.510000" in g and "atrim=start=2.000000:end=3.010000" in g
    assert "atrim=start=3.500000:end=4.000000" in g
    assert g.endswith("[b0][b1]acrossfade=d=0.010000[c1];[c1][b2]acrossfade=d=0.010000")


def test_crossfade_clamps_to_gap_and_falls_back_to_concat():
    g = audio_cut_graph([(0.0, 1.0), (1.004, 2.0), (2.0, 3.0)], crossfade=0.01)
    assert "atrim=start=0.000000:end=1.004000" in g and "acrossfade=d=0.004000" in g
    assert g.endswith("[c1][b2]concat=n=2:v=0:a=1")  # no gap at all -> plain join
    assert audio_cut_graph([(1.0, 2.5)], crossfade=0.01) == "atrim=start=1.000000:end=2.500000,asetpts=PTS-STARTPTS"


def _cut_sine(keeps, crossfade) -> array.array:
    """Cut a 440 Hz sine with the real graph; return mono s16 samples at 48 kHz."""
    src = "sine=frequency=440:sample_rate=48000:duration=4"
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", src,
                          "-af", audio_cut_graph(keeps, crossfade), "-f", "s16le", "-ac", "1", "-"],
                         capture_output=True, check=True).stdout
    return array.array("h", out)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_crossfade_removes_clicks_and_keeps_length():
    # cut points at odd phases of the sine, so a hard cut jumps between unrelated sample values
    keeps = [(0.0, 1.0013), (2.0071, 3.0), (3.4049, 4.0)]
    expected = round(sum(e - s for s, e in keeps) * 48000)
    hard, soft = _cut_sine(keeps, 0.0), _cut_sine(keeps, 0.01)
    assert abs(len(hard) - expected) <= 3 and abs(len(soft) - expected) <= 3  # same length -> A/V sync kept

    def max_step(x):
        return max(abs(x[i + 1] - x[i]) for i in range(len(x) - 1))

    # an uncut sine moves ~240 per sample at lavfi's level; a hard cut jumps ~18x that (a click)
    smooth = max_step(_cut_sine([(0.0, 4.0)], 0.0))
    assert max_step(hard) > 3 * smooth
    assert max_step(soft) < 1.5 * smooth


def test_settings_default_on():
    assert settings.jumpcut_enabled is True and settings.max_pause < 1
