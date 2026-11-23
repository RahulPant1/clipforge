"""Inserts (stat_counter): spoken numbers → timed, placed cards → PNG frames → overlaid in the render."""
import shutil
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.models import Clip, Insert, RenderOptions, Segment, Word
from app.pipeline import insert_render, inserts
from app.pipeline.jumpcut import CutPlan
from app.pipeline.numbers import find, fmt

from .test_api import _fake_steps, _wait_status

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    return [Word(start=start + i * step, end=start + i * step + step * 0.8, word=f" {t}")
            for i, t in enumerate(text.split())]


def seg(text: str, start: float = 0.0, step: float = 0.4) -> Segment:
    ws = words(text, start, step)
    return Segment(start=ws[0].start, end=ws[-1].end, text=text, words=ws)


def shown(text: str) -> list[str]:
    return [n.text() for n in find(words(text))]


# ------------------------------------------------------------------ numbers.py

@pytest.mark.parametrize("said, card", [
    ("40% of startups fail.", "40%"),
    ("fifty percent of people", "50%"),
    ("twenty-five per cent", "25%"),
    ("it costs ₹2.5 crore now", "₹2.5 crore"),
    ("Rs 2,50,000 a month", "₹2,50,000"),  # Indian grouping kept
    ("a 1.5x jump", "1.5x"),
    ("ten thousand subscribers", "10,000"),
    ("two hundred and fifty users", "250"),
    ("one point five million dollars", "$1.5M"),
    ("$5M raised", "$5M"),
    ("10k followers", "10K"),
    ("das hazaar rupaye", "₹10,000"),
    ("pachaas lakh log", "50 lakh"),
    ("५० लोग", "50"),
    ("15000 rupees.", "₹15,000"),
])
def test_numbers_are_read_as_said(said, card):
    assert shown(said) == [card]


@pytest.mark.parametrize("said", ["in 2019 we started", "one thing", "3 things", "ek baar", "3 to 4 hours",
                                  "24/7 support", "at 5:30 pm", "the 1st time", "das se bees"])
def test_not_stats(said):
    assert shown(said) == []


def test_number_spans_several_words_and_keeps_spoken_text():
    (n,) = find(words("we hit two hundred and fifty thousand users"))
    assert (n.start, n.end, n.value, n.text(), n.spoken) == (2, 7, 250000, "250,000", "two hundred and fifty thousand")


def test_count_up_frames_keep_the_format():
    assert fmt(123456, 0, "indian") == "1,23,456" and fmt(1234567) == "1,234,567" and fmt(2.04, 1) == "2.0"
    (n,) = find(words("₹2.5 crore"))
    assert n.text(1.23) == "₹1.2 crore"


# ------------------------------------------------------------------ candidates / labels

def test_candidates_best_first_struck_excluded_with_rule_label():
    segs = [seg("We grew 3 things. And 40% of startups fail, sadly. Then 12 people came.")]
    clip = Clip(index=1, start=0, end=10, title="t", hook="h", score=7)
    cands = inserts.candidates(segs, clip)
    assert [c.num.text() for c in cands] == ["40%", "12"]  # unit first; "3 things" isn't a stat
    assert cands[0].sentence == "And 40% of startups fail, sadly."
    assert inserts.rule_label(cands[0]) == "of startups fail"
    ins = inserts.from_candidate(cands[0])
    assert (ins.value, ins.suffix, ins.spoken, ins.approved) == (40, "%", "40%", False)
    assert ins.t == pytest.approx(cands[0].t)

    w40 = next(w for w in segs[0].words if "40" in w.word)
    clip.removed = [(w40.start, w40.end)]
    assert [c.num.text() for c in inserts.candidates(segs, clip)] == ["12"]


# ------------------------------------------------------------------ timing / schedule

def _clip(*ins: Insert, start=0.0, end=20.0, removed=()) -> Clip:
    return Clip(index=1, start=start, end=end, title="t", hook="h", score=7, inserts=list(ins), removed=list(removed))


