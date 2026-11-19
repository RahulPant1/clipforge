from app.config import settings
from app.models import Clip
from app.pipeline.highlights import build_prompt, dedupe, fit_duration, select_highlights, snap, windows

from .conftest import FakeLLM


def test_windows_split(segs):
    wins = list(windows(segs, 60))
    assert len(wins) > 1
    assert sum(len(w) for w in wins) == len(segs)


def test_snap_aligns_to_segment_bounds(segs):
    s, e = snap(9.3, 41.0, segs)
    assert s == 8.0
    assert e in [x.end for x in segs]


def test_fit_duration_trims_long_and_rejects_short(segs):
    trimmed = fit_duration(0.0, 200.0, segs)
    assert trimmed and trimmed[1] - trimmed[0] <= settings.max_clip_seconds
    assert fit_duration(0.0, 5.0, segs) is None


def test_dedupe_removes_overlap_and_ranks():
    clips = [
        Clip(start=0, end=30, title="a", hook="h", score=5),
        Clip(start=10, end=40, title="b", hook="h", score=9),
        Clip(start=100, end=130, title="c", hook="h", score=7),
    ]
    kept = dedupe(clips, 5)
    assert [c.title for c in kept] == ["b", "c"]
    assert [c.index for c in kept] == [1, 2]


def test_prompt_contains_timestamps(segs):
    p = build_prompt(segs[:3], 2)
    assert "[0.0-7.8]" in p and "2 best" in p


def test_select_highlights_with_fake_llm(segs):
    llm = FakeLLM([
        {"start": 0.4, "end": 39.5, "title": "Good one", "hook": "Wait for it", "score": 8},
        {"start": 50, "end": 52, "title": "Too short", "hook": "x", "score": 9},
        {"start": "bad", "end": 1, "title": "Broken", "hook": "x", "score": 3},
    ])
    clips = select_highlights(segs, 3, llm)
    assert len(clips) == 1
    assert clips[0].title == "Good one"
    assert clips[0].start == 0.0


def test_all_windows_failing_raises_clear_error(segs):
    import pytest

    class DownLLM(FakeLLM):
        def chat_json(self, prompt, schema, **kw):
            raise ConnectionError("connection refused")

    with pytest.raises(RuntimeError, match="Ollama running"):
        select_highlights(segs, 3, DownLLM([]))


# ---------------------------------------------------------------- enough candidates for what you asked

def test_clips_per_window_scales_with_request(monkeypatch):
    from app.pipeline.highlights import MAX_CLIPS_PER_WINDOW, clips_per_window

    monkeypatch.setattr(settings, "clips_per_window", 2)
    assert clips_per_window(6, 2) == 4  # 3 each + 1 spare
    assert clips_per_window(3, 1) == 4
    assert clips_per_window(6, 6) == 2  # long video: the configured minimum still applies
    assert clips_per_window(40, 1) == MAX_CLIPS_PER_WINDOW
    assert clips_per_window(5, 0) == 2


class CountingLLM:
    """Fake Gemma that returns exactly as many non-overlapping 24 s clips as the prompt asks for."""

    def __init__(self):
        self.asked: list[int] = []

    def chat_json(self, prompt, schema, temperature=None, think=None):
        import re

        n = int(re.search(r"pick the (\d+) best", prompt).group(1))
        self.asked.append(n)
        first = float(re.search(r"\[(\d+\.\d)-", prompt).group(1))
        return {"clips": [{"start": first + k * 32, "end": first + k * 32 + 23.8, "title": f"t{first}_{k}",
                           "hook": "h", "score": 9 - k, "keywords": []} for k in range(n)]}

    def unload(self):
        pass


def test_review_mode_gets_two_times_the_requested_candidates(monkeypatch):
    # Real run on a 13.7 min video: asked for 3 shorts, review mode should offer 6, but got 4
    # because every chunk was only ever asked for 2 clips.
    from .conftest import make_segs

    monkeypatch.setattr(settings, "window_seconds", 200)
    monkeypatch.setattr(settings, "clips_per_window", 2)
    segs = make_segs(n=50)  # 400 s → 2 chunks
    llm = CountingLLM()

    clips = select_highlights(segs, 3, llm, keep=6)

    assert llm.asked == [4, 4]
    assert len(clips) == 6
    assert sum(c.approved for c in clips) == 3
    assert all(c.end - c.start <= settings.max_clip_seconds for c in clips)


def test_windows_respect_char_budget(segs):
    from app.pipeline.highlights import _line

    wins = list(windows(segs, 10_000, max_chars=200))  # time limit never hits; chars do
    assert len(wins) > 1 and sum(len(w) for w in wins) == len(segs)
    assert all(sum(len(_line(s)) + 1 for s in w) <= 200 for w in wins)


def test_prompt_leaves_half_the_context_for_thinking(monkeypatch):
    # Real transcript: a 10-min window was 6.6k prompt tokens of an 8k context; asking for 4 clips with thinking on
    # ran out of context. Chunks are now capped so the prompt stays within ~half of num_ctx.
    from app.models import Segment
    from app.pipeline.highlights import CHARS_PER_TOKEN, transcript_char_budget

    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    dense = [Segment(start=i * 2.4, end=i * 2.4 + 2.3, text="so the hiring manager really wants to see this")
             for i in range(250)]  # ~10 min of fast speech in short segments, like the real transcript
    for w in windows(dense, settings.window_seconds, transcript_char_budget()):
        assert len(build_prompt(w, 4)) / CHARS_PER_TOKEN <= 8192 * 0.5 + 50
