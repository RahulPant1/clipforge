"""Learning mode (learn.py): per-window notes → summary/chapters/concepts/quiz, all tied to real transcript times."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Concept, LearnNotes, Segment, StudyNote
from app.pipeline import learn


def segs(n=40, step=15.0) -> list[Segment]:
    return [Segment(start=i * step, end=i * step + step - 1, text=f"line {i} about drones.") for i in range(n)]


class LearnLLM:
    """Fake Gemma: map calls → notes at the window's own times; reduce call → `reduce` (or an error)."""

    def __init__(self, reduce=None, fail_window=None, reduce_error=None):
        self.reduce, self.fail_window, self.reduce_error = reduce, fail_window, reduce_error
        self.prompts, self.model = [], "fake-gemma"

    def chat_json(self, prompt, schema, temperature=None, think=None, images=None):
        self.prompts.append(prompt)
        if "chapter" in schema["properties"]:  # map
            k = len([p for p in self.prompts if "Transcript part" in p])
            if k == self.fail_window:
                raise ConnectionError("hiccup")
            times = [float(x.split("-")[0]) for x in prompt.split("[")[1:] if x[:1].isdigit()]
            return {"chapter": f"Part about thing {k}",
                    "notes": [{"t": times[0] + 0.4, "title": f"Point {k}a", "note": "**Bold** idea."},
                              {"t": times[1], "title": f"Point {k}b", "note": "Another idea."},
                              {"t": 99999, "title": "Far away", "note": "dropped"},
                              {"t": "x", "title": "bad", "note": "dropped"}],
                    "concepts": [{"name": "Failsafe", "explanation": "What happens on signal loss.", "t": times[0]},
                                 {"name": f"Concept {k}", "explanation": "Explained here.", "t": times[1]}]}
        if self.reduce_error:
            raise self.reduce_error
        return self.reduce

    def unload(self):
        pass


def reduce_answer(**kw):
    base = {"summary": "It teaches drones.",
            "chapters": [{"ref": "P1", "title": "Start"}, {"ref": "N2", "title": "Second"},
                         {"ref": "N3", "title": "Too close?"}, {"ref": "P2", "title": "Later"},
                         {"ref": "N99", "title": "nope"}],
            "key_concepts": ["C2", "C1", "C2", "C77"],
            "quiz": [{"q": "What is a failsafe?", "choices": ["A", "B", "C", "D"], "answer": 1, "explain": "Because.",
                      "ref": "N1"},
                     {"q": "Bad answer index", "choices": ["A", "B"], "answer": 5, "explain": "", "ref": "N1"},
                     {"q": "Dup choices", "choices": ["A", "A"], "answer": 0, "explain": "", "ref": "N1"},
                     {"q": "Bad ref", "choices": ["A", "B"], "answer": 0, "explain": "", "ref": "N999"}],
            "hands_on": ["Try *it* in the simulator."], "next_steps": ["Learn PID tuning."]}
    return {**base, **kw}


def test_notes_are_tied_to_real_times_and_validated(monkeypatch):
    monkeypatch.setattr(settings, "window_seconds", 300)
    s = segs()
    llm = LearnLLM(reduce_answer())
    n = learn.build(s, llm, duration=600, title="Drone basics")
    starts = {x.start for x in s}
    assert n.notes and all(x.t in starts for x in n.notes)  # snapped to a line start; far/bad ones dropped
    assert all("Far away" != x.title for x in n.notes) and n.notes[0].note == "Bold idea."
    assert n.chapters[0].t == 0.0 and [c.title for c in n.chapters][:2] == ["Start", "Second"]
    assert all(b.t - a.t >= learn.MIN_CHAPTER_GAP for a, b in zip(n.chapters, n.chapters[1:], strict=False))
    names = [c.name for c in n.concepts]
    assert names[:2] == ["Concept 1", "Failsafe"] and names.count("Failsafe") == 1  # C2, C1 order; deduped
    assert [q.q for q in n.quiz] == ["What is a failsafe?"] and n.quiz[0].t == n.notes[0].t
    assert n.hands_on == ["Try it in the simulator."] and n.model == "fake-gemma"
    assert '"Drone basics"' in llm.prompts[0]