def test_timing_is_output_time_and_holds_to_the_sentence_end():
    ws = words("so we found that 40% of users never come back. next thing", start=10.0, step=0.5)
    t40 = ws[4].start  # 12.0
    clip = _clip(Insert(t=t40, value=40, suffix="%", approved=True), start=10, end=20)
    plan = CutPlan(keeps=[(0.0, 1.0), (1.5, 10.0)], words=[], source_duration=10.0)  # 0.5 s cut before it
    got = inserts.timing(clip.inserts[0], clip, plan, ws)
    assert got.start == pytest.approx(2.0 - 0.5 - inserts.LEAD)
    back = ws[9]  # "back." ends the sentence
    assert got.end == pytest.approx(plan.remap(back.end - 10.0))
    assert settings.insert_min_seconds <= got.end - got.start <= settings.insert_max_seconds


def test_timing_drops_trimmed_out_and_struck_numbers_custom_kept():
    ws = words("we made 40% more", start=0.0)
    plan = CutPlan(keeps=[(0.0, 10.0)], words=[], source_duration=10.0)
    ins = Insert(t=ws[2].start, value=40, suffix="%", approved=True)
    assert inserts.timing(ins, _clip(ins, start=1.0, end=10), plan, ws) is None  # trimmed out
    struck = _clip(ins, removed=[(ws[2].start, ws[2].end)])
    assert inserts.timing(ins, struck, plan, ws) is None
    mine = ins.model_copy(update={"custom": True})
    assert inserts.timing(mine, struck, plan, ws) is not None  # yours: where you put it
    late = Insert(t=9.5, value=3, custom=True, approved=True)  # 0.5 s before the end: too short to read
    assert inserts.timing(late, _clip(late), plan, ws) is None


def test_schedule_only_approved_spaced_and_capped(monkeypatch):
    monkeypatch.setattr(settings, "inserts_max", 2)
    segs = [seg(" ".join(["w"] * 60), step=0.5)]
    plan = CutPlan(keeps=[(0.0, 30.0)], words=[], source_duration=30.0)
    mk = lambda t, ok=True: Insert(t=t, value=10, custom=True, approved=ok)  # noqa: E731
    clip = _clip(mk(1.0), mk(2.0), mk(3.0, ok=False), mk(9.0), mk(15.0), end=30)
    got = inserts.schedule(clip, plan, segs)
    assert [round(t.insert.t) for t in got] == [1, 9]  # 2.0 too close, 3.0 not approved, 15 over the cap


# ------------------------------------------------------------------ placement

@pytest.mark.parametrize("size", [(1080, 1920), (1080, 1350), (1080, 1080)])
def test_card_sits_between_face_and_captions(size):
    w, h = size
    bw, bh = inserts.box_size(h)
    ins = Insert(t=0, value=1)
    x, y = inserts.placement(ins, size, split=False, caption_position="bottom", captions=True)
    caption_band = h - round(settings.caption_margin_v * h / 1920)
    assert x == (w - bw) // 2 and y >= h * 0.4 and y + bh <= caption_band - 100
    xm, ym = inserts.placement(ins, size, split=False, caption_position="middle", captions=True)
    assert ym > h / 2 + 50 and ym + bh <= caption_band  # under centred captions, above the app UI
    _, ys = inserts.placement(ins, size, split=True, caption_position="bottom", captions=True)
    assert ys == round(settings.hook_margin_v * h / 1920)  # stacked faces: top
    up = ins.model_copy(update={"position": "upper"})
    assert inserts.placement(up, size, split=False, caption_position=None, captions=True)[1] == ys


def test_graph_shifts_frames_to_the_card_start():
    g, out = inserts.graph("b", [inserts.Layer(3, 120, 930, 2.0, 4.5)])
    assert g == ("[3:v]format=rgba,setpts=PTS-STARTPTS+2.000/TB[ins0];[b][ins0]overlay=120:930:eof_action=pass:"
                 "enable='between(t,2.000,4.499)'[insv0]")
    assert out == "insv0"


# ------------------------------------------------------------------ insert_render.py

def _spec(**kw):
    ins = Insert(t=0, value=40, suffix="%", label=kw.pop("label", "of startups fail"))
    return insert_render.make_spec(ins, kw.pop("duration", 2.0), (840, 340), "#FFD60A", "Arial")


