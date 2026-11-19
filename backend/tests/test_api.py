import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import Clip, Stage


def fake_pipeline(source, job_dir, num_clips, report):
    report(Stage.transcribing, 0.5, "fake")
    (job_dir / "short_1.mp4").write_bytes(b"fake")
    return [Clip(index=1, start=0, end=30, title="Demo", hook="Hi", score=8, file="short_1.mp4")]


def failing_pipeline(source, job_dir, num_clips, report):
    raise RuntimeError("boom")


def _wait(client, job_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _client(tmp_path, monkeypatch, pipeline):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app
    return TestClient(create_app(pipeline=pipeline))


def test_job_lifecycle(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, fake_pipeline) as c:
        r = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 3, "mode": "auto"})
        assert r.status_code == 201
        job = _wait(c, r.json()["id"])
        assert job["status"] == "done" and job["clips"][0]["title"] == "Demo"
        f = c.get(f"/api/jobs/{job['id']}/files/short_1.mp4")
        assert f.status_code == 200 and f.content == b"fake"
        assert c.get(f"/api/jobs/{job['id']}/files/..%2Fjob.json").status_code == 404
        assert len(c.get("/api/jobs").json()) == 1
        assert c.delete(f"/api/jobs/{job['id']}").status_code == 204


def test_upload_and_failure(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, failing_pipeline) as c:
        r = c.post("/api/jobs/upload", files={"file": ("talk.mp4", b"data", "video/mp4")},
                   data={"num_clips": "2", "mode": "auto"})
        assert r.status_code == 201
        job = _wait(c, r.json()["id"])
        assert job["status"] == "failed" and job["error"] == "boom"


def test_rejects_bad_url(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, fake_pipeline) as c:
        assert c.post("/api/jobs", json={"url": "notaurl"}).status_code == 400


def test_regenerate_post_new_angle_and_gpu_busy(tmp_path, monkeypatch):
    import json as _json

    from app.jobs import GPU_LOCK
    from app.models import JobStatus

    from .conftest import SchemaFakeLLM

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    app = create_app(pipeline=fake_pipeline, start_worker=False, llm_factory=SchemaFakeLLM)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    d = store.dir(job.id)
    seg = {"start": 0, "end": 30, "text": "Compound interest.", "words": []}
    (d / "transcript.json").write_text(_json.dumps([seg]))
    store.update(job.id, status=JobStatus.done,
                 clips=[Clip(index=1, start=0, end=30, title="T", hook="H", score=8, file="short_1.mp4")])

    with TestClient(app) as c:
        first = c.post(f"/api/jobs/{job.id}/clips/1/content/x")
        assert first.status_code == 200
        second = c.post(f"/api/jobs/{job.id}/clips/1/content/x").json()
        assert second["angle"] != first.json()["angle"]  # "new angle" really is new
        assert c.get(f"/api/jobs/{job.id}").json()["clips"][0]["content"]["x"]["angle"] == second["angle"]
        assert c.post(f"/api/jobs/{job.id}/clips/1/content/tiktok").status_code == 400

        GPU_LOCK.acquire()
        try:
            assert c.post(f"/api/jobs/{job.id}/clips/1/content/x").status_code == 409
        finally:
            GPU_LOCK.release()

        z = c.get(f"/api/jobs/{job.id}/export.zip")
        assert z.status_code == 200 and z.headers["content-type"] == "application/zip"



# ---------------------------------------------------------------- review-mode pipeline

def _fake_steps(tmp_path):
    from app.pipeline.orchestrator import Steps

    calls = {"produce": 0, "render": []}

    def prepare(source, job_dir, num_clips, report, candidates=None):
        report(Stage.selecting, 1.0, "fake")
        (job_dir / "transcript.json").write_text('[{"start": 0, "end": 90, "text": "hello world", "words": []}]')
        return [Clip(index=i, start=i * 20, end=i * 20 + 15, title=f"c{i}", hook="h", score=10 - i,
                     approved=i <= num_clips) for i in range(1, (candidates or num_clips) + 1)]

    def produce(job_dir, clips, report):
        calls["produce"] += 1
        for c in clips:
            if c.approved and (not c.file or c.stale):
                c.file, c.stale, c.version = f"short_{c.index}.mp4", False, c.version + 1
                (job_dir / c.file).write_bytes(b"v")
                if not c.content:
                    from app.models import Post
                    c.content = {"x": Post(platform="x", angle="hot_take", text="t", parts=["t"])}
        return clips

    def render_one(job_dir, clip):
        calls["render"].append(clip.index)
        clip.stale, clip.version = False, clip.version + 1
        return clip

    return Steps(prepare=prepare, produce=produce, render_one=render_one), calls


