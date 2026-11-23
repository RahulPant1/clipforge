"""Clip judge (judge.py): rule checks, Gemma's numbered sentence picks, fixes, verdicts, wiring, eval summary."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Clip, ClipFix, ClipReview, ReviewCheck, Segment, Word
from app.pipeline import judge


def talk(*sentences: str, start: float = 0.0, gap: float = 0.1, step: float = 0.4) -> list[Segment]:
    """One segment per sentence; words step s apart; `gap` s of silence between sentences."""
    segs, t = [], start
    for text in sentences:
        words = []
        for tok in text.split():
            words.append(Word(start=round(t, 2), end=round(t + step * 0.8, 2), word=f" {tok}"))
            t += step
        segs.append(Segment(start=words[0].start, end=words[-1].end, text=text, words=words))
        t += gap
    return segs


S = ["We tried every note app for a year.", "Most of them failed for one reason.", "This approach fixed it for us.",
     "You write the note before the meeting starts.", "Then you only add what changed.",
     "It sounds small but it saves an hour a week.", "Try it tomorrow morning."]


def clip_over(segs, i, j, **kw) -> Clip:
    """Clip from the start of sentence i to the end of sentence j (inclusive)."""
    return Clip(index=1, start=segs[i].start, end=segs[j].end, title="t", hook="h", score=7, **kw)


class JudgeLLM:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.prompts = answer, error, []

    def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
        assert "setup_lines" in schema["properties"]
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer

    def unload(self):
        pass


def ok(**kw):
    return {"setup_lines": [], "setup_why": "", "payoff_lines": [], "payoff_why": "", "hook_ok": True,
            "faithful": True, "faithful_note": "", "value_ok": True, "clear_ok": True, **kw}


# ------------------------------------------------------------------ transcript views + rules

def test_sentences_split_on_punctuation_or_a_pause():
    words = [(0, Word(start=0, end=0.3, word=" so")), (0, Word(start=0.4, end=0.7, word=" here")),
             (0, Word(start=1.5, end=1.8, word=" next")), (0, Word(start=1.9, end=2.2, word=" one.")),
             (0, Word(start=2.3, end=2.6, word=" Done"))]
    assert [judge._text(s) for s in judge.sentences(words)] == ["so here", "next one.", "Done"]


def test_rules_mid_sentence_edges_lists_and_reference_hints():
    segs = talk(*S)
    whole = judge.rule_checks(segs, clip_over(segs, 3, 5))
    assert whole == []  # whole sentences, starts on "You": nothing to say
    mid = clip_over(segs, 3, 5).model_copy(update={"start": segs[3].words[2].start, "end": segs[5].words[3].end})
    keys = {(c.key, c.source) for c in judge.rule_checks(segs, mid)}
    assert keys == {("opening", "rule"), ("ending", "rule")}
    hint = judge.rule_checks(segs, clip_over(segs, 2, 4))  # "This approach fixed it…" → a hint, not an issue
    assert [(c.key, c.ok) for c in hint] == [("hint", False)]
    assert judge.verdict(hint, None) == "ready"

    paused = talk(*S, gap=0.8)  # a long pause before the first word = a boundary even without punctuation
    bare = [Segment(**{**s.model_dump(), "words": [w.model_copy(update={"word": w.word.rstrip(".")})
                                                    for w in s.words]}) for s in paused]
    assert judge.rule_checks(bare, clip_over(bare, 3, 5)) == []

    lst = talk("There are three reasons this works.", "First, it is short.", "Second, it is clear.",
               "That is all for today.")
    found = judge.rule_checks(lst, clip_over(lst, 0, 2))
    assert [c.key for c in found] == ["list"] and "only 2" in found[0].note


@pytest.mark.parametrize("checks, fix, want", [
    ([], None, "ready"),
    ([ReviewCheck(key="opening", ok=False)], ClipFix(start=0, end=30), "trim"),
    ([ReviewCheck(key="ending", ok=False)], None, "needs_context"),
    ([ReviewCheck(key="faithful", ok=False)], ClipFix(start=0, end=30), "reject"),
    ([ReviewCheck(key="hook", ok=False), ReviewCheck(key="value", ok=False)], None, "reject"),
    ([ReviewCheck(key="hook", ok=False)], None, "ready"),
    ([ReviewCheck(key="hint", ok=False, source="rule")], None, "ready"),
])
def test_verdict_rule(checks, fix, want):
    assert judge.verdict(checks, fix) == want


def test_fixes_snap_to_sentences_and_respect_the_short_limit(monkeypatch):
    segs = talk(*S)
    c = clip_over(segs, 3, 4)
    fix, why = judge.make_fix(segs, c, segs[2].start + 1.0, None, "setup")
    assert why == "" and fix.start == segs[2].start and fix.end == c.end  # snapped back to the sentence start
    assert fix.reason.startswith("Setup: start") and "s earlier" in fix.reason
    monkeypatch.setattr(settings, "max_clip_seconds", 8.0)
    fix, why = judge.make_fix(segs, c, segs[1].start, None, "setup", snap=False)
    assert fix is None and "over the 8 s" in why


# ------------------------------------------------------------------ Gemma picks sentences by number

def test_gemma_picks_numbered_sentences_and_the_fix_follows():
    segs = talk(*S)
    c = clip_over(segs, 2, 4)  # starts "This approach…": B1 = "Most of them failed…", B2 = "We tried…"
    llm = JudgeLLM(ok(setup_lines=[1], setup_why="This approach", payoff_lines=[1, 9], hook_ok=False))
    r = judge.review(segs, c, llm)
    p = llm.prompts[0]
    assert "B1 [" in p and "Most of them failed" in p.split("B1 [")[1].split("\n")[0]
    assert "A1 [" in p and "It sounds small" in p.split("A1 [")[1].split("\n")[0]
    assert r.verdict == "trim" and r.model and r.fix
    assert (r.fix.start, r.fix.end) == (segs[1].start, segs[5].end)  # B1's start, A1's end (9 is ignored)
    bad = {x.key: x for x in r.checks if not x.ok}
    assert set(bad) == {"opening", "ending", "hook", "hint"} and bad["opening"].quote == "This approach"
    assert "Most of them failed" in bad["opening"].note
    assert {x.key for x in r.checks if x.ok} == {"clear", "faithful", "value"}


@pytest.mark.parametrize("answer", [{"setup_lines": "1"}, "nope", ok(hook_ok="yes")])
def test_a_bad_gemma_answer_leaves_the_rule_checks(answer):
    segs = talk(*S)
    c = clip_over(segs, 3, 5).model_copy(update={"end": segs[5].words[3].end})  # ends mid-sentence
    r = judge.review(segs, c, JudgeLLM(answer))
    assert r.model is None and [x.key for x in r.checks if not x.ok] == ["ending"]
    assert r.verdict == "trim" and r.fix.end == segs[5].end  # the rule fix: on to the sentence end
    r = judge.review(segs, c, JudgeLLM(error=ConnectionError("down")))
    assert r.model is None and r.verdict == "trim"


def test_struck_words_are_not_in_the_judged_clip():
    segs = talk(*S)
    c = clip_over(segs, 3, 5, removed=[(segs[4].start, segs[4].end)])
    llm = JudgeLLM(ok())
    judge.review(segs, c, llm)
    clip_part = llm.prompts[0].split("THE CLIP (")[1].split("Sentences AFTER")[0]
    assert "only add what changed" not in clip_part and "before the meeting" in clip_part


def test_rejected_picks_hand_their_place_to_the_best_spare():
    def c(i, approved, verdict, score):
        return Clip(index=i, start=i * 60, end=i * 60 + 30, title="t", hook="h", score=score, approved=approved,
                    review=ClipReview(verdict=verdict, start=i * 60, end=i * 60 + 30))
    clips = [c(1, True, "reject", 9), c(2, True, "ready", 8), c(3, False, "reject", 9), c(4, False, "trim", 6),
             c(5, False, "ready", 7)]
    assert judge.rebalance(clips, 2) == ["#1 rejected → #5 approved instead"]
    assert [x.index for x in clips if x.approved] == [2, 5]


# ------------------------------------------------------------------ wiring

def test_prepare_judges_candidates_and_auto_mode_takes_the_fixes(monkeypatch):
    from app.pipeline import orchestrator

    segs = talk(*S)
    clips = [clip_over(segs, 2, 4, approved=True), clip_over(segs, 5, 6, approved=False)]
    clips[1].index = 2
    llm = JudgeLLM(ok(setup_lines=[1]))
    trace = {}
    orchestrator.judge_clips(segs, clips, 1, llm, lambda *a: None, trace)
    assert trace["judge"]["verdicts"] == {"trim": 2} and all(c.review for c in clips)
    assert orchestrator.apply_fixes(clips) == 2 and clips[0].start == segs[1].start
    monkeypatch.setattr(settings, "judge_enabled", False)
    fresh = [clip_over(segs, 2, 4)]
    orchestrator.judge_clips(segs, fresh, 1, llm, lambda *a: None, {})
    assert fresh[0].review is None


def test_review_endpoint(tmp_path, monkeypatch):
    from app.jobs import GPU_LOCK

    from .test_api import _fake_steps, _wait_status

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    llm = JudgeLLM(ok(payoff_lines=[1], payoff_why="the answer"))
    with TestClient(create_app(steps=steps, llm_factory=lambda: llm)) as cl:
        jid = cl.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 1}).json()["id"]
        _wait_status(cl, jid, "review")
        segs = talk(*S, start=20.0)  # clip 1 is 20-35 s
        (tmp_path / "jobs" / jid / "transcript.json").write_text(json.dumps([s.model_dump() for s in segs]))
        with GPU_LOCK:
            assert cl.post(f"/api/jobs/{jid}/clips/1/review").status_code == 409
        r = cl.post(f"/api/jobs/{jid}/clips/1/review")
        assert r.status_code == 200
        rev = r.json()["clips"][0]["review"]
        assert rev["verdict"] in ("trim", "needs_context") and rev["start"] == 20 and rev["end"] == 35
        assert any(c["key"] == "ending" and not c["ok"] for c in rev["checks"])
        assert r.json()["clips"][0]["stale"] is False  # a review isn't burned in


def test_judge_eval_damage_and_summary():
    from app import judge_eval

    segs = talk(*(S * 4))
    c = clip_over(segs, 0, 13)  # ~37 s
    d = judge_eval.damaged(c, segs)
    assert d["late_start"].start > c.start and d["early_end"].end < c.end
    rows = [{"kind": "clean", "verdict": "ready", "flags": [], "seconds": 10, "fix_ok": False},
            {"kind": "clean", "verdict": "trim", "flags": ["opening"], "seconds": 12, "fix_ok": False},
            {"kind": "late_start", "verdict": "trim", "flags": ["opening"], "seconds": 11, "fix_ok": True},
            {"kind": "late_start", "verdict": "ready", "flags": [], "seconds": 13, "fix_ok": False},
            {"kind": "early_end", "verdict": "trim", "flags": ["list"], "seconds": 9, "fix_ok": False}]
    s = judge_eval.summarize(rows)
    assert s["clean"]["flag_opening"] == 0.5 and s["late_start"]["caught"] == 0.5
    assert s["late_start"]["fix_within_tol"] == 0.5 and s["early_end"]["caught"] == 1.0
    assert "| clean | 2 |" in judge_eval.report_md(s, [])