def test_pillow_frames_count_up_pop_in_and_fade(tmp_path):
    fr = insert_render.frames(_spec(), tmp_path)
    assert fr.count == 2 * settings.output_fps
    files = sorted(fr.folder.glob("*.png"))
    assert len(files) == fr.count
    first, mid, last = (np.asarray(Image.open(f)) for f in (files[0], files[len(files) // 2], files[-1]))
    assert first.shape == (340, 840, 4)
    assert first[..., 3].max() < mid[..., 3].max() == 255  # fades in
    assert last[..., 3].max() < 128  # fades out
    assert (mid[..., 3] == 0).any()  # transparent around the card


def test_frames_are_cached_per_spec_and_pruned(tmp_path):
    calls = []

    class Fake:
        name, version, templates = "fake", 1, {"stat_counter"}

        def render(self, spec, out):
            calls.append(spec.label)
            out.mkdir(parents=True)
            Image.new("RGBA", (spec.width, spec.height), (255, 0, 0, 255)).save(out / "0001.png")
            return 1

    a = insert_render.frames(_spec(), tmp_path, Fake())
    assert insert_render.frames(_spec(), tmp_path, Fake()).folder == a.folder and calls == ["of startups fail"]
    b = insert_render.frames(_spec(label="never come back"), tmp_path, Fake())
    assert b.folder != a.folder and len(calls) == 2
    insert_render.prune(tmp_path, {b.folder.name})
    assert not a.folder.exists() and b.folder.exists()


# ------------------------------------------------------------------ render (real ffmpeg)

def _src(tmp_path, d=6):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x303030:s=1080x1920:r=25:d={d}",
                    "-f", "lavfi", "-i", f"sine=f=300:d={d}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a",
                    "aac", "-shortest", str(src)], check=True)
    return src


def _frame(path, t: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)


def _red(f: np.ndarray) -> np.ndarray:
    return (f[..., 0] > 200) & (f[..., 1] < 60) & (f[..., 2] < 60)


class RedBox:
    """Fake renderer: every frame a solid red card (easy to find in the output)."""
    name, version, templates = "redbox", 1, {"stat_counter"}

    def render(self, spec, out):
        out.mkdir(parents=True)
        n = round(spec.duration * spec.fps)
        for k in range(n):
            Image.new("RGBA", (spec.width, spec.height), (255, 0, 0, 255)).save(out / f"{k + 1:04d}.png")
        return n


@needs_ffmpeg
def test_render_overlays_the_card_when_the_number_is_said(tmp_path, monkeypatch):
    from app.pipeline import render

    monkeypatch.setitem(insert_render.RENDERERS, "pillow", RedBox())
    src = _src(tmp_path)
    segs = [seg("hello there friends, we saw that 40% of people quit early. then more words came", step=0.35)]
    t40 = next(w for w in segs[0].words if "40" in w.word).start  # 2.1
    ins = Insert(t=t40, value=40, suffix="%", label="quit early", approved=True)
    clip = Clip(index=1, start=0, end=6, title="t", hook="", score=7, inserts=[ins],
                options=RenderOptions(jumpcut=False, reframe="center", hook=False))
    render.render_clip(src, clip, segs, tmp_path)
    out = tmp_path / "short_1.mp4"

    before, during = _red(_frame(out, t40 - 0.5)), _red(_frame(out, t40 + 0.5))
    assert before.sum() == 0
    ys, xs = np.where(during)
    bw, bh = inserts.box_size(1920)
    x, y = inserts.placement(ins, (1080, 1920), split=False, caption_position=None, captions=True)
    assert abs(xs.min() - x) <= 2 and abs(ys.min() - y) <= 2 and abs(ys.max() - (y + bh - 1)) <= 2
    assert _red(_frame(out, 5.8)).sum() == 0  # gone after its sentence (+ max seconds)
    assert clip.render_error is None
    cached = list((tmp_path / "inserts" / "short_1").iterdir())
    assert len(cached) == 1

    clip.inserts = [ins.model_copy(update={"approved": False})]  # unapproved → renders as before, cache pruned
    render.render_clip(src, clip, segs, tmp_path)
    assert _red(_frame(out, t40 + 0.5)).sum() == 0
    assert not list((tmp_path / "inserts" / "short_1").iterdir())


@needs_ffmpeg
def test_real_pillow_card_renders_in_every_format(tmp_path):
    from app.pipeline import render

    src = _src(tmp_path, d=4)
    segs = [seg("we raised ₹2.5 crore in seed funding last year.", step=0.4)]
    ins = inserts.from_candidate(inserts.candidates(segs, Clip(index=1, start=0, end=4, title="t", hook="",
                                                               score=7))[0], approved=True)
    assert ins.label == "in seed funding last"
    clip = Clip(index=1, start=0, end=4, title="t", hook="", score=7, inserts=[ins],
                options=RenderOptions(jumpcut=False, reframe="center", hook=False, aspects=["4:5", "1:1"]))
    render.render_clip(src, clip, segs, tmp_path)
    assert clip.render_error is None and clip.formats == ["4:5", "1:1"]
    f = _frame(tmp_path / "short_1.mp4", ins.t + 1.0)
    yellow = (f[..., 0] > 200) & (f[..., 1] > 170) & (f[..., 2] < 80)  # the accent number
    assert yellow.sum() > 2000
    assert len(list((tmp_path / "inserts" / "short_1").iterdir())) == 3  # one card size per format


@needs_ffmpeg
def test_a_failing_card_never_fails_the_short(tmp_path, monkeypatch):
    from app.pipeline import render

    class Broken:
        name, version, templates = "broken", 1, {"stat_counter"}

        def render(self, spec, out):
            raise RuntimeError("no GPU today")

    monkeypatch.setitem(insert_render.RENDERERS, "pillow", Broken())
    src = _src(tmp_path, d=3)
    clip = Clip(index=1, start=0, end=3, title="t", hook="", score=7,
                inserts=[Insert(t=0.5, value=40, suffix="%", custom=True, approved=True)],
                options=RenderOptions(jumpcut=False, reframe="center", hook=False))
    assert render.render_clip(src, clip, [], tmp_path) == "short_1.mp4"
    assert (tmp_path / "short_1.mp4").exists() and "no GPU today" in clip.render_error


# ------------------------------------------------------------------ API

def test_editing_approved_inserts_marks_the_short_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    with TestClient(create_app(steps=steps)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1}).json()["id"]
        _wait_status(c, jid, "review")
        c.post(f"/api/jobs/{jid}/produce")
        _wait_status(c, jid, "done")
        ins = {"t": 22.0, "value": 40, "suffix": "%", "label": "quit"}
        r = c.patch(f"/api/jobs/{jid}/clips/1", json={"inserts": [ins]})  # a suggestion: not burned in
        assert r.status_code == 200 and r.json()["clips"][0]["stale"] is False
        saved = r.json()["clips"][0]["inserts"][0]
        assert saved["id"] and saved["approved"] is False
        r = c.patch(f"/api/jobs/{jid}/clips/1", json={"inserts": [{**saved, "approved": True}]})
        assert r.json()["clips"][0]["stale"] is True
        bad = c.patch(f"/api/jobs/{jid}/clips/1", json={"inserts": [{**ins, "value": "nan"}]})
        assert bad.status_code == 422


