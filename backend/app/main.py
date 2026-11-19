"""FastAPI app. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""
import json
import logging
import re
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import settings
from .jobs import GPU_LOCK, JobStore, Worker
from .models import (
    Clip,
    ClipAdd,
    ClipEdit,
    Job,
    JobCreate,
    JobMode,
    JobStatus,
    Post,
    PostEdit,
    RenderOptions,
    Segment,
    Stage,
)
from .pipeline.content import ANGLES, ContentContext, clip_text, generate_post, load_meta, write_posts_md
from .pipeline.export import build_pack
from .pipeline.history import get_history
from .pipeline.llm import OllamaClient
from .pipeline.presets import POSITIONS, all_presets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SAFE_NAME = re.compile(r"^[\w.\-]+$")


def create_app(pipeline=None, start_worker: bool = True, llm_factory=OllamaClient, steps=None) -> FastAPI:
    store = JobStore(settings.data_dir)
    worker = Worker(store, pipeline, steps)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            worker.start()
        yield

    app = FastAPI(title="ClipForge", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.worker = store, worker
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health() -> dict:
        try:
            llm = OllamaClient().health()
        except Exception as e:
            llm = {"ok": False, "error": str(e)}
        return {"api": "ok", "ollama": llm, "whisper": settings.whisper_model,
                "encoder": settings.encoder}

    @app.get("/api/caption-presets")
    def caption_presets() -> dict:
        """Caption looks for the Shorts step (built-in + data/caption_presets.json) and the global defaults."""
        return {"default": settings.caption_preset, "position": settings.caption_position,
                "style": settings.caption_style, "positions": list(POSITIONS),
                "presets": [p.public() for p in all_presets().values()]}

    @app.post("/api/jobs", response_model=Job, status_code=201)
    def create_job(body: JobCreate) -> Job:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "url must start with http(s)://")
        job = store.create(body.url, body.num_clips, body.mode)
        worker.submit(job.id, body.url)
        return job

    @app.post("/api/jobs/upload", response_model=Job, status_code=201)
    def upload_job(file: UploadFile = File(...), num_clips: int = Form(5), mode: JobMode = Form(JobMode.review)) -> Job:
        job = store.create(file.filename or "upload.mp4", num_clips, mode)
        d = store.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / "source.mp4"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        worker.submit(job.id, str(dest))
        return job

    @app.post("/api/jobs/{job_id}/retry", response_model=Job)
    def retry_job(job_id: str) -> Job:
        """Run a failed job again in its own folder: a finished download / transcript is reused, not redone."""
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job.status != JobStatus.failed:
            raise HTTPException(409, "only failed jobs can be retried")
        if job.source.startswith(("http://", "https://")):
            source = job.source  # download() returns early if source.mp4 is already there
        else:
            upload = store.dir(job_id) / "source.mp4"
            if not upload.is_file():
                raise HTTPException(409, "the uploaded video is gone — upload it again as a new job")
            source = str(upload)
        job = store.update(job_id, status=JobStatus.queued, stage=Stage.queued, progress=0.0, error=None,
                           message="Queued: retry")
        worker.submit(job_id, source)
        return job

    @app.get("/api/jobs", response_model=list[Job])
    def list_jobs() -> list[Job]:
        return store.list()

    @app.get("/api/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str) -> Job:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return job

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str) -> None:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job.status in (JobStatus.queued, JobStatus.running):
            raise HTTPException(409, "job is busy — wait until it finishes before deleting it")
        store.delete(job_id)

    # ---------------------------------------------------------------- review & editing

    def editable(job_id: str) -> Job:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        if job.status not in (JobStatus.review, JobStatus.done):
            raise HTTPException(409, "job is busy — wait until it finishes")
        return job

    @app.get("/api/jobs/{job_id}/transcript")
    def get_transcript(job_id: str) -> list[dict]:
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        p = store.dir(job_id) / "transcript.json"
        if not p.exists():
            return []
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in json.loads(p.read_text())]

    @app.patch("/api/jobs/{job_id}/clips/{index}", response_model=Job)
    def edit_clip(job_id: str, index: int, body: ClipEdit) -> Job:
        editable(job_id)
        changes = body.model_dump(exclude_none=True)

        def apply(c: Clip) -> None:
            def burned_in(c: Clip) -> tuple:  # everything the rendered mp4 depends on
                return c.start, c.end, c.options.model_dump(), c.hook, c.keywords

            before = burned_in(c)
            for k, v in changes.items():
                setattr(c, k, type(c.options)(**v) if k == "options" else v)
            if c.end - c.start < 3 or c.end - c.start > 180:
                raise HTTPException(400, "clip must be between 3 and 180 seconds")
            if c.file and burned_in(c) != before:
                c.stale = True

        try:
            return store.update_clip(job_id, index, apply)
        except KeyError:
            raise HTTPException(404, "clip not found") from None

    @app.post("/api/jobs/{job_id}/clips", response_model=Job, status_code=201)
    def add_clip(job_id: str, body: ClipAdd) -> Job:
        job = editable(job_id)
        if not 3 <= body.end - body.start <= 180:
            raise HTTPException(400, "clip must be between 3 and 180 seconds")
        idx = max((c.index for c in job.clips), default=0) + 1
        clip = Clip(index=idx, start=body.start, end=body.end, title=body.title, hook=body.hook, score=5,
                    custom=True, approved=True)
        if (store.dir(job_id) / "signals.json").exists():  # no-speech job: same defaults as its picked clips
            clip.options = RenderOptions(jumpcut=False, caption_style="none")
        clips = sorted([*job.clips, clip], key=lambda c: c.start)
        return store.update(job_id, clips=clips)

    @app.post("/api/jobs/{job_id}/produce", response_model=Job)
    def produce_job(job_id: str) -> Job:
        """Write posts + render every approved clip that's missing or changed."""
        job = editable(job_id)
        if not any(c.approved for c in job.clips):
            raise HTTPException(400, "approve at least one clip first")
        worker.submit_produce(job_id)
        return store.get(job_id)

    @app.post("/api/jobs/{job_id}/clips/{index}/render", response_model=Job)
    def rerender_clip(job_id: str, index: int) -> Job:
        job = editable(job_id)
        if not any(c.index == index for c in job.clips):
            raise HTTPException(404, "clip not found")
        worker.submit_render(job_id, index)
        return store.get(job_id)

    @app.patch("/api/jobs/{job_id}/clips/{index}/content/{platform}", response_model=Post)
    def edit_post(job_id: str, index: int, platform: str, body: PostEdit) -> Post:
        editable(job_id)
        if body.status and body.status not in ("draft", "approved", "posted"):
            raise HTTPException(400, "status must be draft, approved or posted")
        out: dict[str, Post] = {}

        def apply(c: Clip) -> None:
            post = c.content.get(platform)
            if not post:
                raise HTTPException(404, "post not found — generate it first")
            if body.parts is not None:
                post.parts = [p.strip() for p in body.parts if p.strip()]
                if platform == "x":
                    post.text = "\n\n---\n\n".join(post.parts)
            if body.text is not None:
                post.text = body.text
            if body.title is not None:
                post.title = body.title
            if body.text is not None or body.parts is not None or body.title is not None:
                post.edited = True
            if body.status:
                post.status = body.status
            out["post"] = post

        try:
            job = store.update_clip(job_id, index, apply)
        except KeyError:
            raise HTTPException(404, "clip not found") from None
        write_posts_md(next(c for c in job.clips if c.index == index), store.dir(job_id))
        return out["post"]

    @app.post("/api/jobs/{job_id}/clips/{index}/content/{platform}", response_model=Post)
    def regenerate_post(job_id: str, index: int, platform: str) -> Post:
        """New creative angle for one platform of one clip (variety engine picks an unused angle)."""
        job = store.get(job_id)
        if not job or job.status != JobStatus.done:
            raise HTTPException(404, "finished job not found")
        if platform not in ANGLES:
            raise HTTPException(400, f"platform must be one of {list(ANGLES)}")
        clip = next((c for c in job.clips if c.index == index), None)
        if not clip:
            raise HTTPException(404, "clip not found")
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
            try:
                segs = [Segment(**x) for x in json.loads((d / "transcript.json").read_text())]
                meta = load_meta(d)
                ctx = ContentContext(clip=clip, text=clip_text(segs, clip), video_title=meta.get("title", ""),
                                     channel=meta.get("channel", ""), url=meta.get("url", ""))
                old = clip.content.get(platform)
                post = generate_post(platform, ctx, llm_factory(), get_history(), d,
                                     exclude_angle=old.angle if old else None)
            except Exception as e:
                if store.get(job_id) is None:
                    raise KeyError(job_id) from e  # deleted while generating — not Gemma's fault
                raise HTTPException(502, f"Gemma failed: {e}") from e
            # Save onto the job as it is NOW (other edits may have landed during the ~15 s Gemma call), and
            # while still holding GPU_LOCK, so a produce queued meanwhile reads the job only after this write.
            job = store.update_clip(job_id, index, lambda c: c.content.__setitem__(platform, post))
        except KeyError:
            if store.get(job_id) is not None:
                raise HTTPException(404, "clip not found") from None
            shutil.rmtree(d, ignore_errors=True)  # carousel PNGs may have re-created the deleted job's folder
            raise HTTPException(404, "job was deleted while the new post was being written") from None
        finally:
            GPU_LOCK.release()

        write_posts_md(next(c for c in job.clips if c.index == index), d)
        return post

    @app.get("/api/jobs/{job_id}/export.zip")
    def export_pack(job_id: str) -> FileResponse:
        job = store.get(job_id)
        if not job or job.status != JobStatus.done:
            raise HTTPException(404, "finished job not found")
        path = build_pack(job, store.dir(job_id))
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @app.get("/api/jobs/{job_id}/files/{name}")
    def get_file(job_id: str, name: str) -> FileResponse:
        if not SAFE_NAME.match(name) or not store.get(job_id):
            raise HTTPException(404, "not found")
        path = store.dir(job_id) / name
        if not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path)

    return app


app = create_app()