def test_review_flow_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, calls = _fake_steps(tmp_path)
    with TestClient(create_app(steps=steps)) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x", "num_clips": 2}).json()["id"]
        job = _wait_status(c, jid, "review")
        assert len(job["clips"]) == 4 and [x["approved"] for x in job["clips"]] == [True, True, False, False]
        assert c.get(f"/api/jobs/{jid}/transcript").json()[0]["text"] == "hello world"

        # edit + approve + add a custom clip
        r = c.patch(f"/api/jobs/{jid}/clips/1", json={"title": "Better title", "end": 38})
        assert r.status_code == 200 and r.json()["clips"][0]["title"] == "Better title"
        assert c.patch(f"/api/jobs/{jid}/clips/3", json={"approved": True}).status_code == 200
        assert c.patch(f"/api/jobs/{jid}/clips/2", json={"end": 41.5}).status_code == 400  # < 3 s
        r = c.post(f"/api/jobs/{jid}/clips", json={"start": 70, "end": 85, "title": "Mine"})
        assert r.status_code == 201 and any(x["custom"] for x in r.json()["clips"])

        c.post(f"/api/jobs/{jid}/produce")
        job = _wait_status(c, jid, "done")
        rendered = [x for x in job["clips"] if x["file"]]
        assert len(rendered) == 4  # 3 approved + custom; clip 4 not approved
        assert not next(x for x in job["clips"] if x["index"] == 4)["file"]

        # change render options -> stale -> re-render single clip
        r = c.patch(f"/api/jobs/{jid}/clips/1", json={"options": {"jumpcut": False, "reframe": "center"}})
        assert r.json()["clips"][0]["stale"] is True
        c.post(f"/api/jobs/{jid}/clips/1/render")
        job = _wait_status(c, jid, "done", lambda j: not j["clips"][0]["stale"])
        assert calls["render"] == [1] and job["clips"][0]["version"] == 2

        # title isn't burned in -> still fresh; the hook title card is -> stale
        assert c.patch(f"/api/jobs/{jid}/clips/1", json={"title": "New"}).json()["clips"][0]["stale"] is False
        assert c.patch(f"/api/jobs/{jid}/clips/1", json={"hook": "Wait for it"}).json()["clips"][0]["stale"] is True

        # edit post text + status
        body = {"parts": ["first", " ", "second"], "status": "approved"}
        r = c.patch(f"/api/jobs/{jid}/clips/1/content/x", json=body)
        assert r.status_code == 200 and r.json()["parts"] == ["first", "second"] and r.json()["edited"]
        assert r.json()["status"] == "approved"
        assert c.patch(f"/api/jobs/{jid}/clips/1/content/x", json={"status": "bogus"}).status_code == 400
        posted = c.patch(f"/api/jobs/{jid}/clips/1/content/x", json={"status": "posted"}).json()
        assert posted["status"] == "posted" and posted["text"] == "first\n\n---\n\nsecond"

        # producing again only touches what's new (approve clip 4)
        c.patch(f"/api/jobs/{jid}/clips/4", json={"approved": True})
        c.post(f"/api/jobs/{jid}/produce")
        job = _wait_status(c, jid, "done", lambda j: bool(next(x for x in j["clips"] if x["index"] == 4)["file"]))
        x1 = next(x for x in job["clips"] if x["index"] == 1)
        assert x1["content"]["x"]["status"] == "posted"  # hand edits survive


def test_edits_blocked_while_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    app = create_app(steps=_fake_steps(tmp_path)[0], start_worker=False)
    with TestClient(app) as c:
        jid = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=x"}).json()["id"]  # stays queued
        assert c.patch(f"/api/jobs/{jid}/clips/1", json={"title": "x"}).status_code == 409