# ------------------------------------------------------------------ phase 3: Gemma picks + labels

STATS = "So 40% of startups fail in year one. I have 12 cats at home. We raised ₹2 crore last year."


class CardLLM:
    """Fake Gemma for the stat card schema: a fixed answer (or an exception), prompts recorded."""

    def __init__(self, answer=None, error: Exception | None = None):
        self.answer, self.error, self.prompts = answer, error, []

    def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
        assert "cards" in schema["properties"]
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer

    def unload(self):
        pass


def _stat_clip(start=0.0, **kw) -> tuple[Clip, list[Segment]]:
    segs = [seg(STATS, start=start, step=0.5)]
    return Clip(index=1, start=start, end=start + 12, title="t", hook="h", score=7, **kw), segs


def test_gemma_only_picks_by_id_and_labels_come_from_the_sentence():
    clip, segs = _stat_clip()
    llm = CardLLM({"cards": [
        {"id": 1, "worth": 9, "label": "of **startups** fail."},
        {"id": 3, "worth": 8, "label": "raised by our genius founders"},  # invented words → rule label
        {"id": 2, "worth": 3, "label": "cats"},  # not worth a card
        {"id": 7, "worth": 10, "label": "made up"},  # no such number
        {"id": 1, "worth": 10, "label": "dup"},
    ]})
    got = inserts.suggest(clip, segs, llm)
    assert [(inserts.display(i), i.label, i.approved) for i in got] == [
        ("40%", "of startups fail", False), ("₹2 crore", "last year", False)]
    prompt = llm.prompts[0]
    assert '1: 40% - "So 40% of startups fail in year one."' in prompt and "3: ₹2 crore" in prompt
    assert all(i.t == pytest.approx(next(w.start for w in segs[0].words if w.word.strip() == i.spoken.split()[0]))
               for i in got)


