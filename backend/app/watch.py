"""Watched folder: drop a video into WATCH_DIR and it becomes a job (off unless WATCH_DIR is set).

A file is taken once its size and modified time stop changing between two scans (still copying → wait).
It is copied into the job folder as source.mp4 — your file is left where it is. What was taken is remembered in
data/watched.json (name, size, mtime → job id), so restarts don't re-queue it; replacing a file with a new
version (different size or time) queues it again.
"""
import json
import logging
import shutil
import threading
from pathlib import Path

from .config import settings
from .models import JobMode, JobStatus

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
RECORD = "watched.json"


class Watcher:
    def __init__(self, store, submit, folder: Path | None = None, interval: float | None = None):
        self.store, self.submit = store, submit
        self.folder = Path(folder or settings.watch_dir) if (folder or settings.watch_dir) else None
        self.interval = interval if interval is not None else settings.watch_interval
        self.record = Path(settings.data_dir) / RECORD
        self.taken: dict[str, dict] = self._read()
        self._last: dict[str, tuple[int, float]] = {}  # name -> (size, mtime) seen on the previous scan
        self._stop = threading.Event()

    def _read(self) -> dict:
        try:
            return json.loads(self.record.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.record.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.record.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.taken, indent=1))
        tmp.replace(self.record)

    def start(self) -> None:
        if not self.folder:
            return
        log.info("watching %s for new videos (every %ss)", self.folder, self.interval)
        threading.Thread(target=self._loop, daemon=True, name="clipforge-watch").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:  # a flaky /mnt/c mount must not end the watcher
                log.exception("watch: scan failed")
            self._stop.wait(self.interval)

    def scan(self) -> list[str]:
        """One pass: queue every settled, not yet taken video. Returns the new job ids."""
        if not self.folder or not self.folder.is_dir():
            return []
        now: dict[str, tuple[int, float]] = {}
        for f in sorted(self.folder.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."):
                st = f.stat()
                now[f.name] = (st.st_size, st.st_mtime)
        made = []
        for name, (size, mtime) in now.items():
            done = self.taken.get(name)
            if done and done["size"] == size and done["mtime"] == mtime:
                continue
            if self._last.get(name) != (size, mtime) or size == 0:
                continue  # new or still being written: take it on a later scan if it stays the same
            made.append(self.take(self.folder / name, size, mtime))
        self._last = now
        return [m for m in made if m]

    def take(self, path: Path, size: int, mtime: float) -> str | None:
        job = self.store.create(path.name, settings.watch_clips, JobMode(settings.watch_mode), settings.watch_topic,
                                hinglish=settings.hinglish_default)
        self.taken[path.name] = {"size": size, "mtime": mtime, "job": job.id}
        self._save()
        try:
            d = self.store.dir(job.id)
            d.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, d / "source.mp4")
        except OSError as e:
            log.error("watch: can't copy %s: %s", path, e)
            self.store.update(job.id, status=JobStatus.failed, error=f"Couldn't copy from the watched folder: {e}")
            return None
        self.store.update(job.id, message=f"Queued from the watched folder: {path.name}")
        self.submit(job.id, str(d / "source.mp4"))
        log.info("watch: %s → job %s", path.name, job.id)
        return job.id
