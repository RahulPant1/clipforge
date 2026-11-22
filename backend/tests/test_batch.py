"""Batch: several links / playlists queued as jobs, queue kept across restarts, watched folder."""
import os

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import JobStatus
from app.pipeline import batch, discover

PL = "https://www.youtube.com/playlist?list=PL123"


@pytest.fixture
def yt(monkeypatch):
    """Fake yt-dlp playlist listing: never touches YouTube."""
    asked = []
    entries = [{"id": "aaaaaaaaaaa", "title": "One", "channel": "Ch", "duration": 900},
               {"id": "bbbbbbbbbbb", "title": "[Private video]"},
               {"id": "ccccccccccc", "title": "Live now", "live_status": "is_live"},
               {"id": "ddddddddddd", "title": "Two", "uploader": "Up", "duration": 1800}]

    def extract(url, flat=False, limit=None):
        asked.append(url)
        if "broken" in url:
            raise RuntimeError("\x1b[0;31mERROR:\x1b[0m This playlist does not exist")
        return {"title": "My list", "entries": entries}

    monkeypatch.setattr(discover, "_extract", extract)
    return asked


def test_links_and_what_is_a_list():
    text = "https://youtu.be/aaaaaaaaaaa,\n  see https://www.youtube.com/watch?v=bbbbbbbbbbb&list=PL1. junk"
    assert batch.links(text) == ["https://youtu.be/aaaaaaaaaaa",
                                 "https://www.youtube.com/watch?v=bbbbbbbbbbb&list=PL1"]
    assert batch.is_list(PL) and batch.is_list("https://www.youtube.com/@somechannel")
    assert not batch.is_list("https://www.youtube.com/watch?v=bbbbbbbbbbb&list=PL1")  # that's the video
    assert not batch.is_list("https://youtu.be/aaaaaaaaaaa")


def test_expand_lists_playlists_and_drops_duplicates(yt):
    out = batch.expand(f"https://youtu.be/ddddddddddd\n{PL}\nhttps://example.com/talk.mp4\n"
                       "https://www.youtube.com/playlist?list=broken")
    assert [(v["id"], v["from"]) for v in out["videos"]] == [("ddddddddddd", ""), ("aaaaaaaaaaa", "My list"),
                                                             ("", "")]  # Two is already in; private/live skipped
    assert out["videos"][1]["title"] == "One" and out["videos"][1]["duration"] == 900
    assert out["videos"][2]["url"] == "https://example.com/talk.mp4"
    assert [(e["url"], e["error"]) for e in out["errors"]] == [("https://www.youtube.com/playlist?list=broken",
                                                              "This playlist does not exist")]
    batch.expand("https://www.youtube.com/@somechannel")
    assert yt[-1] == "https://www.youtube.com/@somechannel/videos"  # a channel's uploads, not its tabs
    capped = batch.expand(PL, limit=1)
    assert len(capped["videos"]) == 1 and capped["more"]


def test_api_queues_a_batch_in_order_and_cancels_waiting_jobs(yt, tmp_path):
    from app.main import create_app

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store, worker = app.state.store, app.state.worker
    store.create("https://www.youtube.com/watch?v=aaaaaaaaaaa", 1)
    with TestClient(app) as c:
        v = c.post("/api/batch/expand", json={"text": PL}).json()["videos"]
        assert [(x["id"], x["done"]) for x in v] == [("aaaaaaaaaaa", 1), ("ddddddddddd", 0)]

        r = c.post("/api/jobs/batch", json={"urls": ["https://youtu.be/ddddddddddd", "https://youtu.be/eeeeeeeeeee",
                                                     "https://www.youtube.com/watch?v=ddddddddddd"],
                                            "num_clips": 2, "topic": "pricing", "hinglish": False})
        jobs = r.json()["jobs"]
        assert r.status_code == 201 and len(jobs) == 2  # the same video twice → once
        assert all(j["status"] == "queued" and j["num_clips"] == 2 and j["topic"] == "pricing" for j in jobs)
        queued = [worker.q.get_nowait() for _ in range(worker.q.qsize())]
        assert [(k, jid) for k, jid, _ in queued][-2:] == [("run", jobs[0]["id"]), ("run", jobs[1]["id"])]
        for item in queued:
            worker.q.put(item)

        assert c.post("/api/jobs/batch", json={"urls": ["file:///etc/passwd"]}).status_code == 400
        assert c.post("/api/jobs/batch", json={"urls": []}).status_code == 422

        # a waiting job can be cancelled; the worker then skips it
        assert c.delete(f"/api/jobs/{jobs[1]['id']}").status_code == 204
        worker._run("run", jobs[1]["id"], source="https://youtu.be/eeeeeeeeeee")
        assert store.get(jobs[1]["id"]) is None
        # a job whose clips are queued for rendering is still busy
        store.update(jobs[0]["id"], clips=[], status=JobStatus.running)
        assert c.delete(f"/api/jobs/{jobs[0]['id']}").status_code == 409