def test_a_failed_part_or_summary_still_gives_notes(monkeypatch):
    monkeypatch.setattr(settings, "window_seconds", 200)
    s = segs()
    n = learn.build(s, LearnLLM(reduce_answer(), fail_window=2), duration=600)
    assert n.notes and not any(x.title.startswith("Point 2") for x in n.notes)
    n = learn.build(s, LearnLLM(reduce_error=ValueError("bad json")), duration=600)
    assert n.summary == "" and n.chapters[0].t == 0.0 and len(n.chapters) >= 2 and n.notes and n.concepts
    with pytest.raises(RuntimeError, match="needs speech"):
        learn.build([], LearnLLM(), duration=10)


def test_few_chapters_fall_back_to_one_per_part(monkeypatch):
    monkeypatch.setattr(settings, "window_seconds", 200)
    n = learn.build(segs(), LearnLLM(reduce_answer(chapters=[{"ref": "P1", "title": "Only one"}])), duration=600)
    assert [c.title for c in n.chapters][0] == "Part about thing 1" and len(n.chapters) >= 3


def test_markdown_links_youtube_times():
    n = LearnNotes(title="T", summary="S", notes=[StudyNote(t=75, title="A", note="B")],
                   concepts=[Concept(name="C", explanation="E", t=3725)])
    md = learn.markdown(n, "https://www.youtube.com/watch?v=abcdefghijk")
    assert "[1:15](https://youtu.be/abcdefghijk?t=75) **A** — B" in md and "(1:02:05](" not in md
    assert "[1:02:05](https://youtu.be/abcdefghijk?t=3725)" in md
    assert "- 1:15 **A**" in learn.markdown(n, "")  # not YouTube: plain times
    assert learn.youtube_id("https://youtu.be/abcdefghijk?t=3") == "abcdefghijk"


def test_learn_pipeline_writes_notes(tmp_path, monkeypatch):
    from app.pipeline import orchestrator

    monkeypatch.setattr(orchestrator, "ingest", lambda src, d, rep, llm, romanize=False: (d / "v.mp4", segs(), 600.0))
    (tmp_path / "meta.json").write_text(json.dumps({"title": "Drone basics", "url": "https://youtu.be/abcdefghijk"}))
    n = orchestrator.learn_pipeline("x", tmp_path, lambda *a: None, LearnLLM(reduce_answer()))
    assert learn.load(tmp_path).summary == n.summary == "It teaches drones."
    assert "youtu.be/abcdefghijk?t=" in (tmp_path / "notes.md").read_text()


# ------------------------------------------------------------------ worker + API

def test_learn_jobs_and_notes_for_an_existing_job(tmp_path, monkeypatch):
    from .test_api import _fake_steps, _wait_status

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, calls = _fake_steps(tmp_path)
    learned = []

    def fake_learn(source, job_dir, report, llm=None, romanize=False):
        learned.append(job_dir.name)
        (job_dir / "source.mp4").write_bytes(b"v")
        n = LearnNotes(title="T", summary="Notes!", notes=[StudyNote(t=0, title="A", note="B")])
        learn.save(n, job_dir)
        return n

    steps.learn = fake_learn
    with TestClient(create_app(steps=steps)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "purpose": "learn"}).json()["id"]
        job = _wait_status(c, jid, "done")
        assert job["purpose"] == "learn" and job["learn"] is True and job["clips"] == [] and learned == [jid]
        assert calls["produce"] == 0
        got = c.get(f"/api/jobs/{jid}/learn").json()
        assert got["notes"]["summary"] == "Notes!" and got["markdown"] == "notes.md"
        assert c.get(f"/api/jobs/{jid}/files/notes.md").status_code == 200

        sid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=y", "num_clips": 1}).json()["id"]  # shorts
        _wait_status(c, sid, "review")
        assert c.get(f"/api/jobs/{sid}/learn").json() == {"notes": None, "markdown": None}
        assert c.post(f"/api/jobs/{sid}/learn").status_code == 400  # no video yet in the fake job
        (tmp_path / "jobs" / sid / "source.mp4").write_bytes(b"v")
        assert c.post(f"/api/jobs/{sid}/learn").status_code == 200
        job = _wait_status(c, sid, "review", lambda j: j["learn"])  # back to where it was, now with notes
        assert job["purpose"] == "create" and len(job["clips"]) == 2
