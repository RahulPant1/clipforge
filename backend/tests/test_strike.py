"""Text-based cutting: words struck in the transcript (clip.removed) are cut out of the short."""
import json

import pytest

from app.config import settings
from app.models import Clip, JobStatus, RenderOptions, Segment, Word
from app.pipeline.content import clip_text
from app.pipeline.jumpcut import clean_removed, is_struck, plan_cuts

from .test_render import _probe, _video, needs_ffmpeg

W = [Word(start=1.0, end=1.4, word=" I"), Word(start=1.5, end=1.9, word=" really,"),
     Word(start=2.6, end=3.0, word=" basically,"), Word(start=3.1, end=3.5, word=" think"),
     Word(start=3.6, end=4.0, word=" so.")]
SEGS = [Segment(start=1.0, end=4.0, text="I really, basically, think so.", words=W)]
BASICALLY = [(12.6, 13.0)]  # source time: the clip below starts at 10 s


def _segs(offset=10.0):
    return [Segment(start=s.start + offset, end=s.end + offset, text=s.text,
                    words=[Word(start=w.start + offset, end=w.end + offset, word=w.word) for w in s.words])
            for s in SEGS]


def test_clean_removed_sorts_merges_and_clips():
    assert clean_removed([(5, 6), (1, 2), (1.5, 3), (7, 7.005)]) == [(1, 3), (5, 6)]
    assert clean_removed([(0, 2), (9, 12)], start=1, end=10) == [(1, 2), (9, 10)]
    assert clean_removed(None) == []
    assert is_struck(Word(start=1, end=2, word=" x"), [(1.4, 1.6)]) and not is_struck(
        Word(start=1, end=2, word=" x"), [(1.0, 1.4)])  # its middle must be inside


@pytest.mark.parametrize("jumpcut", [True, False])
def test_struck_word_and_its_pause_are_cut_and_not_captioned(jumpcut):
    segs = _segs()
    plain = plan_cuts(segs, 10, 15, enabled=jumpcut)
    plan = plan_cuts(segs, 10, 15, enabled=jumpcut, removed=BASICALLY)
    assert [w.word for w in plan.words] == [" I", " really,", " think", " so."]
    pad = settings.pause_padding
    assert plan.removed > plain.removed + 0.35
    if not jumpcut:  # cut from just after "really," to just before "think": the word and the pause before it
        assert plan.removed == pytest.approx((3.1 - pad) - (1.9 + pad), abs=2 / settings.output_fps)
    think = next(w for w in plan.words if w.word == " think")
    really = next(w for w in plan.words if w.word == " really,")
    assert think.start - really.end == pytest.approx(2 * pad, abs=2 / settings.output_fps)  # joined like a jump cut


def test_striking_the_first_words_starts_the_short_later():
    plan = plan_cuts(_segs(), 10, 15, enabled=False, removed=[(11.0, 11.9)])
    assert plan.keeps[0][0] == pytest.approx(2.6 - settings.pause_padding, abs=1 / settings.output_fps)
    assert plan.words[0].word == " basically," and plan.words[0].start == pytest.approx(settings.pause_padding,
                                                                                        abs=1 / settings.output_fps)


def test_ranges_outside_the_clip_do_nothing():
    assert plan_cuts(_segs(), 10, 15, enabled=False, removed=[(30, 31)]).keeps == [(0.0, 5.0)]


def test_posts_skip_struck_words():
    clip = Clip(start=10, end=15, title="t", hook="h", score=5, removed=BASICALLY)
    assert clip_text(_segs(), clip) == "I really, think so."
    assert clip_text(_segs(), clip.model_copy(update={"removed": []})) == "I really, basically, think so."


@needs_ffmpeg
def test_render_cuts_struck_words_even_with_jumpcuts_off(tmp_path, monkeypatch):
    from app.pipeline.render import render_clip

    monkeypatch.setattr(settings, "reframe_mode", "center")
    words = [Word(start=1.0, end=1.5, word=" hello"), Word(start=2.0, end=5.0, word=" looooong"),
             Word(start=5.5, end=6.0, word=" there")]
    segs = [Segment(start=1.0, end=6.0, text="hello looooong there", words=words)]
    clip = Clip(index=1, start=0.0, end=8.0, title="t", hook="h", score=5, removed=[(2.0, 5.0)],
                options=RenderOptions(jumpcut=False))
    render_clip(_video(tmp_path, 10), clip, segs, tmp_path)
    dur = float(_probe(tmp_path / "short_1.mp4", "format=duration"))
    assert clip.cut_seconds > 3.5 and abs(dur - clip.out_duration) < 0.3
    srt = (tmp_path / "short_1.srt").read_text()
    assert "looooong" not in srt.lower() and "hello" in srt.lower()


def test_api_strike_marks_stale_and_validates(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(pipeline=lambda *a: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    d = store.dir(job.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.json").write_text(json.dumps([s.model_dump() for s in _segs()]))
    store.update(job.id, status=JobStatus.done,
                 clips=[Clip(index=1, start=10, end=15, title="a", hook="h", score=7, file="short_1.mp4")])
    with TestClient(app) as c:
        r = c.patch(f"/api/jobs/{job.id}/clips/1", json={"removed": [[13.0, 12.6], [12.9, 13.2], [12.6, 12.95]]})
        clip = r.json()["clips"][0]
        assert clip["removed"] == [[12.6, 13.2]] and clip["stale"]  # merged; reversed pair dropped
        store.update_clip(job.id, 1, lambda c: setattr(c, "stale", False))
        clip = c.patch(f"/api/jobs/{job.id}/clips/1", json={"title": "b"}).json()["clips"][0]
        assert clip["removed"] == [[12.6, 13.2]] and not clip["stale"]  # other edits leave strikes alone
        clip = c.patch(f"/api/jobs/{job.id}/clips/1", json={"removed": []}).json()["clips"][0]
        assert clip["removed"] == [] and clip["stale"]
