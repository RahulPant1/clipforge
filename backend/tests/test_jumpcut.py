from app.config import settings
from app.models import Segment, Word
from app.pipeline.jumpcut import is_filler, plan_cuts, select_expr


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


def test_select_expr():
    assert select_expr([(0, 1.5), (2, 3)]) == "between(t,0.000,1.500)+between(t,2.000,3.000)"


def test_settings_default_on():
    assert settings.jumpcut_enabled is True and settings.max_pause < 1
