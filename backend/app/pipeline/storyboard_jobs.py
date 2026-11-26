"""Single-process background queue for per-draft storyboard image generation."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from ..files import atomic_output
from ..models import VisualPrompt
from . import script_short, visuals

log = logging.getLogger(__name__)
_PENDING = {"queued", "running"}


class StoryboardJobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="storyboard")
        self._lock = Lock()
        self._active = set()

    def _path(self, draft_id):
        if not re.fullmatch(r"[0-9a-f]{10}", draft_id):
            raise KeyError(draft_id)
        return script_short.root() / "_image_jobs" / f"{draft_id}.json"

    def _write(self, path, record):
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path) as staged:
            staged.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def get(self, draft_id):
        with self._lock:
            path = self._path(draft_id)
            if not path.is_file():
                return None
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["status"] in _PENDING and path not in self._active:
                for item in record["scenes"]:
                    if item["status"] in _PENDING:
                        item.update(status="failed", error="ClipForge restarted. Retry the unfinished scenes.")
                record["status"] = "failed"
                self._write(path, record)
            return record

    def submit(self, draft_id, indexes, run):
        path = self._path(draft_id)
        draft = next((item for item in script_short.drafts() if item["id"] == draft_id), None)
        if draft is None:
            raise KeyError(draft_id)
        scenes = draft.get("storyboard", [])
        if not indexes or len(set(indexes)) != len(indexes) or any(not 0 <= idx < len(scenes) for idx in indexes):
            raise ValueError("Choose distinct scenes from this draft")
        reference = draft.get("visual_reference", "").strip() if draft.get("reference_image") else ""
        anchor = f"Visual continuity: {reference}. Scene: " if reference else ""
        requested = [VisualPrompt(prompt=anchor + scenes[index]["prompt"].strip()) for index in indexes]
        with self._lock:
            if path in self._active or len(self._active) >= 8:
                raise RuntimeError("Storyboard generation is already queued or its queue is full.")
            record = {"id": uuid4().hex, "status": "queued", "scenes": [
                {"index": index, "line": scenes[index]["line"], "prompt": scenes[index]["prompt"], "status": "queued"}
                for index in indexes
            ]}
            self._write(path, record)
            self._active.add(path)
            self._pool.submit(self._process, path, record, requested, run)
            return json.loads(json.dumps(record))

    def _process(self, path, record, requested, run):
        def progress(index, result):
            with self._lock:
                scene = record["scenes"][index]
                if result is None:
                    scene["status"] = "running"
                elif result.get("id"):
                    scene.update(status="ready", image_id=result["id"])
                else:
                    scene.update(status="failed", error=result["error"])
                self._write(path, record)

        def generate():
            with self._lock:
                record["status"] = "running"
                self._write(path, record)
            return visuals.generate(requested, progress=progress)

        message = "An earlier scene failed. Retry these scenes when ready."
        try:
            run(generate)
        except Exception as error:
            log.exception("Storyboard background generation failed")
            message = getattr(error, "detail", None) or str(error) or "Image generation failed."
        finally:
            with self._lock:
                try:
                    for scene in record["scenes"]:
                        if scene["status"] in _PENDING:
                            scene.update(status="failed", error=message)
                    complete = all(scene["status"] == "ready" for scene in record["scenes"])
                    record["status"] = "ready" if complete else "failed"
                    self._write(path, record)
                finally:
                    self._active.discard(path)
