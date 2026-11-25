from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.config import settings
from app.jobs import GPU_LOCK, JobStore, QueueConflict, Worker
from app.models import Clip, JobStatus, Post


def test_failed_job_replace_keeps_saved_and_in_memory_state(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = store.create("source", 1)
    record = store.dir(job.id) / "job.json"
    previous = record.read_bytes()
    version = store.version

    def fail(self, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        store.update(job.id, status=JobStatus.done)

    assert record.read_bytes() == previous
    assert store.get(job.id) == job
    assert store.version == version
    assert list(record.parent.iterdir()) == [record]


def test_bad_job_record_does_not_block_startup(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("source", 1)
    store.update(job.id, status=JobStatus.done)
    bad = store.dir("bad") / "job.json"
    bad.parent.mkdir()
    bad.write_text("{")

    reopened = JobStore(tmp_path)

    assert [item.id for item in reopened.list()] == [job.id]
    assert bad.read_text() == "{"


def test_concurrent_submissions_claim_job_once(tmp_path):
    store = JobStore(tmp_path)
    worker = Worker(store)
    job = store.update(store.create("source", 1).id, status=JobStatus.done)
    start = Barrier(2)

    def submit(index):
        start.wait()
        try:
            worker.submit_render(job, index)
        except QueueConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [1, 2])) == [False, True]
    assert worker.q.qsize() == 1
    assert store.get(job.id).status == JobStatus.queued

    store.update(job.id, status=JobStatus.done)
    with pytest.raises(QueueConflict):
        worker.submit_render(job, 1)


def test_production_publishes_shorts_before_post_generation(tmp_path, monkeypatch):
    from app.pipeline import orchestrator
    from tests.conftest import FakeLLM

    store = JobStore(tmp_path)
    worker = Worker(store)
    clips = [Clip(index=i, start=0, end=5, title="t", hook="h", score=5, approved=True) for i in (1, 2)]
    job = store.update(store.create("source", 2).id, status=JobStatus.review, clips=clips)
    (store.dir(job.id) / "transcript.json").write_text("[]")
    monkeypatch.setattr(settings, "inserts_suggest", False)
    monkeypatch.setattr(settings, "content_enabled", True)
    monkeypatch.setattr(orchestrator, "OllamaClient", lambda: FakeLLM([]))
    monkeypatch.setattr(orchestrator, "cover_for", lambda *args: None)
    events = []

    def render(video, clip, segments, directory):
        assert not GPU_LOCK.locked()
        assert [c.index for c in store.get(job.id).clips if c.file] == list(range(1, clip.index))
        events.append(("render", clip.index))
        return f"short_{clip.index}.mp4"

    def posts(ctx, *args):
        current = store.get(job.id)
        assert GPU_LOCK.locked()
        assert current.status == JobStatus.running and all(clip.file for clip in current.clips)
        events.append(("posts", ctx.clip.index))
        return {"x": Post(platform="x", angle="a", text="generated")}

    monkeypatch.setattr(orchestrator, "render_clip", render)
    monkeypatch.setattr(orchestrator, "generate_all", posts)
    worker.submit_produce(job)
    kind, job_id, kwargs = worker.q.get_nowait()
    worker._run(kind, job_id, **kwargs)

    assert store.get(job.id).status == JobStatus.done
    assert events == [("render", 1), ("render", 2), ("posts", 1), ("posts", 2)]