def test_waiting_jobs_survive_a_restart_in_order(tmp_path, monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    first = store.create("https://youtu.be/aaaaaaaaaaa", 1)
    second = store.create("https://youtu.be/bbbbbbbbbbb", 1)
    upload = store.create("clip.mp4", 1)  # upload whose file never arrived
    running = store.create("https://youtu.be/ccccccccccc", 1)
    store.update(running.id, status=JobStatus.running)

    again = create_app(pipeline=lambda *a, **k: [], start_worker=False)  # = server restart
    s2, w2 = again.state.store, again.state.worker
    assert [jid for _, jid, _ in list(w2.q.queue)] == [first.id, second.id]
    assert s2.get(first.id).status == JobStatus.queued
    assert s2.get(upload.id).status == JobStatus.failed and "upload" in s2.get(upload.id).error
    assert s2.get(running.id).status == JobStatus.failed  # mid-prepare: as before, Retry reuses its download


def test_watched_folder_takes_settled_videos_once(tmp_path, monkeypatch):
    from app.jobs import JobStore
    from app.watch import Watcher

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "watch_clips", 3)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    store = JobStore(tmp_path / "data" / "jobs")
    submitted = []
    w = Watcher(store, lambda jid, src: submitted.append((jid, src)), folder=inbox, interval=0)

    (inbox / "talk.mp4").write_bytes(b"x" * 10)
    (inbox / "notes.txt").write_text("not a video")
    assert w.scan() == []  # first sight: might still be copying
    (inbox / "talk.mp4").write_bytes(b"x" * 20)
    assert w.scan() == []  # grew: still copying
    made = w.scan()
    assert len(made) == 1 and submitted[0][1].endswith("source.mp4")
    job = store.get(made[0])
    assert job.source == "talk.mp4" and job.num_clips == 3 and job.status == JobStatus.queued
    assert (store.dir(job.id) / "source.mp4").read_bytes() == b"x" * 20 and (inbox / "talk.mp4").exists()
    assert w.scan() == []  # taken once

    w2 = Watcher(store, lambda *a: submitted.append(a), folder=inbox, interval=0)  # restart
    w2.scan()
    assert w2.scan() == []  # remembered in watched.json
    st = (inbox / "talk.mp4").stat()
    os.utime(inbox / "talk.mp4", (st.st_atime, st.st_mtime + 60))  # replaced by a new version
    w2.scan()
    assert len(w2.scan()) == 1


# ------------------------------------------------------------------ job titles (Jobs list)

def test_job_titles_from_the_list_the_download_or_an_old_meta_json(tmp_path, monkeypatch):
    import json

    from app.jobs import JobStore, Worker
    from app.main import create_app
    from app.models import Clip, Stage
    from app.pipeline.orchestrator import Steps

    monkeypatch.setattr(settings, "data_dir", tmp_path / "d")
    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    with TestClient(app) as c:
        jobs = c.post("/api/jobs/batch", json={
            "urls": ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb"],
            "info": {"https://youtu.be/aaaaaaaaaaa": {"title": "  Salary   talk ", "channel": "Ch"}}}).json()["jobs"]
        assert [(j["title"], j["channel"]) for j in jobs] == [("Salary talk", "Ch"), ("", "")]
        one = c.post("/api/jobs", json={"url": "https://youtu.be/ccccccccccc", "title": "From Find"}).json()
        assert one["title"] == "From Find"

    # the download writes meta.json → the next progress report puts the title on the job
    store = JobStore(tmp_path / "w")
    job = store.create("https://youtu.be/ddddddddddd", 1)

    def prepare(source, job_dir, num_clips, report, candidates=None):
        report(Stage.downloading, 0.5, "downloading")
        assert store.get(job.id).title == ""
        (job_dir / "meta.json").write_text(json.dumps({"title": "Real title", "channel": "Real channel"}))
        report(Stage.transcribing, 0.1, "transcribing")
        assert (store.get(job.id).title, store.get(job.id).channel) == ("Real title", "Real channel")
        return [Clip(index=1, start=0, end=20, title="c", hook="h", score=5)]

    worker = Worker(store, steps=Steps(prepare=prepare))
    job_dir = store.dir(job.id)
    job_dir.mkdir(parents=True, exist_ok=True)
    worker._run("run", job.id, source=job.source)
    assert store.get(job.id).status == JobStatus.review

    # a job from before titles: read from its meta.json when the store loads
    old = store.create("https://youtu.be/eeeeeeeeeee", 1)
    store.update(old.id, status=JobStatus.done)
    (store.dir(old.id) / "meta.json").write_text(json.dumps({"title": "Old one", "channel": "Old ch"}))
    assert JobStore(tmp_path / "w").get(old.id).title == "Old one"
