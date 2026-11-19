"""Review fixes: pauses Whisper hides, Instagram's 5-hashtag cap, emoji that libass can't draw, honest trace labels."""
import shutil
import subprocess

import numpy as np
import pytest

from app.config import settings
from app.models import Clip, Segment, Word
from app.pipeline import jumpcut as jc
from app.pipeline.captions import build_ass, build_hook_ass, hook_text
from app.pipeline.content import IG_MAX_HASHTAGS, assemble_instagram
from app.pipeline.highlights import select_highlights

from .conftest import FakeLLM
from .test_content import _ctx

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
RATE = 16000


def _speech(*parts, noise_db=-60.0, rng=np.random.default_rng(0)):
    """Concatenate (seconds, dBFS) parts of noise-like 'speech' over a constant room-noise floor."""
    out = []
    for secs, db in parts:
        n = int(secs * RATE)
        level = 10 ** (db / 20) if db is not None else 0.0
        out.append(rng.standard_normal(n) * level + rng.standard_normal(n) * 10 ** (noise_db / 20))
    return np.concatenate(out).astype(np.float32)


def test_find_silences_is_relative_to_the_clip():
    min_len = settings.max_pause + 2 * settings.pause_padding
    pcm = _speech((2, -20), (1.0, None), (2, -20), (0.3, None), (2, -20))
    sil = jc.find_silences(pcm, RATE, min_len)
    assert len(sil) == 1 and sil[0][0] == pytest.approx(2.0, abs=0.03) and sil[0][1] == pytest.approx(3.0, abs=0.03)
    # hum 28 dB under the voice: still a pause (threshold follows the speech level, not a fixed -50 dB)
    noisy = _speech((2, -12), (1.0, None), (2, -12), noise_db=-40)
    assert len(jc.find_silences(noisy, RATE, min_len)) == 1
    assert jc.find_silences(_speech((2, -20), (1.0, None), (2, -20), noise_db=-40), RATE, min_len) == []  # 20 dB SNR
    soft = _speech((2, -12), (1.0, -34), (2, -12))  # quiet words in a loud clip are not silence
    assert jc.find_silences(soft, RATE, min_len) == []


def _smeared_segs():
    """Whisper ran 'world' over a 1 s pause: no gap between word timings at all."""
    words = [Word(start=0.0, end=0.9, word=" hello"), Word(start=0.9, end=3.0, word=" world"),
             Word(start=3.0, end=4.0, word=" again")]
    return [Segment(start=0, end=4, text="hello world again", words=words)]


def test_plan_cuts_removes_a_pause_the_word_timings_hid():
    segs = _smeared_segs()
    assert jc.plan_cuts(segs, 0, 4).removed == 0  # words alone: nothing to cut
    plan = jc.plan_cuts(segs, 0, 4, silences=[(1.5, 2.6)])
    pad = settings.pause_padding
    assert plan.removed == pytest.approx(1.1 - 2 * pad, abs=1 / settings.output_fps)
    assert len(plan.keeps) == 2 and plan.keeps[0][1] == pytest.approx(1.5 + pad, abs=0.04)
    assert plan.words[-1].end == pytest.approx(plan.duration, abs=0.05)  # captions still end with the audio
    short = jc.plan_cuts(segs, 0, 4, silences=[(1.5, 1.9)])  # under MAX_PAUSE once padded: kept
    assert short.removed == 0
    assert jc.plan_cuts(segs, 0, 4, enabled=False, silences=[(1.5, 2.6)]).removed == 0


@needs_ffmpeg
def test_audio_silences_reads_the_clip_from_the_video(tmp_path):
    v = tmp_path / "talk.mp4"
    vol = "volume='if(between(t,3,4.5),0,1)':eval=frame"  # silent 3-4.5 s
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x36:r=10:d=8",
                    "-f", "lavfi", "-i", "anoisesrc=d=8:a=0.3", "-af", vol, "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", str(v)], check=True)
    (s, e), = jc.audio_silences(v, 2.0, 7.0)  # clip-relative
    assert s == pytest.approx(1.0, abs=0.06) and e == pytest.approx(2.5, abs=0.06)
    assert jc.audio_silences(tmp_path / "missing.mp4", 0, 5) == []


def test_instagram_keeps_at_most_five_hashtags_including_inline_ones():
    raw = {"caption_hook": "Robots read first #ATS", "caption_body": "Tailor it.", "cta": "Save this",
           "hashtags": ["resume", "ats", "jobs", "career", "hiring", "tips", "sql", "work"]}
    post = assemble_instagram(raw, "cheat_sheet", _ctx())
    assert len(post.hashtags) == IG_MAX_HASHTAGS - 1 == 4 and "#ats" not in [t.lower() for t in post.hashtags]
    import re
    assert len(re.findall(r"#\w+", post.text)) == IG_MAX_HASHTAGS


def test_emoji_never_reach_libass():
    assert hook_text("Robots read it first 🤖🔥") == "Robots read it first"
    assert build_hook_ass("🤖🔥", 30) == ""  # nothing left to show: no empty card
    ass = build_ass([Word(start=0, end=0.5, word=" Fire🔥"), Word(start=0.5, end=1, word=" café")], [])
    assert "🔥" not in ass and "FIRE" in ass and "CAFÉ" in ass


def test_trace_says_ranked_out_not_overlap_when_nothing_overlapped(segs):
    picks = [{"start": 0.0, "end": 31.8, "title": "a", "hook": "h", "score": 9, "keywords": []},
             {"start": 64.0, "end": 95.8, "title": "b", "hook": "h", "score": 5, "keywords": []},
             {"start": 8.0, "end": 39.8, "title": "c", "hook": "h", "score": 4, "keywords": []}]
    trace: dict = {}
    clips = select_highlights(segs, 1, FakeLLM(picks), trace=trace)
    assert [c.title for c in clips] == ["a"]
    assert [i["outcome"] for i in trace["items"]] == ["kept", "ranked_out", "overlap"]
    assert isinstance(clips[0], Clip)