def test_label_check():
    clip, segs = _stat_clip()
    c = inserts.candidates(segs, clip)[0]  # 40%
    assert inserts.label_ok("startups fail", c) and inserts.label_ok("Of Startups, fail", c)
    assert not inserts.label_ok("40%", c) and not inserts.label_ok("of founders fail", c)
    assert not inserts.label_ok("", c)


@pytest.mark.parametrize("llm", [CardLLM(error=ConnectionError("ollama down")), CardLLM({"oops": 1}),
                                 CardLLM("not json")])
def test_gemma_failing_falls_back_to_rule_picks(llm):
    clip, segs = _stat_clip()
    got = inserts.suggest(clip, segs, llm)
    assert [inserts.display(i) for i in got] == ["40%", "₹2 crore"]  # units; "12 cats" scores too low
    assert got[0].label == "of startups fail"  # trailing "in" trimmed


def test_no_numbers_no_gemma_call():
    llm = CardLLM({"cards": []})
    clip = Clip(index=1, start=0, end=5, title="t", hook="h", score=7)
    assert inserts.suggest(clip, [seg("nothing to count here at all")], llm) == [] and not llm.prompts


def test_merge_keeps_yours_and_replaces_old_suggestions():
    mine = Insert(t=1.0, value=5, custom=True, approved=True)
    ok = Insert(t=6.0, value=40, approved=True)
    old = Insert(t=9.0, value=12)
    clip = Clip(index=1, start=0, end=12, title="t", hook="h", score=7, inserts=[old, ok, mine])
    new = [Insert(t=6.1, value=40), Insert(t=10.0, value=2)]
    assert [(i.t, i.approved) for i in inserts.merge(clip, new)] == [(1.0, True), (6.0, True), (10.0, False)]


def test_transcript_edit_drops_suggestions_on_the_changed_word_only():
    sug, ok = Insert(t=2.0, value=40), Insert(t=2.0, value=40, approved=True, id="mine")
    far = Insert(t=8.0, value=12)
    clip = Clip(index=1, start=0, end=12, title="t", hook="h", score=7, inserts=[sug, ok, far], inserts_checked=True)
    assert inserts.forget_changed(clip, [(1.9, 2.3)])
    assert [i.id for i in clip.inserts] == ["mine", far.id] and clip.inserts_checked is False
    assert not inserts.forget_changed(clip, [(1.9, 2.3)])


