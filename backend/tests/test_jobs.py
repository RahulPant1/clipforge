from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from app.config import settings
from app.jobs import GPU_LOCK, JobStore, QueueConflict, Worker
from app.models import Clip, JobMode, JobStatus, NarrationBatch, NarrationItem, Post, Stage


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


def test_restart_preserves_ready_narrations_and_makes_interrupted_ones_retryable(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("source", 2)
    batch = NarrationBatch(restore=JobStatus.done, items={
        1: NarrationItem(status="ready", progress=1),
        2: NarrationItem(status="running", progress=0.5),
    })
    store.update(job.id, status=JobStatus.running, stage=Stage.narrating, narration=batch)

    restarted = JobStore(tmp_path).get(job.id)
    assert restarted.status == JobStatus.done
    assert restarted.narration.items[1].status == "ready"
    assert restarted.narration.items[2].status == "failed"


@pytest.mark.parametrize("mode,purpose,step", [
    (JobMode.review, "create", "prepare"), (JobMode.auto, "create", "run"), (JobMode.review, "learn", "learn"),
])
def test_saved_job_model_reaches_transcription_entry_points(tmp_path, mode, purpose, step):
    from app.pipeline.orchestrator import Steps

    seen = []

    def run(*args, whisper_model, **kwargs):
        seen.append(whisper_model)
        return []

    store = JobStore(tmp_path)
    job = store.create("source", 1, mode=mode, purpose=purpose, whisper_model="large-v3-turbo")
    reloaded = JobStore(tmp_path)
    worker = Worker(reloaded, steps=Steps(**{step: run}))
    worker._run("run", job.id, source=job.source)
    assert seen == ["large-v3-turbo"]
    assert reloaded.get(job.id).status in (JobStatus.review, JobStatus.done)


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
