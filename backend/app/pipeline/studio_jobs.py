"""Persist Studio draft jobs independently of the client that starts them."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from ..files import atomic_output
from ..models import ScriptBrief, ScriptShortDraft
from . import script_short, storyboard

log = logging.getLogger(__name__)


class StudioDraftJobs:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio-draft")
        self._lock = Lock()
        self._active = set()

    def _path(self, ident):
        if not re.fullmatch(r"[a-f0-9]{10}", ident):
            raise KeyError(ident)
        return script_short.root() / "_studio_jobs" / f"{ident}.json"

    def _write(self, ident, record):
        path = self._path(ident)
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path) as staged:
            staged.write_text(json.dumps(record), encoding="utf-8")

    def submit(self, request: ScriptBrief, run):
        with self._lock:
            if len(self._active) >= 8:
                raise RuntimeError("The Studio draft queue is full.")
            ident = uuid4().hex[:10]
            record = {"id": ident, "status": "queued"}
            self._write(ident, record)
            self._active.add(ident)
            self._pool.submit(self._process, ident, request, run)
            return record

    def get(self, ident):
        with self._lock:
            path = self._path(ident)
            if not path.is_file():
                raise KeyError(ident)
            record = json.loads(path.read_text(encoding="utf-8"))
            if ident not in self._active and record["status"] in {"queued", "running"}:
                if any(item["id"] == ident for item in script_short.drafts()):
                    record.update(status="ready", draft_id=ident)
                else:
                    record.update(status="failed", error="ClipForge restarted during drafting. Submit again to retry.")
                self._write(ident, record)
            return record

    def outputs(self, ident):
        record = self.get(ident)
        if record["status"] != "ready":
            return {"id": ident, "status": record["status"], "items": []}
        draft = next((item for item in script_short.drafts() if item["id"] == ident), None)
        if draft is None:
            raise KeyError(ident)
        items = [{"kind": "draft", "path": str((script_short.root() / "_drafts" / f"{ident}.json").resolve())}]
        for export in script_short.recent():
            if export.get("draft_id") == ident:
                for filename in ("short.mp4", "captions.srt", "thumbnail.jpg", "youtube.txt", "youtube-package.zip"):
                    try:
                        path = script_short.file(export["id"], filename)
                    except KeyError:
                        continue
                    items.append({"kind": filename, "path": str(path.resolve())})
        return {"id": ident, "status": "ready", "title": draft["title"], "items": items}

    def _process(self, ident, request, run):
        try:
            self._write(ident, {"id": ident, "status": "running"})

            def create(model):
                script = storyboard.draft(request.brief, model, request.source_notes)
                scenes = storyboard.plan(script["text"], 4, model)
                return ScriptShortDraft(id=ident, title=script["title"], text=script["text"],
                                        storyboard=scenes, source_notes=request.source_notes)

            script_short.save_draft(run(create))
            record = {"id": ident, "status": "ready", "draft_id": ident}
        except Exception as error:
            log.exception("Studio draft generation failed")
            detail = getattr(error, "detail", None) or str(error)
            record = {"id": ident, "status": "failed", "error": detail[:400] or "Draft generation failed."}
        finally:
            with self._lock:
                self._active.discard(ident)
                self._write(ident, record)