def test_produce_suggests_once_with_the_same_gemma_load(tmp_path, monkeypatch):
    import json as _json

    from app.pipeline import orchestrator

    monkeypatch.setattr(settings, "content_enabled", False)
    renders = []

    def fake_render(video, clip, segs, job_dir):
        renders.append(clip.index)
        clip.stale = False
        return f"short_{clip.index}.mp4"

    monkeypatch.setattr(orchestrator, "render_clip", fake_render)
    monkeypatch.setattr(orchestrator, "cover_for", lambda *a: None)
    clip, segs = _stat_clip()
    (tmp_path / "transcript.json").write_text(_json.dumps([s.model_dump() for s in segs]))
    llm = CardLLM({"cards": [{"id": 1, "worth": 9, "label": "startups fail"}]})
    orchestrator.produce(tmp_path, [clip], lambda *a: None, llm=llm)
    assert [(inserts.display(i), i.label, i.approved) for i in clip.inserts] == [("40%", "startups fail", False)]
    assert clip.inserts_checked and renders == [1] and len(llm.prompts) == 1

    orchestrator.produce(tmp_path, [clip], lambda *a: None, llm=llm)  # nothing to render, nothing to ask
    assert len(llm.prompts) == 1 and renders == [1]

    monkeypatch.setattr(settings, "inserts_auto", True)  # auto: approved → the rendered short goes stale
    clip.inserts, clip.inserts_checked, clip.file = [], False, "short_1.mp4"
    orchestrator.produce(tmp_path, [clip], lambda *a: None, llm=llm)
    assert clip.inserts[0].approved and renders == [1, 1]


def test_suggest_endpoint(tmp_path, monkeypatch):
    import json as _json

    from app.jobs import GPU_LOCK

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    llm = CardLLM({"cards": [{"id": 1, "worth": 9, "label": "startups fail"}, {"id": 3, "worth": 7, "label": "x"}]})
    with TestClient(create_app(steps=steps, llm_factory=lambda: llm)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1}).json()["id"]
        _wait_status(c, jid, "review")
        _, segs = _stat_clip(start=20.0)  # clip 1 is 20-35 s
        (tmp_path / "jobs" / jid / "transcript.json").write_text(_json.dumps([s.model_dump() for s in segs]))
        mine = {"t": 33.0, "value": 3, "suffix": "x", "custom": True, "approved": True}
        c.patch(f"/api/jobs/{jid}/clips/1", json={"inserts": [mine]})

        with GPU_LOCK:
            assert c.post(f"/api/jobs/{jid}/clips/1/inserts/suggest").status_code == 409
        r = c.post(f"/api/jobs/{jid}/clips/1/inserts/suggest")
        assert r.status_code == 200
        got = r.json()["clips"][0]
        assert [(i["value"], i["approved"], i["custom"]) for i in got["inserts"]] == [
            (40, False, False), (2, False, False), (3, True, True)]
        assert got["inserts_checked"] is True and got["inserts"][1]["label"] == "last year"

        # fixing the misheard "40%" drops that suggestion, keeps the rest
        r = c.patch(f"/api/jobs/{jid}/words", json={"seg": 0, "i": 1, "word": " 45%"})
        assert r.status_code == 200
        assert [i["value"] for i in r.json()["clips"][0]["inserts"]] == [2, 3]


def test_whisper_number_pieces_are_joined_and_indices_stay_on_the_words():
    pieces = [" had", " 99", ".99", "%", " of", " 10,", " 15", " years"]  # as Whisper wrote them
    ws = [Word(start=i * 0.3, end=i * 0.3 + 0.25, word=t) for i, t in enumerate(pieces)]
    a, b, c = find(ws)
    assert (a.text(), a.start, a.end, a.spoken) == ("99.99%", 1, 4, "99.99%")
    assert (b.text(), b.start, b.end, c.text()) == ("10", 5, 6, "15")  # "10, 15": two numbers, not 1,015


def test_number_repeated_in_the_label_is_dropped():
    clip, segs = _stat_clip()
    got = inserts.suggest(clip, segs, CardLLM({"cards": [{"id": 1, "worth": 9, "label": "40% of startups fail"}]}))
    assert got[0].label == "of startups fail"


def test_labels_allow_plurals_and_hyphens_and_rule_labels_end_cleanly():
    clip = Clip(index=1, start=0, end=20, title="t", hook="h", score=7)
    segs = [seg("Huberman says 99% of people need a two to five minute transition period between tasks.")]
    c = inserts.candidates(segs, clip)[0]
    assert inserts.rule_label(c) == "of people need"  # not "of people need a two"
    assert inserts.label_ok("of people need transition periods", c)
    assert inserts.label_ok("two-to-five minute transition", c)
    assert not inserts.label_ok("of people need long breaks", c)