def _wait_status(client, job_id, status, cond=lambda j: True, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == status and cond(job):
            return job
        if job["status"] == "failed":
            raise AssertionError(job["error"])
        time.sleep(0.05)
    raise AssertionError(f"never reached {status}: {job['status']} {job.get('error')}")


# ---------------------------------------------------------------- deleting a job while the worker runs it
#
# Bug: DELETE /api/jobs/{id} doesn't check whether the job is busy. If the worker is mid-task, its next
# store.update() raises KeyError, the except-handler's store.update() raises again, and the exception escapes
# Worker._loop (try/finally only) — the worker thread dies and every later job stays "queued" forever.
#
# Fix: the API refuses to delete queued/running jobs (409), and the worker survives a job vanishing anyway.

def _deleting_steps(store):
    """Steps whose every phase deletes its own job mid-flight (simulates a concurrent DELETE)."""
    from app.pipeline.orchestrator import Steps

    def _delete(job_dir):
        store.delete(job_dir.name)

    def prepare(source, job_dir, num_clips, report, candidates=None):
        _delete(job_dir)
        report(Stage.transcribing, 0.5, "still working")
        return [Clip(index=1, start=0, end=20, title="c", hook="h", score=5)]

    def run(source, job_dir, num_clips, report):
        return prepare(source, job_dir, num_clips, report)

    def produce(job_dir, clips, report):
        _delete(job_dir)
        report(Stage.rendering, 0.5, "still rendering")
        return clips

    def render_one(job_dir, clip):
        _delete(job_dir)
        return clip

    return Steps(prepare=prepare, produce=produce, render_one=render_one, run=run)


@pytest.mark.parametrize("kind,mode", [("run", "review"), ("run", "auto"), ("produce", "review"),
                                       ("render", "review")])
def test_worker_task_survives_job_deleted_mid_run(tmp_path, kind, mode):
    from app.jobs import JobStore, Worker
    from app.models import JobMode, JobStatus

    store = JobStore(tmp_path)
    worker = Worker(store)
    worker.steps = _deleting_steps(store)
    job = store.create("https://youtube.com/watch?v=x", 1, JobMode(mode))
    if kind != "run":
        store.update(job.id, status=JobStatus.review,
                     clips=[Clip(index=1, start=0, end=20, title="c", hook="h", score=5)])

    # Must not raise: an exception here escapes Worker._loop and kills the worker thread.
    worker._run(kind, job.id, source="https://youtube.com/watch?v=x", index=1, restore=JobStatus.review)

    assert store.get(job.id) is None, "a deleted job must not be resurrected by the worker"
    assert not store.dir(job.id).exists(), "worker left the deleted job's folder behind"


def test_delete_running_job_keeps_worker_alive(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    normal_prepare = steps.prepare
    started, proceed = threading.Event(), threading.Event()

    def prepare(source, job_dir, num_clips, report, candidates=None):
        if "block" in source:
            started.set()
            proceed.wait(5)
        return normal_prepare(source, job_dir, num_clips, report, candidates)

    steps.prepare = prepare
    app = create_app(steps=steps)
    with TestClient(app) as c:
        try:
            a = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=block", "num_clips": 1}).json()["id"]
            assert started.wait(5), "worker never picked up job A"
            r = c.delete(f"/api/jobs/{a}")
            assert r.status_code == 409 and "busy" in r.json()["detail"]
        finally:
            proceed.set()

        _wait_status(c, a, "review")  # refused while busy → job A finishes normally
        assert c.delete(f"/api/jobs/{a}").status_code == 204  # …and can be deleted once it's idle
        assert c.get(f"/api/jobs/{a}").status_code == 404

        # The worker must still be alive and processing the queue.
        b = c.post("/api/jobs", json={"url": "https://youtube.com/watch?v=next", "num_clips": 1}).json()["id"]
        _wait_status(c, b, "review")
        assert app.state.worker._thread.is_alive()


# ---------------------------------------------------------------- "new angle" vs concurrent edits
#
# Seen in a real session: a clip 2 PATCH landed while a "new angle" for clip 1 was generating (~15 s on gemma4).
# The endpoint saved a job snapshot taken *before* the Gemma call, wiping edits made meanwhile, and released
# GPU_LOCK before saving, so a produce queued meanwhile could overwrite the new post with its own old snapshot.

def _done_job_with_posts(store):
    import json as _json

    from app.models import JobStatus, Post

    job = store.create("https://youtube.com/watch?v=x", 2)
    d = store.dir(job.id)
    seg = {"start": 0, "end": 90, "text": "Compound interest.", "words": []}
    (d / "transcript.json").write_text(_json.dumps([seg]))
    li = Post(platform="linkedin", angle="lesson", text="old linkedin")
    clips = [Clip(index=i, start=i * 30, end=i * 30 + 25, title=f"T{i}", hook="H", score=8, file=f"short_{i}.mp4",
                  content={"linkedin": li, "x": Post(platform="x", angle="hot_take", text="old x", parts=["old x"])})
             for i in (1, 2)]
    for c in clips:
        (d / c.file).write_bytes(b"v")
    store.update(job.id, status=JobStatus.done, clips=clips)
    return job.id


def test_new_angle_keeps_edits_made_while_gemma_is_writing(tmp_path, monkeypatch):
    from .conftest import SchemaFakeLLM

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    holder = {}

    class MeddlingLLM(SchemaFakeLLM):
        """While 'Gemma' writes, other requests edit the same job (another clip, and another post of this clip)."""

        def chat_json(self, prompt, schema, temperature=None, think=None):
            store, jid = holder["store"], holder["jid"]
            store.update_clip(jid, 2, lambda c: setattr(c, "title", "edited meanwhile"))
            store.update_clip(jid, 1, lambda c: setattr(c.content["linkedin"], "status", "posted"))
            return super().chat_json(prompt, schema, temperature)

    app = create_app(start_worker=False, llm_factory=MeddlingLLM)
    holder["store"] = app.state.store
    holder["jid"] = jid = _done_job_with_posts(app.state.store)

    with TestClient(app) as c:
        r = c.post(f"/api/jobs/{jid}/clips/1/content/x")
        assert r.status_code == 200
        clips = {x["index"]: x for x in c.get(f"/api/jobs/{jid}").json()["clips"]}
        assert clips[1]["content"]["x"]["text"] != "old x"  # the new angle landed
        assert clips[2]["title"] == "edited meanwhile"  # …without wiping the edit to clip 2
        assert clips[1]["content"]["linkedin"]["status"] == "posted"  # …or to another post of clip 1


def test_new_angle_on_a_job_deleted_meanwhile_is_404_and_leaves_no_files(tmp_path, monkeypatch):
    from .conftest import SchemaFakeLLM

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    holder = {}

    class DeletingLLM(SchemaFakeLLM):
        def chat_json(self, prompt, schema, temperature=None, think=None):
            holder["store"].delete(holder["jid"])
            return super().chat_json(prompt, schema, temperature)

    app = create_app(start_worker=False, llm_factory=DeletingLLM)
    holder["store"] = app.state.store
    holder["jid"] = jid = _done_job_with_posts(app.state.store)

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(f"/api/jobs/{jid}/clips/1/content/instagram")  # instagram also writes carousel PNGs
        assert r.status_code == 404
    assert not app.state.store.dir(jid).exists(), "carousel files re-created the deleted job's folder"


def test_produce_queued_during_new_angle_does_not_overwrite_it(tmp_path, monkeypatch):
    from app.jobs import GPU_LOCK

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    app = create_app(steps=steps)
    store = app.state.store
    jid = _done_job_with_posts(store)

    with TestClient(app) as c:
        GPU_LOCK.acquire()  # a "new angle" request is generating
        try:
            assert c.post(f"/api/jobs/{jid}/produce").status_code == 200
            _wait_status(c, jid, "running")  # worker picked the task up and is waiting for the GPU
            # …the new angle finishes and saves while it still holds the GPU
            store.update_clip(jid, 1, lambda cl: setattr(cl.content["x"], "text", "new angle"))
        finally:
            GPU_LOCK.release()
        job = _wait_status(c, jid, "done")
        assert next(x for x in job["clips"] if x["index"] == 1)["content"]["x"]["text"] == "new angle"


# ---------------------------------------------------------------- retry a failed job

def test_retry_failed_job_reuses_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    steps, _ = _fake_steps(tmp_path)
    normal_prepare, seen = steps.prepare, []

    def flaky_prepare(source, job_dir, num_clips, report, candidates=None):
        seen.append(source)
        if len(seen) == 1:
            (job_dir / "source.mp4").write_bytes(b"downloaded")  # got this far, then failed
            raise RuntimeError("401 Client Error: expired token")
        assert (job_dir / "source.mp4").read_bytes() == b"downloaded"  # same folder, download kept
        return normal_prepare(source, job_dir, num_clips, report, candidates)

    steps.prepare = flaky_prepare
    with TestClient(create_app(steps=steps)) as c:
        url = "https://youtube.com/watch?v=x"
        jid = c.post("/api/jobs", json={"url": url, "num_clips": 1}).json()["id"]
        assert _wait(c, jid)["status"] == "failed"

        r = c.post(f"/api/jobs/{jid}/retry")
        assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["error"] is None
        job = _wait_status(c, jid, "review")
        assert seen == [url, url] and job["clips"]
        assert c.post(f"/api/jobs/{jid}/retry").status_code == 409  # only failed jobs


def test_retry_upload_uses_saved_file_or_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    seen = []

    def failing(source, job_dir, num_clips, report):
        seen.append(source)
        raise RuntimeError("boom")

    app = create_app(pipeline=failing)
    with TestClient(app) as c:
        r = c.post("/api/jobs/upload", files={"file": ("talk.mp4", b"data", "video/mp4")},
                   data={"num_clips": "1", "mode": "auto"})
        jid = r.json()["id"]
        _wait(c, jid)
        c.post(f"/api/jobs/{jid}/retry")
        _wait(c, jid)
        saved = str(app.state.store.dir(jid) / "source.mp4")
        assert seen == [saved, saved]  # not the bare filename "talk.mp4"

        (app.state.store.dir(jid) / "source.mp4").unlink()
        r = c.post(f"/api/jobs/{jid}/retry")
        assert r.status_code == 409 and "upload it again" in r.json()["detail"]
        assert c.post("/api/jobs/nope/retry").status_code == 404
