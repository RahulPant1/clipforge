"""File-backed job store + a single background worker.

One worker on purpose: Whisper and Gemma share one laptop GPU, so jobs run one at a time.
"""
import json
import logging
import queue
import shutil
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import settings
from .files import atomic_output
from .models import Clip, Job, JobMode, JobStatus, NarrationBatch, NarrationItem, Stage, WhisperChoice

log = logging.getLogger(__name__)

PipelineFn = Callable[..., list[Clip]]

# Serializes model work. Legacy pipelines and auto jobs hold it for their entire run.
GPU_LOCK = threading.Lock()


class QueueConflict(Exception):
    """The job no longer has a status that allows this operation."""


def video_title(job_dir: Path) -> tuple[str, str]:
    """(title, channel) from the job's meta.json (written by download), or ("", "")."""
    try:
        meta = json.loads((job_dir / "meta.json").read_text())
    except (OSError, ValueError):
        return "", ""
    return " ".join(str(meta.get("title") or "").split())[:300], " ".join(str(meta.get("channel") or "").split())[:200]


class JobStore:
    def __init__(self, root: Path):
        self.root = root / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # Change feed for SSE: every save/delete bumps `version`; listeners wait on the condition.
        self._changed = threading.Condition(self._lock)
        self.version = 0
        self._seen: dict[str, int] = {}  # job id -> version of its last change
        self._deleted: dict[str, int] = {}  # job id -> version it was deleted at
        self.listeners: list[Callable[[str], None]] = []  # called with the job id after every save/delete
        self._load()

    def _load(self) -> None:
        for f in self.root.glob("*/job.json"):
            try:
                job = Job.model_validate_json(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.exception("Skipping unreadable job record %s", f)
                continue
            if job.status in (JobStatus.queued, JobStatus.running) and job.stage == Stage.narrating and job.narration:
                job.narration = job.narration.fail_pending("Interrupted by server restart")
                job.status = job.narration.restore
                job.message = "Narration interrupted — retry failed shorts"
                self._write(job)
            elif job.status == JobStatus.queued and not job.clips:
                job.message = "Queued (kept across a server restart)"  # never started: create_app re-submits it
                self._write(job)
            elif job.status in (JobStatus.queued, JobStatus.running):
                if job.clips:  # interrupted while producing/rendering — your edits are kept
                    job.status, job.message = JobStatus.review, "Interrupted by server restart — run again"
                else:
                    job.status, job.error = JobStatus.failed, "Interrupted by server restart"
                self._write(job)
            if not job.title:  # jobs from before titles: take it from the download's meta.json (saved on next change)
                job.title, job.channel = video_title(f.parent)
            self._jobs[job.id] = job

    def waiting(self) -> list[Job]:
        """New jobs that never started (a batch queue), oldest first — the worker's queue is lost on restart."""
        return sorted((j for j in self.list() if j.status == JobStatus.queued and not j.clips),
                      key=lambda j: j.created_at)

    def _bump(self, job_id: str, deleted: bool = False) -> None:
        """Record a change (caller holds the lock) and wake the event streams."""
        self.version += 1
        (self._deleted if deleted else self._seen)[job_id] = self.version
        if not deleted:
            self._deleted.pop(job_id, None)
        self._changed.notify_all()
        for fn in self.listeners:  # must be quick (they run under the store lock): Library.touch just queues
            fn(job_id)

    def changes(self, since: int, timeout: float) -> tuple[int, list[Job], list[str]]:
        """Block until something changed after version `since` (or timeout).
        Returns (version, jobs changed since — latest state only, ids deleted since)."""
        with self._changed:
            self._changed.wait_for(lambda: self.version > since, timeout=timeout)
            jobs = [self._jobs[i] for i, v in self._seen.items() if v > since and i in self._jobs]
            gone = [i for i, v in self._deleted.items() if v > since]
            return self.version, jobs, gone

    def dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _write(self, job: Job) -> None:
        d = self.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        with atomic_output(d / "job.json") as staged:
            staged.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    def _save(self, job: Job) -> None:
        """Persist before updating memory and notifying listeners. Caller holds the lock."""
        self._write(job)
        self._jobs[job.id] = job
        self._bump(job.id)

    def create(self, source: str, num_clips: int, mode: JobMode = JobMode.review, topic: str = "",
               hinglish: bool = False, title: str = "", channel: str = "", purpose: str = "create",
               whisper_model: WhisperChoice | None = None) -> Job:
        from .pipeline.highlights import clean_topic

        job = Job(id=uuid.uuid4().hex[:10], source=source, num_clips=num_clips, mode=mode, topic=clean_topic(topic),
                  hinglish=hinglish, title=" ".join(title.split())[:300], channel=" ".join(channel.split())[:200],
                  purpose=purpose, whisper_model=whisper_model)
        with self._lock:
            self._save(job)
        return job

    def update(self, job_id: str, **fields) -> Job:
        with self._lock:
            job = self._jobs[job_id].model_copy(update={**fields, "updated_at": datetime.now(UTC)})
            self._save(job)
            return job

    def queue_job(self, job_id: str, allowed: tuple[JobStatus, ...], *, expected: Job | None = None,
                  **fields) -> tuple[Job, JobStatus]:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None or current.status not in allowed or (expected is not None and current is not expected):
                raise QueueConflict("Job status changed. Refresh and try again.")
            job = current.model_copy(update={
                **fields, "status": JobStatus.queued, "error": None, "updated_at": datetime.now(UTC),
            })
            self._save(job)
            return job, current.status

    def update_clip(self, job_id: str, index: int, fn: Callable[[Clip], None]) -> Job:
        """Apply fn to a copy of one clip and save the job atomically."""
        with self._lock:
            job = self._jobs[job_id]
            clips = [c.model_copy(deep=True) for c in job.clips]
            target = next((c for c in clips if c.index == index), None)
            if target is None:
                raise KeyError(index)
            fn(target)
            job = job.model_copy(update={"clips": clips, "updated_at": datetime.now(UTC)})
            self._save(job)
            return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
            self._seen.pop(job_id, None)
            shutil.rmtree(self.dir(job_id), ignore_errors=True)
            self._bump(job_id, deleted=True)


class Worker:
    """Runs one task at a time. Task kinds:
    run      — new job: auto mode = everything; review mode = prepare (stops at "review")
    narrate  — speak approved scripts and activate successful takes
    produce  — posts + renders for approved clips
    render   — re-render one clip after you changed it
    """

    def __init__(self, store: JobStore, pipeline: PipelineFn | None = None, steps=None, llm_factory=None):
        from .pipeline.llm import OllamaClient
        from .pipeline.orchestrator import Steps

        self.store = store
        self.llm_factory = llm_factory or OllamaClient
        self.steps = steps or Steps()
        if pipeline is not None:  # legacy/tests: a single callable for auto mode
            self.steps.run = pipeline
        self.q: queue.Queue[tuple[str, str, dict]] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clipforge-worker")

    def start(self) -> None:
        self._thread.start()

    def submit(self, job_id: str, source: str) -> None:
        self.q.put(("run", job_id, {"source": source}))

    def submit_produce(self, job: Job) -> Job:
        return self._submit_editable("produce", job, "Queued: rendering and posts")

    def submit_learn(self, job: Job) -> Job:
        """Study notes for a job that already has its video (learn.py); the job goes back to where it was."""
        return self._submit_editable("learn", job, "Queued: study notes")

    def submit_render(self, job: Job, index: int) -> Job:
        return self._submit_editable("render", job, f"Queued: re-render short {index}", index=index)

    def submit_narration(self, job: Job, batch: NarrationBatch) -> Job:
        queued, prev = self.store.queue_job(job.id, (JobStatus.review, JobStatus.done), expected=job,
                                            narration=batch, message="Queued: narration",
                                            stage=Stage.narrating, progress=0.0)
        self.q.put(("narrate", job.id, {"restore": prev}))
        return queued

    def _narrate(self, job: Job, directory: Path) -> None:
        from .pipeline import dub

        batch = job.narration
        items = dict(batch.items)
        pending = [index for index, item in items.items() if item.status == "queued"]
        clips = {clip.index: clip for clip in job.clips}
        try:
            self.llm_factory().unload()
        except Exception:
            log.debug("Could not unload the language model before narration", exc_info=True)

        def progress(index: int, value: float, message: str) -> None:
            items[index] = NarrationItem(status="running", progress=value, message=message)
            fraction = sum(1 if items[i].status == "failed" else items[i].progress for i in pending) / len(pending)
            self.store.update(job.id, narration=batch.model_copy(update={"items": items.copy()}),
                              progress=fraction, message=f"Short {index}: {message}")

        requested = []
        for index in pending:
            clip = clips[index].model_copy(deep=True)
            duration = batch.duration_for(index)
            clip.options = clip.options.model_copy(update={
                "dub": True, "dub_pacing": duration.pacing, "dub_ambience": batch.ambience,
                "dub_ambience_level": batch.ambience_level, "dub_smart_max_seconds": duration.smart_max_seconds,
            })
            requested.append(clip)
        for clip, error in dub.generate_batch(directory, requested, progress):
            index = clip.index
            if error is not None:
                log.error("Narration failed for job %s short %s: %s", job.id, index, error)
                items[index] = NarrationItem(status="failed", message=str(error)[:300] or "Narration failed")
            else:
                clip.stale = bool(clip.file)
                clips[index] = clip
                items[index] = NarrationItem(status="ready", progress=1, message="Ready to render")
            self.store.update(job.id, clips=list(clips.values()),
                              narration=batch.model_copy(update={"items": items.copy()}))
        failed = sum(item.status == "failed" for item in items.values())
        self.store.update(job.id, status=batch.restore, progress=1.0,
                          message=f"{failed} narration(s) failed" if failed else "Narration ready")

    def _submit_editable(self, kind: str, job: Job, message: str, **kwargs) -> Job:
        queued, prev = self.store.queue_job(job.id, (JobStatus.review, JobStatus.done), expected=job, message=message,
                                            stage=Stage.queued, progress=0.0)
        self.q.put((kind, job.id, {**kwargs, "restore": prev}))
        return queued

    def _loop(self) -> None:
        while True:
            kind, job_id, kw = self.q.get()
            try:
                self._run(kind, job_id, **kw)
            except Exception:  # never let one task kill the only worker — later jobs would sit "queued" forever
                log.exception("worker: unhandled error in %s task for job %s", kind, job_id)
            finally:
                self.q.task_done()

    def _run(self, kind: str, job_id: str, source: str = "", index: int = 0,
             restore: JobStatus | None = None) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        before = restore
        try:
            self.store.update(job_id, status=JobStatus.running)
        except KeyError:  # cancelled (deleted) just as it came up in the queue
            return
        d = self.store.dir(job_id)

        def report(stage: Stage, progress: float, message: str) -> None:
            named = {}
            cur = self.store.get(job_id)
            if cur is not None and not cur.title:  # download just wrote meta.json → the Jobs list shows the title
                title, channel = video_title(d)
                if title:
                    named = {"title": title, "channel": channel}
            self.store.update(job_id, stage=stage, progress=round(progress, 3), message=message, **named)

        def publish(clip: Clip) -> None:
            current = self.store.get(job_id)
            if current is None:
                raise KeyError(job_id)
            snapshot = clip.model_copy(deep=True)
            clips = {c.index: c for c in current.clips}
            clips[snapshot.index] = snapshot
            self.store.update(job_id, clips=list(clips.values()))

        try:
            from .pipeline.orchestrator import produce, run_pipeline

            if kind == "produce" and self.steps.produce is produce:
                with GPU_LOCK:
                    job = self.store.get(job_id)
                    if job is None:
                        raise KeyError(job_id)
                    clips = [c.model_copy(deep=True) for c in job.clips]
                clips = produce(d, clips, report, on_clip=publish, model_lock=GPU_LOCK)
                self.store.update(job_id, status=JobStatus.done, stage=Stage.done, progress=1.0, clips=clips)
                return

            with GPU_LOCK:
                # Re-read under the lock: a "new angle" request may have saved a post while we waited for the GPU
                job = self.store.get(job_id)
                if job is None:
                    raise KeyError(job_id)  # deleted while queued → handled below like any vanished job
                # only passed when set, so step callables written before topics (tests, scripts) keep working
                ingest_options = {}
                if job.hinglish:
                    ingest_options["romanize"] = True
                if job.whisper_model:
                    ingest_options["whisper_model"] = job.whisper_model
                steer = {**ingest_options, **({"topic": job.topic} if job.topic else {})}
                if kind == "narrate":
                    self._narrate(job, d)
                elif kind == "learn" or (kind == "run" and job.purpose == "learn"):
                    self.steps.learn(source or job.source, d, report, **ingest_options)
                    done = JobStatus.done if kind == "run" else (before or JobStatus.done)
                    self.store.update(job_id, status=done, stage=Stage.done, progress=1.0, learn=True,
                                      message="Study notes ready")
                elif kind == "run" and job.mode == JobMode.auto:
                    if self.steps.run is run_pipeline:
                        steer["on_clip"] = publish
                    clips = self.steps.run(source, d, job.num_clips, report, **steer)
                    self.store.update(job_id, status=JobStatus.done, stage=Stage.done, progress=1.0, clips=clips)
                elif kind == "run":
                    from .pipeline.orchestrator import CANDIDATE_FACTOR

                    clips = self.steps.prepare(source, d, job.num_clips, report,
                                               candidates=job.num_clips * CANDIDATE_FACTOR, **steer)
                    self.store.update(job_id, status=JobStatus.review, stage=Stage.selecting, progress=1.0,
                                      clips=clips, message="Review the clips, then create shorts")
                elif kind == "produce":
                    clips = self.steps.produce(d, [c.model_copy(deep=True) for c in job.clips], report)
                    self.store.update(job_id, status=JobStatus.done, stage=Stage.done, progress=1.0, clips=clips)
                elif kind == "render":
                    report(Stage.rendering, 0, f"Re-rendering short {index}")
                    clip = next(c for c in job.clips if c.index == index).model_copy(deep=True)
                    message = f"Short {index} re-rendered"
                    try:
                        self.steps.render_one(d, clip)
                    except Exception as e:
                        clip.render_error = str(e)[:300]
                        message = f"Short {index} render failed"
                    clips = [clip if c.index == index else c for c in self.store.get(job_id).clips]
                    self.store.update(job_id, status=before or JobStatus.done, stage=Stage.done, progress=1.0,
                                      clips=clips, message=message)
        except Exception as e:
            if self.store.get(job_id) is None:  # deleted while running — drop it, don't resurrect its folder
                log.warning("job %s was deleted during %s; discarding its output", job_id, kind)
                shutil.rmtree(d, ignore_errors=True)
                return
            log.error("job %s %s failed\n%s", job_id, kind, traceback.format_exc())
            if kind == "narrate":
                batch = self.store.get(job_id).narration.fail_pending(str(e)[:300] or "Narration failed")
                self.store.update(job_id, status=batch.restore, narration=batch, error=str(e), message="")
            elif kind == "run":
                self.store.update(job_id, status=JobStatus.failed, error=str(e))
            else:  # keep the job reviewable — nothing you edited is lost
                self.store.update(job_id, status=before or JobStatus.review, error=str(e), message="")


def default_store() -> JobStore:
    return JobStore(settings.data_dir)
