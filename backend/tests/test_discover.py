"""Find videos for a topic: search + length filter + Creative Commons option, scored by their captions."""
import json

import pytest
from fastapi.testclient import TestClient

from app.pipeline import discover


def _entry(vid, minutes, title="t", live=None):
    return {"id": vid, "title": title, "channel": "Ch", "duration": minutes * 60, "view_count": 1000,
            "live_status": live, "description": "d"}


@pytest.fixture
def yt(monkeypatch):
    """Fake yt-dlp: records the URLs asked for; never touches YouTube."""
    asked = []
    entries = [_entry("aaaaaaaaaaa", 12, "Salary negotiation"), _entry("bbbbbbbbbbb", 3, "a short"),
               _entry("ccccccccccc", 200, "a 3h stream"), _entry("ddddddddddd", 25, "live", live="is_live"),
               {"id": "eeeeeeeeeee", "title": "no duration"}, _entry("fffffffffff", 40, "Negotiating pay")]
    lines = [(5.0, "today we talk about salary negotiation"), (20.0, "negotiate the salary, then negotiate"),
             (300.0, "something else entirely"), (600.0, "back to salary: negotiating offers")]
    info = {"title": "Salary negotiation", "duration": 720, "language": "en", "license": "Creative Commons "
            "Attribution license (reuse allowed)", "upload_date": "20240102", "view_count": 5, "like_count": 1,
            "automatic_captions": {"en-orig": [{"ext": "json3", "url": "https://captions/x"}]}}

    def extract(url, flat=False, limit=None):
        asked.append((url, flat, limit))
        return {"entries": entries} if flat else info

    def fetch(url):
        return json.dumps({"events": [{"tStartMs": t * 1000, "segs": [{"utf8": x}]} for t, x in lines]}).encode()

    monkeypatch.setattr(discover, "_extract", extract)
    monkeypatch.setattr(discover, "_fetch", fetch)
    discover._video.cache_clear()
    yield asked
    discover._video.cache_clear()


def test_search_keeps_long_form_videos_only(yt):
    found = discover.search("  salary   negotiation ", n=10)
    assert [r["id"] for r in found] == ["aaaaaaaaaaa", "fffffffffff"]  # no short, 3 h stream, live, unknown length
    assert yt[0] == ("ytsearch20:salary negotiation", True, 20)
    assert found[0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa" and found[0]["thumbnail"].startswith(
        "https://i.ytimg.com/vi/aaaaaaaaaaa/")
    assert discover.search("salary", min_minutes=30)[0]["id"] == "fffffffffff"
    assert discover.search("") == []


def test_creative_commons_uses_youtubes_filter(yt):
    discover.search("salary negotiation", cc=True)
    url = yt[-1][0]
    assert url.startswith("https://www.youtube.com/results?") and "sp=EgIwAQ%3D%3D" in url
    assert "salary+negotiation" in url


def test_score_finds_where_the_topic_is_discussed(yt):
    s = discover.score("aaaaaaaaaaa", "salary negotiation")
    assert s["has_captions"] and s["cc"] and s["upload_date"] == "2024-01-02"
    assert s["hits"] >= 7 and 0 < s["score"] <= 100
    assert [m["t"] for m in s["moments"]] == [5, 600]  # minute 0 (dense) and minute 10; minute 5 had nothing
    assert discover.score("aaaaaaaaaaa", "quantum physics")["score"] == 0
    with pytest.raises(ValueError):
        discover.score("../../etc", "x")


def test_more_topic_scores_higher():
    lines = [(t * 30.0, "salary negotiation tips" if t % 2 == 0 else "other stuff") for t in range(40)]
    rare = [(t * 30.0, "salary" if t == 3 else "other stuff") for t in range(40)]
    assert discover.score_lines(lines, "salary negotiation", 1200)["score"] > \
        discover.score_lines(rare, "salary negotiation", 1200)["score"]
    assert discover.score_lines([], "salary", 600)["score"] is None  # no captions: can't judge


def test_hindi_captions_match_an_english_topic():
    hindi = [(10.0, "आज हम स्टार्टअप फंडरेजिंग की बात करेंगे"), (20.0, "हर स्टार्टअप को इन्वेस्टर चाहिए")]
    s = discover.score_lines(hindi, "startup fundraising", 600)
    assert s["hits"] >= 3 and s["moments"] and "startaap" in s["moments"][0]["text"]


def test_skeleton():
    assert discover.skeleton("startup")[:4] == discover.skeleton("startaap")[:4] == "strt"
    assert discover.skeleton("fundraising")[:4] == discover.skeleton("phandarejing")[:4] == "fndr"


def test_api_marks_videos_you_already_did(yt):
    from app.main import create_app

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    app.state.store.create("https://youtu.be/aaaaaaaaaaa", 1)
    with TestClient(app) as c:
        r = c.get("/api/discover", params={"topic": "salary negotiation", "cc": "false"}).json()["results"]
        assert [(x["id"], x["done"]) for x in r] == [("aaaaaaaaaaa", 1), ("fffffffffff", 0)]
        assert c.get("/api/discover", params={"topic": " "}).status_code == 400
        s = c.get("/api/discover/score", params={"id": "aaaaaaaaaaa", "topic": "salary"}).json()
        assert s["score"] > 0 and s["cc"]
        assert c.get("/api/discover/score", params={"id": "nope", "topic": "x"}).status_code == 400


