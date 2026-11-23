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
from .models import Clip, Job, JobMode, JobStatus, Stage

log = logging.getLogger(__name__)

PipelineFn = Callable[..., list[Clip]]

# Held while anything uses the GPU (a whole job, or a single "new angle" regeneration)
GPU_LOCK = threading.Lock()


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
            job = Job.model_validate_json(f.read_text())
            if job.status == JobStatus.queued and not job.clips:
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
        (d / "job.json").write_text(job.model_dump_json(indent=2))

    def create(self, source: str, num_clips: int, mode: JobMode = JobMode.review, topic: str = "",
               hinglish: bool = False, title: str = "", channel: str = "", purpose: str = "create") -> Job:
        from .pipeline.highlights import clean_topic

        job = Job(id=uuid.uuid4().hex[:10], source=source, num_clips=num_clips, mode=mode, topic=clean_topic(topic),
                  hinglish=hinglish, title=" ".join(title.split())[:300], channel=" ".join(channel.split())[:200],
                  purpose=purpose)
        with self._lock:
            self._jobs[job.id] = job
            self._write(job)
            self._bump(job.id)
        return job

    def update(self, job_id: str, **fields) -> Job:
        with self._lock:
            job = self._jobs[job_id].model_copy(update={**fields, "updated_at": datetime.now(UTC)})
            self._jobs[job_id] = job
            self._write(job)
            self._bump(job_id)
            return job

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
            self._jobs[job_id] = job
            self._write(job)
            self._bump(job_id)
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
    produce  — posts + renders for approved clips
    render   — re-render one clip after you changed it
    """

    def __init__(self, store: JobStore, pipeline: PipelineFn | None = None, steps=None):
        from .pipeline.orchestrator import Steps

        self.store = store
        self.steps = steps or Steps()
        if pipeline is not None:  # legacy/tests: a single callable for auto mode
            self.steps.run = pipeline
        self.q: queue.Queue[tuple[str, str, dict]] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clipforge-worker")

    def start(self) -> None:
        self._thread.start()

    def submit(self, job_id: str, source: str) -> None:
        self.q.put(("run", job_id, {"source": source}))

    def submit_produce(self, job_id: str) -> None:
        prev = self.store.get(job_id).status
        self.store.update(job_id, status=JobStatus.queued, message="Queued: posts + rendering", error=None)
        self.q.put(("produce", job_id, {"restore": prev}))

    def submit_learn(self, job_id: str) -> None:
        """Study notes for a job that already has its video (learn.py); the job goes back to where it was."""
        prev = self.store.get(job_id).status
        self.store.update(job_id, status=JobStatus.queued, message="Queued: study notes", error=None)
        self.q.put(("learn", job_id, {"restore": prev}))

    def submit_render(self, job_id: str, index: int) -> None:
        prev = self.store.get(job_id).status
        self.store.update(job_id, status=JobStatus.queued, message=f"Queued: re-render short {index}", error=None)
        self.q.put(("render", job_id, {"index": index, "restore": prev}))

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

        try:
            with GPU_LOCK:
                # Re-read under the lock: a "new angle" request may have saved a post while we waited for the GPU
                job = self.store.get(job_id)
                if job is None:
                    raise KeyError(job_id)  # deleted while queued → handled below like any vanished job
                # only passed when set, so step callables written before topics (tests, scripts) keep working
                steer = {"topic": job.topic} if job.topic else {}
                if job.hinglish:
                    steer["romanize"] = True
                if kind == "learn" or (kind == "run" and job.purpose == "learn"):
                    self.steps.learn(source or job.source, d, report,
                                     **({"romanize": True} if job.hinglish else {}))
                    done = JobStatus.done if kind == "run" else (before or JobStatus.done)
                    self.store.update(job_id, status=done, stage=Stage.done, progress=1.0, learn=True,
                                      message="Study notes ready")
                elif kind == "run" and job.mode == JobMode.auto:
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
                    try:
                        self.steps.render_one(d, clip)
                    except Exception as e:
                        clip.render_error = str(e)[:300]
                    clips = [clip if c.index == index else c for c in self.store.get(job_id).clips]
                    self.store.update(job_id, status=before or JobStatus.done, stage=Stage.done, progress=1.0,
                                      clips=clips, message=f"Short {index} re-rendered")
        except Exception as e:
            if self.store.get(job_id) is None:  # deleted while running — drop it, don't resurrect its folder
                log.warning("job %s was deleted during %s; discarding its output", job_id, kind)
                shutil.rmtree(d, ignore_errors=True)
                return
            log.error("job %s %s failed\n%s", job_id, kind, traceback.format_exc())
            if kind == "run":
                self.store.update(job_id, status=JobStatus.failed, error=str(e))
            else:  # keep the job reviewable — nothing you edited is lost
                self.store.update(job_id, status=before or JobStatus.review, error=str(e), message="")


def default_store() -> JobStore:
    return JobStore(settings.data_dir)