def test_youtube_errors_are_a_502(monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(discover, "_extract", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Sign in to "
                                                                                                "confirm")))
    discover._video.cache_clear()
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    with TestClient(app) as c:
        r = c.get("/api/discover", params={"topic": "salary"})
        assert r.status_code == 502 and "Sign in" in r.json()["detail"]
        assert c.get("/api/discover/score", params={"id": "aaaaaaaaaaa", "topic": "x"}).status_code == 502


# ------------------------------------------------------------------ smart search (Gemma)

class Gemma:
    """Fake LLM: answers by schema; records prompts."""
    def __init__(self, queries=None, judge=None):
        self.queries = queries if queries is not None else ["salary negotiation podcast", "how to negotiate pay",
                                                            "salary negotiation podcast", "salary kaise badhaye"]
        self.verdict = judge or {"relevance": 8, "best": 2, "why": "Explains **anchoring** high, with a real offer."}
        self.prompts = []

    def chat_json(self, prompt, schema, **kw):
        self.prompts.append(prompt)
        return {"queries": self.queries} if "queries" in schema["properties"] else self.verdict

    def unload(self):
        pass


def test_expand_queries_keeps_the_topic_first_and_cleans_gemmas():
    llm = Gemma(queries=['"salary negotiation podcast" #career', "SALARY NEGOTIATION", "x " * 20, "pay raise tips",
                         "salary kaise badhaye"])
    qs = discover.expand_queries("  salary negotiation ", llm)
    assert qs == ["salary negotiation", "salary negotiation podcast career", "pay raise tips", "salary kaise badhaye"]
    assert "Hinglish" in llm.prompts[0]
    assert discover.expand_queries("salary", Gemma(queries="nonsense")) == ["salary"]


def test_search_many_merges_round_robin_without_duplicates(monkeypatch):
    results = {"a": [{"id": "1"}, {"id": "2"}], "b": [{"id": "2"}, {"id": "3"}], "c": []}

    def fake_search(q, n=20, **kw):
        if q == "boom":
            raise RuntimeError("blocked")
        return [dict(r) for r in results[q]]

    monkeypatch.setattr(discover, "search", fake_search)
    found = discover.search_many(["a", "b", "boom", "c"])
    assert [(r["id"], r["found_by"]) for r in found] == [("1", "a"), ("2", "b"), ("3", "b")]  # each top first
    with pytest.raises(RuntimeError):
        discover.search_many(["boom"])


def test_judge_rates_from_the_best_moments(yt):
    llm = Gemma()
    v = discover.judge("aaaaaaaaaaa", "salary negotiation", llm)
    assert v == {"id": "aaaaaaaaaaa", "relevance": 8, "why": "Explains anchoring high, with a real offer.",
                 "best_t": 600}  # excerpt 2 = the moment at 10:00
    assert "[1] (0:05)" in llm.prompts[0] and "negotiate the salary" in llm.prompts[0]
    assert discover.judge("aaaaaaaaaaa", "quantum physics", Gemma())["relevance"] is None  # never discussed
    with pytest.raises(ValueError):
        discover.judge("aaaaaaaaaaa", "salary", Gemma(judge={"best": 1, "why": "?"}))  # no rating


def test_api_smart_search_takes_the_gpu_and_waits_for_jobs(yt):
    from app.jobs import GPU_LOCK
    from app.main import create_app

    llm = Gemma()
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False, llm_factory=lambda: llm)
    with TestClient(app) as c:
        qs = c.post("/api/discover/expand", json={"topic": "salary negotiation"}).json()["queries"]
        assert qs[0] == "salary negotiation" and len(qs) == 4
        r = c.get("/api/discover", params=[("topic", "salary negotiation"), ("cc", "false"),
                                           *[("queries", q) for q in qs]]).json()["results"]
        assert [x["id"] for x in r] == ["aaaaaaaaaaa", "fffffffffff"] and r[0]["found_by"] == "salary negotiation"
        assert len([u for u, flat, _ in yt if flat]) == 4  # every query searched
        v = c.post("/api/discover/judge", json={"id": "aaaaaaaaaaa", "topic": "salary negotiation"}).json()
        assert v["relevance"] == 8 and v["best_t"] == 600
        assert c.post("/api/discover/judge", json={"id": "../etc", "topic": "x"}).status_code == 422
        with GPU_LOCK:
            r = c.post("/api/discover/expand", json={"topic": "salary"})
            assert r.status_code == 409 and "plain search" in r.json()["detail"]
        llm.verdict = {"why": "no number"}
        assert c.post("/api/discover/judge", json={"id": "aaaaaaaaaaa", "topic": "salary"}).status_code == 502
