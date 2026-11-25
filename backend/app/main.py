"""FastAPI app. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""
import base64
import json
import logging
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import BinaryIO

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import settings
from .events import job_events
from .jobs import GPU_LOCK, JobStore, QueueConflict, Worker
from .library import XLSX, Library, video_key, windows_path
from .models import (
    BatchCreate,
    BatchLinks,
    BrandKit,
    BrandVoice,
    BrollPlacement,
    Clip,
    ClipAdd,
    ClipEdit,
    CompareRequest,
    CoverEdit,
    DubBatchDraft,
    DubEdit,
    DubGenerate,
    DubLine,
    DubScript,
    ExportPreviewRange,
    Insert,
    Job,
    JobCreate,
    JobMode,
    JobStatus,
    NarrationBatch,
    NarrationItem,
    NarrationOptions,
    NarrationQueue,
    Post,
    PostEdit,
    ProduceRequest,
    PronunciationDictionary,
    Purpose,
    QuoteEdit,
    RecordingEdit,
    RenderOptions,
    ReplaceWords,
    Retry,
    ScriptChoice,
    Segment,
    SmartJudge,
    SmartTopic,
    SpeakRequest,
    Stage,
    Vocabulary,
    VoiceBuild,
    VoiceCreate,
    VoiceUpdate,
    WhisperChoice,
    WordEdit,
)
from .pipeline import (
    batch,
    brand,
    broll,
    cover,
    discover,
    dub,
    emoji,
    hinglish,
    inserts,
    judge,
    learn,
    quote,
    revoice,
    scenes,
    sfx,
    transcript_edit,
    tts,
    vocabulary,
    voice,
    waveform,
)
from .pipeline.ambience import available as ambience_available
from .pipeline.content import ANGLES, NAMES, ContentContext, clip_text, generate_post, load_meta, write_posts_md
from .pipeline.export import build_pack
from .pipeline.history import get_history
from .pipeline.hooks import write_variants
from .pipeline.ingest_cache import source_cache_usage
from .pipeline.jumpcut import clean_removed
from .pipeline.llm import OllamaClient
from .pipeline.presets import POSITIONS, all_presets, get_preset
from .pipeline.reframe import people, source_size, tracked_path, visible_speaker_turns
from .pipeline.render import ASPECTS, FEED, extra_aspects, render_clip
from .watch import Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SAFE_NAME = re.compile(r"^[\w.\-]+$")


def create_app(pipeline=None, start_worker: bool = True, llm_factory=OllamaClient, steps=None) -> FastAPI:
    store = JobStore(settings.data_dir)
    worker = Worker(store, pipeline, steps, llm_factory=llm_factory)
    library = Library(store, fetch_meta=settings.library_fetch_meta)
    store.listeners.append(library.touch)
    watcher = Watcher(store, worker.submit)

    def source_for(job: Job) -> str | None:
        """What the worker downloads/reads for a new job: its link, or the uploaded copy in its folder."""
        if job.source.startswith(("http://", "https://")):
            return job.source
        upload = store.dir(job.id) / "source.mp4"
        return str(upload) if upload.is_file() else None

    for job in store.waiting():  # a batch queued before a restart carries on, in the same order
        src = source_for(job)
        if src:
            worker.submit(job.id, src)
        else:
            store.update(job.id, status=JobStatus.failed, error="Interrupted by server restart (upload incomplete)")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            worker.start()
            library.start()
            watcher.start()
        yield
        watcher.stop()

    app = FastAPI(title="ClipForge", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.library = store, worker, library
    app.state.watcher = watcher

    @app.exception_handler(QueueConflict)
    async def queue_conflict(_: Request, error: QueueConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

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

    # ---------------------------------------------------------------- brand kit (logo, end card, colour)

    def kit_info(kit) -> dict:
        return {**kit.model_dump(), "default_accent": settings.carousel_accent, "default_handle": settings.brand_handle,
                "accent_now": brand.accent(kit), "handle_now": brand.handle(kit)}

    @app.get("/api/brand-kit")
    def get_brand_kit() -> dict:
        return kit_info(brand.load())

    @app.put("/api/brand-kit")
    def put_brand_kit(body: BrandKit) -> dict:
        return kit_info(brand.save(body))

    @app.post("/api/brand-kit/logo")
    def upload_logo(file: UploadFile = File(...)) -> dict:
        data = file.file.read(10_000_001)
        if len(data) > 10_000_000:
            raise HTTPException(413, "logo over 10 MB — export a smaller PNG")
        try:
            return kit_info(brand.save_logo(data))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/brand-kit/logo")
    def delete_logo() -> dict:
        return kit_info(brand.remove_logo())

    @app.get("/api/brand-kit/logo.png")
    def get_logo() -> FileResponse:
        if not brand.logo_path().is_file():
            raise HTTPException(404, "no logo")
        return FileResponse(brand.logo_path(), media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/api/brand-kit/end-card.png")
    def end_card_preview() -> FileResponse:
        """The end card's overlay as it would be drawn now (the UI shows it over a dark frame)."""
        out = settings.data_dir / "brand" / "end-card-preview.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        kit = brand.load()
        brand.end_card_png(kit.model_copy(update={"enabled": True}), out)
        return FileResponse(out, media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.get("/api/sounds")
    def sounds() -> dict:
        """Sound design choices for the Shorts step: music tracks in data/sounds/music and the defaults."""
        sfx.music_dir().mkdir(parents=True, exist_ok=True)  # so there's a folder to drop tracks into
        return {"music": sfx.music_tracks(), "folder": windows_path(sfx.music_dir().resolve()),
                "sfx_default": settings.sfx_default,
                "music_default": settings.music_default if sfx.music_path(settings.music_default) else ""}

    @app.get("/api/caption-presets")
    def caption_presets() -> dict:
        """Caption looks for the Shorts step (built-in + data/caption_presets.json) and the global defaults
        (also the feed versions new shorts get: EXTRA_ASPECTS)."""
        return {"default": settings.caption_preset, "position": settings.caption_position,
                "style": settings.caption_style, "positions": list(POSITIONS),
                "aspects": extra_aspects(RenderOptions()), "feed_aspects": FEED,
                "emoji": settings.caption_emoji, "emoji_ready": emoji.ready(),
                "presets": [p.public() for p in all_presets().values()]}

    # ---------------------------------------------------------------- brand voice (all jobs)

    def voice_info(v: BrandVoice) -> dict:
        return {"voice": v.model_dump(), "platforms": [{"key": k, "name": NAMES[k]} for k in ANGLES],
                "max_examples": voice.MAX_EXAMPLES, "max_chars": voice.MAX_CHARS,
                "default_notes": settings.content_voice}

    @app.get("/api/brand-voice")
    def get_brand_voice() -> dict:
        """Your posts used as few-shot examples so Content Studio writes in your voice."""
        return voice_info(voice.load())

    @app.put("/api/brand-voice")
    def put_brand_voice(body: BrandVoice) -> dict:
        return voice_info(voice.save(voice.clean(body, list(ANGLES))))

    # ---------------------------------------------------------------- your voice (tts.py: Qwen3-TTS cloning)
    tts_state: dict = {}

    @app.get("/api/tts/status")
    def tts_status(refresh: bool = False) -> dict:
        """Is the TTS env installed and can it use the GPU? (checked once, then cached; ?refresh=true re-checks)"""
        if refresh or "v" not in tts_state:
            tts_state["v"] = tts.status()
        return tts_state["v"]

    def gpu_work(fn):
        """Run fn() holding GPU_LOCK with Gemma unloaded (8 GB: Whisper / TTS need the room). 409 while busy."""
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        try:
            try:
                llm_factory().unload()
            except Exception:
                pass  # Ollama not running: nothing to unload
            return fn()
        except KeyError:
            raise HTTPException(404, "not found") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from None
        finally:
            GPU_LOCK.release()

    def voice_or_404(vid: str) -> dict:
        try:
            return tts.load(vid)
        except (KeyError, ValueError):
            raise HTTPException(404, "voice not found") from None

    @app.get("/api/voices")
    def list_voices() -> dict:
        return {"voices": tts.all_voices(), "styles": list(tts.STYLES), "max_recordings": tts.MAX_RECORDINGS,
                "outlier_gap": tts.OUTLIER_GAP, "model": settings.tts_model, "languages": list(tts.LANGUAGES),
                "sample_text": settings.tts_sample_text}

    @app.post("/api/voices", status_code=201)
    def create_voice(body: VoiceCreate) -> dict:
        try:
            return tts.create(body.name, body.description, body.consent)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.patch("/api/voices/{vid}")
    def update_voice(vid: str, body: VoiceUpdate) -> dict:
        """Rename / describe / set the default language (no rebuild needed)."""
        voice_or_404(vid)
        try:
            return tts.update_profile(vid, body.model_dump(exclude_none=True))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/voices/{vid}")
    def delete_voice(vid: str) -> dict:
        voice_or_404(vid)
        tts.delete(vid)
        return {"ok": True}

    @app.post("/api/voices/{vid}/recordings")
    def add_voice_recordings(vid: str, files: list[UploadFile] = File(...), style: str = Form("")) -> dict:
        """Your recordings (any audio/video, several at once): cleaned, split if long, transcribed by Whisper."""
        voice_or_404(vid)
        data = [(f.filename or "audio", f.file.read(tts.MAX_UPLOAD + 1)) for f in files[:20]]
        if any(len(b) > tts.MAX_UPLOAD for _, b in data):
            raise HTTPException(413, f"each file must be under {tts.MAX_UPLOAD // 2**20} MB")
        return gpu_work(lambda: tts.add_recordings(vid, data, style))

    @app.patch("/api/voices/{vid}/recordings/{file}")
    def edit_voice_recording(vid: str, file: str, body: RecordingEdit) -> dict:
        voice_or_404(vid)
        try:
            return tts.edit_recording(vid, file, body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, "recording not found") from None

    @app.delete("/api/voices/{vid}/recordings/{file}")
    def delete_voice_recording(vid: str, file: str) -> dict:
        voice_or_404(vid)
        return tts.remove_recording(vid, file)

    @app.post("/api/voices/{vid}/build")
    def build_voice(vid: str, body: VoiceBuild | None = None) -> dict:
        """Your voice from every used recording (averaged speaker embedding + the best clip's delivery)."""
        voice_or_404(vid)
        return gpu_work(lambda: tts.build(vid, (body.style if body else "") or "",
                                          (body.sample_text if body else "") or None))

    @app.post("/api/voices/{vid}/speak")
    def speak_voice(vid: str, body: SpeakRequest) -> dict:
        """Any text in your voice → a wav in the voice's samples (first call downloads the model, ~4 GB)."""
        voice_or_404(vid)
        script = DubScript(clip=0, voice=vid, version=body.version)
        spoken = dub.spoken_text(script, DubLine(i=0, start=0, end=0, original=body.text, text=body.text))
        out = gpu_work(lambda: tts.speak(vid, [spoken], body.language or None, version=body.version))
        return {**out[0], "voice": tts.load(vid)}

    @app.get("/api/pronunciations")
    def get_pronunciations() -> PronunciationDictionary:
        return dub.pronunciation_dictionary()

    @app.put("/api/pronunciations")
    def put_pronunciations(body: PronunciationDictionary) -> PronunciationDictionary:
        saved_scopes = {alias.voice for alias in dub.pronunciation_dictionary().aliases}
        for vid in {alias.voice for alias in body.aliases if alias.voice} - saved_scopes:
            voice_or_404(vid)
        dub.save_pronunciation_dictionary(body)
        return body

    @app.post("/api/voices/{vid}/pace")
    def calibrate_voice(vid: str, version: int | None = None) -> dict:
        voice_or_404(vid)
        return gpu_work(lambda: dub.calibrate_pace(vid, version))

    @app.post("/api/voices/{vid}/compare")
    def compare_voice(vid: str, body: CompareRequest) -> dict:
        """The same sentence in two versions of the voice (one model load) — hear what a rebuild changed."""
        voice_or_404(vid)
        return {"items": gpu_work(lambda: tts.compare(vid, body.text, body.versions, body.language or None))}

    @app.get("/api/voices/{vid}/audio/{kind}/{name}")
    def voice_audio(vid: str, kind: str, name: str):
        if kind not in ("recordings", "samples") or not SAFE_NAME.match(name):
            raise HTTPException(404, "not found")
        voice_or_404(vid)
        f = tts.voice_dir(vid) / kind / name
        if not f.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(f, media_type="audio/wav")

    # ---------------------------------------------------------------- re-voice a recording (revoice.py)
    def revoice_view(rid: str) -> dict:
        try:
            m, sc = revoice.meta(rid), revoice.script(rid)
        except (KeyError, ValueError):
            raise HTTPException(404, "recording not found") from None
        return {**m, "script": sc.model_dump(mode="json"),
                "stale": [ln.i for ln in sc.lines if dub.stale(sc, ln)] if sc.voice else [],
                "max_rate": dub.MAX_RATE}

    @app.get("/api/revoice")
    def list_revoice() -> dict:
        return {"items": revoice.all_items(), "max_seconds": revoice.MAX_SECONDS}

    @app.post("/api/revoice", status_code=201)
    def create_revoice(file: UploadFile = File(...), name: str = Form("", max_length=80)) -> dict:
        """Your narration (audio or video, ≤ MAX_SECONDS): saved and transcribed into sentences you can edit."""
        data = file.file.read(revoice.MAX_UPLOAD + 1)
        if len(data) > revoice.MAX_UPLOAD:
            raise HTTPException(413, f"under {revoice.MAX_UPLOAD // 2**20} MB please")
        m = gpu_work(lambda: revoice.create(file.filename or "recording", data, name))
        return revoice_view(m["id"])

    @app.get("/api/revoice/{rid}")
    def get_revoice(rid: str) -> dict:
        return revoice_view(rid)

    @app.put("/api/revoice/{rid}")
    def put_revoice(rid: str, body: DubEdit) -> dict:
        revoice_view(rid)
        try:
            revoice.edit(rid, body.voice, body.version, body.texts)
        except (KeyError, ValueError):
            raise HTTPException(404, "voice not found") from None
        return revoice_view(rid)

    @app.post("/api/revoice/{rid}/generate")
    def generate_revoice(rid: str, body: DubGenerate | None = None) -> dict:
        """Every sentence in the chosen voice at your timing → revoiced.mp4 (video) / revoiced.wav (audio)."""
        sc = revoice_view(rid)["script"]
        if not sc["voice"]:
            raise HTTPException(400, "choose a voice first")
        gpu_work(lambda: revoice.generate(rid, body.lines if body else None))
        return revoice_view(rid)

    @app.delete("/api/revoice/{rid}")
    def delete_revoice(rid: str) -> dict:
        revoice_view(rid)
        revoice.delete(rid)
        return {"ok": True}

    @app.get("/api/revoice/{rid}/files/{name}")
    def revoice_file(rid: str, name: str):
        if not SAFE_NAME.match(name):
            raise HTTPException(404, "not found")
        try:
            f = revoice.folder(rid) / name
        except KeyError:
            raise HTTPException(404, "not found") from None
        if not f.is_file() or name in ("meta.json", "words.json"):
            raise HTTPException(404, "not found")
        return FileResponse(f)

    @app.get("/api/brand-voice/suggestions")
    def brand_voice_suggestions() -> dict[str, list[str]]:
        """Posts you edited by hand or marked posted (newest job first) — good voice examples, not yet added."""
        have = voice.load().examples
        out: dict[str, list[str]] = {k: [] for k in ANGLES}
        for job in store.list():
            for c in job.clips:
                for k, post in c.content.items():
                    if k not in out or not (post.edited or post.status == "posted"):
                        continue
                    text = f"{post.title}\n\n{post.text}" if post.title else post.text
                    text = text.strip()[:voice.MAX_CHARS].strip()
                    if text and text not in out[k] and text not in have.get(k, []) and len(out[k]) < 8:
                        out[k].append(text)
        return out

    @app.post("/api/jobs", response_model=Job, status_code=201)
    def create_job(body: JobCreate) -> Job:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "url must start with http(s)://")
        hinglish = settings.hinglish_default if body.hinglish is None else body.hinglish
        job = store.create(body.url, body.num_clips, body.mode, body.topic, hinglish=hinglish, title=body.title,
                           channel=body.channel, purpose=body.purpose, whisper_model=body.whisper_model)
        worker.submit(job.id, body.url)
        return job

    @app.post("/api/batch/expand")
    def batch_expand(body: BatchLinks) -> dict:
        """Pasted links → the videos they stand for (playlists/channels listed, no download) + "done before"."""
        out = batch.expand(body.text)
        for v in out["videos"]:
            v["done"] = len(library.lookup(v["url"]))
        return out

    @app.post("/api/jobs/batch", status_code=201)
    def create_batch(body: BatchCreate) -> dict:
        """One job per link, same settings, queued in order — the single worker runs them one after another."""
        urls, seen = [], set()
        for u in (x.strip() for x in body.urls):
            if not u.startswith(("http://", "https://")):
                raise HTTPException(400, f"not a link: {u[:80]}")
            if video_key(u) not in seen:
                seen.add(video_key(u))
                urls.append(u)
        hinglish = settings.hinglish_default if body.hinglish is None else body.hinglish
        jobs = []
        for u in urls:
            known = body.info.get(u)
            job = store.create(u, body.num_clips, body.mode, body.topic, hinglish=hinglish,
                               title=known.title if known else "", channel=known.channel if known else "",
                               whisper_model=body.whisper_model)
            worker.submit(job.id, u)
            jobs.append(job)
        return {"jobs": jobs}

    @app.post("/api/jobs/upload", response_model=Job, status_code=201)
    def upload_job(file: UploadFile = File(...), num_clips: int = Form(5), mode: JobMode = Form(JobMode.review),
                   topic: str = Form("", max_length=200), hinglish: bool | None = Form(None),
                   purpose: Purpose = Form("create"), whisper_model: WhisperChoice | None = Form(None)) -> Job:
        job = store.create(file.filename or "upload.mp4", num_clips, mode, topic, title=Path(file.filename or "").stem,
                           hinglish=settings.hinglish_default if hinglish is None else hinglish, purpose=purpose,
                           whisper_model=whisper_model)
        d = store.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / "source.mp4"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        worker.submit(job.id, str(dest))
        return job

    @app.get("/api/jobs/{job_id}/learn")
    def get_learn(job_id: str) -> dict:
        """Study notes (learning mode); {"notes": null} until they're made."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        n = learn.load(store.dir(job_id))
        return {"notes": n.model_dump(mode="json") if n else None, "markdown": "notes.md" if n else None}

    @app.post("/api/jobs/{job_id}/learn", response_model=Job)
    def make_learn(job_id: str) -> Job:
        """Study notes for a job whose video is already here (shorts job or a learn job again): queued."""
        job = editable(job_id)
        if not (store.dir(job_id) / "source.mp4").is_file():
            raise HTTPException(400, "no video in this job yet")
        return worker.submit_learn(job)

    @app.post("/api/jobs/{job_id}/retry", response_model=Job)
    def retry_job(job_id: str, body: Retry | None = None) -> Job:
        """Run a failed job again in its own folder: a finished download / transcript is reused, not redone.

        Optional body {"topic": "..."} changes the topic first ("" = none), e.g. after "No moments about X found".
        """
        from .pipeline.highlights import clean_topic

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
        changes = {} if body is None or body.topic is None else {"topic": clean_topic(body.topic)}
        job, _ = store.queue_job(job_id, (JobStatus.failed,), expected=job, stage=Stage.queued, progress=0.0,
                                 message="Queued: retry", **changes)
        worker.submit(job_id, source)
        return job

    @app.get("/api/jobs", response_model=list[Job])
    def list_jobs() -> list[Job]:
        return store.list()

    def stream(request: Request, job_id: str | None = None) -> StreamingResponse:
        return StreamingResponse(job_events(store, request.is_disconnected, job_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/events")
    def all_events(request: Request) -> StreamingResponse:
        """Live job updates (SSE): a snapshot of every job, then each change as it happens."""
        return stream(request)

    @app.get("/api/jobs/{job_id}/events")
    def events(job_id: str, request: Request) -> StreamingResponse:
        """Live updates for one job (SSE) — e.g. `curl -N localhost:8000/api/jobs/<id>/events`."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        return stream(request, job_id)

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
        waiting = job.status == JobStatus.queued and not job.clips  # never started: cancel it (worker skips it)
        if job.status in (JobStatus.queued, JobStatus.running) and not waiting:
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

    @app.get("/api/jobs/{job_id}/scenes")
    def get_scenes(job_id: str) -> dict:
        """What is on screen, scene by scene (scenes.json), for the Transcript step; mode "none" = no index yet."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        index = scenes.load(store.dir(job_id))
        if not index:
            return {"mode": "none", "scenes": []}
        return {"mode": index.mode, "reason": index.reason, "model": index.model,
                "scenes": [{"k": k, "start": x.start, "end": x.end, "t": x.t, "kind": x.kind,
                            "description": x.description}
                           for k, x in enumerate(index.scenes) if x.description]}

    @app.get("/api/jobs/{job_id}/scenes/{k}.jpg")
    def scene_thumb(job_id: str, k: int):
        """Scene k's keyframe (small JPEG) for the Transcript step."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        d = store.dir(job_id)
        index = scenes.load(d)
        f = scenes.thumb(d, d / "source.mp4", index, k) if index and (d / "source.mp4").is_file() else None
        if not f:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    # ---------------------------------------------------------------- caption fixes + vocabulary

    def mark_stale(job_id: str, spans: list[tuple[float, float]]) -> Job:
        """Rendered shorts whose range covers a changed word have outdated captions burned in. Suggested stat cards
        on a changed word are dropped (the number may be different now); approved ones are yours and stay."""
        job = store.get(job_id)
        for c in job.clips:
            if inserts.forget_changed(c.model_copy(deep=True), spans):  # only write when one goes
                job = store.update_clip(job_id, c.index, lambda x: inserts.forget_changed(x, spans))
            if c.file and not c.stale and any(s < c.end and e > c.start for s, e in spans):
                job = store.update_clip(job_id, c.index, lambda x: setattr(x, "stale", True))
        return job

    @app.get("/api/jobs/{job_id}/script")
    def get_script(job_id: str) -> dict:
        """Hindi transcripts: "devanagari" (can switch to Hinglish), "hinglish" (can switch back) or "" (n/a)."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        return {"state": hinglish.state(store.dir(job_id)), "default": settings.hinglish_default}

    @app.post("/api/jobs/{job_id}/script", response_model=Job)
    def set_script(job_id: str, body: ScriptChoice) -> Job:
        """Hindi transcript in Latin letters (Hinglish) or back to Devanagari. Captions/.srt/posts follow; rendered
        shorts are marked stale. Going back drops fixes made in Hinglish (the Devanagari original is restored)."""
        editable(job_id)
        d = store.dir(job_id)
        spans = hinglish.romanize(d) if body.hinglish else hinglish.back_to_native(d)
        job = store.update(job_id, hinglish=body.hinglish)
        return mark_stale(job_id, spans) if spans else job

    @app.get("/api/jobs/{job_id}/words")
    def get_words(job_id: str, start: float = 0.0, end: float = 1e9) -> list[dict]:
        """Transcript words in [start, end] with their address (seg, i) — for the caption editor."""
        if not store.get(job_id):
            raise HTTPException(404, "job not found")
        return transcript_edit.words_in(transcript_edit.load(store.dir(job_id)), start, end)

    @app.patch("/api/jobs/{job_id}/words", response_model=Job)
    def edit_word(job_id: str, body: WordEdit) -> Job:
        """Fix one misheard word (captions, .srt and post writing all use it)."""
        editable(job_id)
        try:
            span = transcript_edit.set_word(store.dir(job_id), body.seg, body.i, body.word)
        except KeyError:
            raise HTTPException(404, "word not found — reload the transcript") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return mark_stale(job_id, [span])

    @app.post("/api/jobs/{job_id}/transcript/replace")
    def replace_words(job_id: str, body: ReplaceWords) -> dict:
        """Replace a word or phrase everywhere in the transcript (dry_run: just count). remember=True also
        adds the replacement to the vocabulary, so future transcripts spell it right."""
        if body.dry_run:
            if not store.get(job_id):
                raise HTTPException(404, "job not found")
        else:
            editable(job_id)
        try:
            spans = transcript_edit.replace_all(store.dir(job_id), body.find, body.replace, dry_run=body.dry_run)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        job = store.get(job_id)
        if spans and not body.dry_run:
            job = mark_stale(job_id, spans)
            if body.remember:
                vocabulary.add(body.replace)
        return {"matches": len(spans), "job": None if body.dry_run else job.model_dump(mode="json")}

    @app.get("/api/vocabulary", response_model=Vocabulary)
    def get_vocabulary() -> Vocabulary:
        """Names and terms Whisper should know (used for new transcripts)."""
        return Vocabulary(terms=vocabulary.load())

    @app.put("/api/vocabulary", response_model=Vocabulary)
    def put_vocabulary(body: Vocabulary) -> Vocabulary:
        return Vocabulary(terms=vocabulary.save(body.terms))

    @app.get("/api/jobs/{job_id}/waveform")
    def get_waveform(job_id: str) -> dict:
        """Loudness levels (0-100, `rate` per second) of the whole source for the Pick timeline; cached."""
        if not store.get(job_id) or not (store.dir(job_id) / "source.mp4").is_file():
            raise HTTPException(404, "no source video yet")
        return waveform.load(store.dir(job_id))

    @app.patch("/api/jobs/{job_id}/clips/{index}", response_model=Job)
    def edit_clip(job_id: str, index: int, body: ClipEdit) -> Job:
        editable(job_id)
        changes = body.model_dump(exclude_none=True)

        def apply(c: Clip) -> None:
            def burned_in(c: Clip) -> tuple:  # everything the rendered mp4 depends on
                return (c.start, c.end, c.options.model_dump(), c.hook, c.keywords, c.removed,
                        [i.model_dump() for i in c.inserts if i.approved])

            before = burned_in(c)
            if "removed" in changes:
                changes["removed"] = clean_removed(changes["removed"])
            for k, v in changes.items():
                if k == "options":
                    v = type(c.options)(**v)
                elif k == "inserts":
                    v = [Insert(**x) for x in v]
                setattr(c, k, v)
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
    def produce_job(job_id: str, body: ProduceRequest | None = None) -> Job:
        """Write posts + render every approved clip that's missing or changed. Approved clips the judge marked
        needs_context / reject must be confirmed ({"confirm": true}) — the check before rendering."""
        job = editable(job_id)
        if not any(c.approved for c in job.clips):
            raise HTTPException(400, "approve at least one clip first")
        flagged = [c for c in job.clips if c.approved and judge.current(c) and c.review.verdict in judge.FLAGGED]
        if flagged and not (body and body.confirm):
            names = ", ".join(f"#{c.index} ({c.review.verdict.replace('_', ' ')})" for c in flagged)
            raise HTTPException(409, f"confirm creating clips the judge flagged: {names}")
        return worker.submit_produce(job)

    @app.post("/api/jobs/{job_id}/clips/{index}/render", response_model=Job)
    def rerender_clip(job_id: str, index: int) -> Job:
        job = editable(job_id)
        if not any(c.index == index for c in job.clips):
            raise HTTPException(404, "clip not found")
        return worker.submit_render(job, index)

    @app.patch("/api/jobs/{job_id}/clips/{index}/content/{platform}", response_model=Post)
    def edit_post(job_id: str, index: int, platform: str, body: PostEdit) -> Post:
        editable(job_id)
        if body.status and body.status not in ("draft", "approved", "posted"):
            raise HTTPException(400, "status must be draft, approved or posted")
        url = None if body.url is None else body.url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "the post link must start with http(s)://")
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
                if body.status == "posted" and post.status != "posted":
                    post.posted_at = datetime.now(UTC)
                elif body.status != "posted":
                    post.posted_at = None
                post.status = body.status
            if url is not None:
                post.url = url
                if url and post.status != "posted":  # a live link means it's out there
                    post.status, post.posted_at = "posted", datetime.now(UTC)
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
            current = store.get(job_id)
            if current is None or current.status != JobStatus.done:
                raise HTTPException(409, "job is no longer ready — refresh and try again")
            _, clip = clip_of(job_id, index)
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

    def clip_of(job_id: str, index: int) -> tuple[Job, Clip]:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        clip = next((c for c in job.clips if c.index == index), None)
        if not clip:
            raise HTTPException(404, "clip not found")
        return job, clip

    def source_of(job_id: str) -> Path:
        video = store.dir(job_id) / "source.mp4"
        if not video.is_file():
            raise HTTPException(409, "the source video isn't there yet")
        return video

    def data_url(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode() if jpeg else ""

    @app.post("/api/jobs/{job_id}/clips/{index}/hooks", response_model=Job)
    def hook_variants(job_id: str, index: int) -> Job:
        """Gemma writes several title + hook pairs in different styles (question, bold claim, number…) to pick from."""
        editable(job_id)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
            editable(job_id)
            _, clip = clip_of(job_id, index)
            try:
                segs = [Segment(**x) for x in json.loads((d / "transcript.json").read_text())]
                variants = write_variants(clip, clip_text(segs, clip), llm_factory(), load_meta(d).get("title", ""))
            except Exception as e:
                if store.get(job_id) is None:
                    raise HTTPException(404, "job was deleted meanwhile") from e
                raise HTTPException(502, f"Gemma failed: {e}") from e
            try:  # onto the job as it is now (see regenerate_post), still holding GPU_LOCK
                return store.update_clip(job_id, index, lambda c: setattr(c, "variants", variants))
            except KeyError:
                raise HTTPException(404, "clip not found") from None
        finally:
            GPU_LOCK.release()

    @app.post("/api/jobs/{job_id}/clips/{index}/inserts/suggest", response_model=Job)
    def suggest_inserts(job_id: str, index: int) -> Job:
        """Gemma picks which spoken numbers get a stat card (and labels them). Suggestions replace the old ones;
        approved and hand-made cards stay. Never approves: you look at them first."""
        editable(job_id)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
            editable(job_id)
            _, clip = clip_of(job_id, index)
            try:
                segs = [Segment(**x) for x in json.loads((d / "transcript.json").read_text())]
                new = inserts.suggest(clip, segs, llm_factory())
            except Exception as e:
                if store.get(job_id) is None:
                    raise HTTPException(404, "job was deleted meanwhile") from e
                raise HTTPException(502, f"stat cards failed: {e}") from e

            def apply(c: Clip) -> None:  # onto the job as it is now, still holding GPU_LOCK
                c.inserts = inserts.merge(c, new)
                c.inserts_checked = True

            try:
                return store.update_clip(job_id, index, apply)
            except KeyError:
                raise HTTPException(404, "clip not found") from None
        finally:
            GPU_LOCK.release()

    @app.get("/api/jobs/{job_id}/validation")
    def job_validation(job_id: str) -> dict:
        """Before rendering: how many complete shorts this job can give, how long the rest need, a recommendation."""
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return {"validation": judge.validate(job.clips, job.num_clips)}

    @app.post("/api/jobs/{job_id}/clips/{index}/review", response_model=Job)
    def review_clip(job_id: str, index: int) -> Job:
        """Clip judge on the clip as it is now (after your trims): verdict, what's missing, a suggested fix."""
        editable(job_id)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
            editable(job_id)
            _, clip = clip_of(job_id, index)
            try:
                segs = [Segment(**x) for x in json.loads((d / "transcript.json").read_text())]
                scene_index = scenes.load(d)  # what's on screen counts too, for videos that have a scene index
                shown = scene_index.for_prompt() if scene_index and scene_index.mode == "described" else None
                result = judge.review(segs, clip, llm_factory(), shown or None)
            except Exception as e:
                if store.get(job_id) is None:
                    raise HTTPException(404, "job was deleted meanwhile") from e
                raise HTTPException(502, f"clip check failed: {e}") from e
            try:  # onto the job as it is now, still holding GPU_LOCK
                return store.update_clip(job_id, index, lambda c: setattr(c, "review", result))
            except KeyError:
                raise HTTPException(404, "clip not found") from None
        finally:
            GPU_LOCK.release()

    # ---------------------------------------------------------------- Replace voice (dub.py)
    dub_lock = Lock()

    @app.get("/api/narration/options")
    def narration_options() -> dict:
        return {"ambience_available": ambience_available()}

    @app.post("/api/jobs/{job_id}/dub/batch", response_model=Job, status_code=202)
    def queue_narration(job_id: str, body: NarrationQueue) -> Job:
        job = editable(job_id)
        if body.ambience and not ambience_available():
            raise HTTPException(400, "Install Demucs to keep source ambience")
        clips = {clip.index: clip for clip in job.clips}
        if not body.duration_overrides.keys() <= set(body.clips):
            raise HTTPException(400, "Duration overrides must refer to selected shorts")
        items = {}
        for index in dict.fromkeys(body.clips):
            if index not in clips:
                raise HTTPException(404, f"Short {index} was not found")
            duration = body.duration_for(index)
            if (duration.pacing == "smart" and duration.smart_max_seconds is not None
                    and clips[index].duration > duration.smart_max_seconds + 1e-6):
                raise HTTPException(400, f"Short {index} exceeds the Smart duration limit. Trim it or raise the limit.")
            try:
                dub.activation_script(store.dir(job_id), clips[index])
            except ValueError as e:
                raise HTTPException(400, f"Short {index}: {e}") from None
            items[index] = NarrationItem()
        batch = NarrationBatch(items=items, restore=job.status, **body.model_dump(exclude={"clips"}))
        return worker.submit_narration(job, batch)

    @app.post("/api/jobs/{job_id}/dub/batch/retry", response_model=Job, status_code=202)
    def retry_narration(job_id: str) -> Job:
        job = editable(job_id)
        batch = job.narration
        if batch is None or not any(item.status == "failed" for item in batch.items.values()):
            raise HTTPException(400, "No failed narrations to retry")
        items = {index: NarrationItem() if item.status == "failed" else item for index, item in batch.items.items()}
        return worker.submit_narration(job, batch.model_copy(update={"items": items, "restore": job.status}))

    def segs_of(job_id: str) -> list[Segment]:
        f = store.dir(job_id) / "transcript.json"
        if not f.is_file():
            raise HTTPException(400, "no transcript yet")
        return [Segment(**x) for x in json.loads(f.read_text())]

    @app.post("/api/jobs/{job_id}/clips/{index}/export-preview")
    def export_preview(job_id: str, index: int, body: ExportPreviewRange | None = None) -> dict:
        editable(job_id)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "The renderer is busy. Try again when the current job finishes.")
        try:
            editable(job_id)
            _, clip = clip_of(job_id, index)
            interval = body or ExportPreviewRange()
            file = render_clip(source_of(job_id), clip.model_copy(deep=True), segs_of(job_id),
                               store.dir(job_id), preview=True, preview_range=(interval.start, interval.length))
            return {"file": file}
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except RuntimeError as e:
            raise HTTPException(502, str(e)) from None
        finally:
            GPU_LOCK.release()

    def attach_broll(job_id: str, index: int, stream: BinaryIO) -> Job:
        editable(job_id)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "The renderer is busy. Try again when the current job finishes.")
        try:
            editable(job_id)
            _, clip = clip_of(job_id, index)
            asset = uuid.uuid4().hex[:12]
            directory = store.dir(job_id)
            duration = clip.duration if clip.options.dub else clip.duration - clip.cut_seconds
            if clip.options.dub and clip.options.dub_pacing != "source":
                duration = dub.output_duration(directory, clip)
            broll.next_slot(clip.options.cutaways(), duration)  # reject before copying a potentially large file
            footage = broll.save(directory, index, stream, asset)
            try:
                def attach(current: Clip) -> None:
                    placements = current.options.cutaways()
                    start, end = broll.next_slot(placements, duration)
                    placement = BrollPlacement(start=start, end=end, asset=asset,
                                               revision=footage.stat().st_mtime_ns // 1_000_000)
                    current.options = current.options.model_copy(update={"broll": None,
                                                                         "brolls": [*placements, placement]})
                    current.stale = bool(current.file)

                return store.update_clip(job_id, clip.index, attach)
            except Exception:
                footage.unlink(missing_ok=True)
                raise
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        finally:
            GPU_LOCK.release()

    @app.post("/api/jobs/{job_id}/clips/{index}/broll", response_model=Job)
    def upload_broll(job_id: str, index: int, file: UploadFile = File(...)) -> Job:
        return attach_broll(job_id, index, file.file)

    @app.get("/api/jobs/{job_id}/clips/{index}/broll/suggestions")
    def suggest_broll(job_id: str, index: int) -> dict:
        _, clip = clip_of(job_id, index)
        directory = store.dir(job_id)
        segs = segs_of(job_id) if (directory / "transcript.json").is_file() else []
        script = dub.load(directory, index) if clip.options.dub else None
        speech = " ".join(line.text for line in script.lines if clip.start <= line.start < clip.end) if script else ""
        scene_index = scenes.load(directory)
        picture = " ".join(scene.description for scene in scene_index.scenes
                           if scene.start < clip.end and scene.end > clip.start) if scene_index else ""
        primary = " ".join([clip.title, clip.hook, *clip.keywords])
        return {"directory": str(broll.library_root()),
                "items": broll.suggest(primary, f"{speech or clip_text(segs, clip)} {picture}")}

    def broll_asset(asset_id: str) -> Path:
        asset = broll.library_assets().get(asset_id)
        if asset is None:
            raise HTTPException(404, "Local B-roll file not found")
        return asset[0]

    @app.get("/api/broll/assets/{asset_id}")
    def preview_broll_asset(asset_id: str) -> FileResponse:
        return FileResponse(broll_asset(asset_id), media_type="video/mp4")

    @app.post("/api/jobs/{job_id}/clips/{index}/broll/use/{asset_id}", response_model=Job)
    def use_broll_asset(job_id: str, index: int, asset_id: str) -> Job:
        editable(job_id)
        try:
            with broll_asset(asset_id).open("rb") as stream:
                return attach_broll(job_id, index, stream)
        except FileNotFoundError:
            raise HTTPException(404, "Local B-roll file not found") from None

    @app.post("/api/jobs/{job_id}/dub/drafts")
    def draft_dubs(job_id: str, body: DubBatchDraft) -> dict:
        job = editable(job_id)
        voice = voice_or_404(body.voice)
        try:
            prompt = tts.prompt_for(voice, body.version)
        except KeyError:
            raise HTTPException(400, "Choose a voice with an available build") from None
        prompt_name = prompt.relative_to(tts.voice_dir(body.voice)).as_posix()
        version = next(build["version"] for build in voice["builds"] if build.get("prompt") == prompt_name)
        eligible = {clip.index: clip for clip in job.clips if clip.approved and not clip.visual}
        if not set(body.clips) <= eligible.keys():
            raise HTTPException(400, "Select approved clips with dialogue")
        d = store.dir(job_id)
        segs = segs_of(job_id)
        previous = {index: dub.load(d, index) for index in dict.fromkeys(body.clips)}
        drafts = []
        for index, saved in previous.items():
            clip = eligible[index]
            script = saved.model_copy(deep=True) if saved else dub.draft(segs, clip)
            if not any(clip.start <= line.start < clip.end and line.text.strip() for line in script.lines):
                raise HTTPException(400, f"Short {index} has no narration lines in its current range")
            drafts.append((clip, dub.edit(script, body.voice, version)))

        if body.mode in ("rewrite", "compact"):
            def rewrite():
                llm = llm_factory()
                try:
                    for clip, script in drafts:
                        dub.narration_rewrite(script, clip, llm, compact=body.mode == "compact")
                finally:
                    llm.unload()

            gpu_work(rewrite)
        with dub_lock:
            if editable(job_id) is not job or any(dub.load(d, index) != saved for index, saved in previous.items()):
                raise HTTPException(409, "The clips or scripts changed while preparing drafts. Try again.")
            for _, script in drafts:
                dub.save(d, script)
        return {"clips": [clip.index for clip, _ in drafts]}

    @app.get("/api/jobs/{job_id}/clips/{index}/dub")
    def get_dub(job_id: str, index: int) -> dict:
        """The clip's Replace-voice script — saved, or a draft of its sentences as said now."""
        _, clip = clip_of(job_id, index)
        script = dub.load(store.dir(job_id), index)
        saved = script is not None
        segs = segs_of(job_id)
        script = script or dub.draft(segs, clip)
        dictionary = dub.pronunciation_dictionary()
        spoken = {line.i: dub.spoken_text(script, line, dictionary) for line in script.lines}
        paces = {(vid, version): dub.voice_pace(DubScript(clip=index, voice=vid, version=version))
                 for vid, version in {dub.line_voice(script, line) for line in script.lines}}
        return {"saved": saved, "script": script.model_dump(mode="json"),
                "spoken_texts": spoken, "line_paces": {ln.i: paces[dub.line_voice(script, ln)]
                                                      for ln in script.lines},
                "stale": [ln.i for ln in script.lines if clip.start <= ln.start < clip.end
                          and dub.stale(script, ln, dictionary)] if script.voice else [],
                "max_rate": dub.MAX_RATE, "warnings": dub.script_warnings(segs, clip, script),
                "voice_pace": dub.voice_pace(script)}

    @app.put("/api/jobs/{job_id}/clips/{index}/dub")
    def put_dub(job_id: str, index: int, body: DubEdit) -> dict:
        """Choose the voice/version and edit the lines (nothing is generated yet)."""
        editable(job_id)
        _, clip = clip_of(job_id, index)
        d = store.dir(job_id)
        if body.voice:
            try:
                tts.load(body.voice)
            except (KeyError, ValueError):
                raise HTTPException(404, "voice not found") from None
        for vid in set(body.line_voices.values()) | set(body.speaker_voices.values()):
            if vid:
                voice_or_404(vid)
        if (any(len(key) > 40 for key in body.speaker_voices)
                or any(len(value.strip()) > 40 for value in body.speakers.values())):
            raise HTTPException(400, "Speaker names must be at most 40 characters")
        with dub_lock:
            editable(job_id)
            script = None if body.reset else dub.load(d, index)
            script = script or dub.draft(segs_of(job_id), clip)
            current = {line.i: line.text for line in script.lines}
            speakers = {line.i: line.speaker for line in script.lines}
            if any(speakers.get(i) != speaker for i, speaker in body.expected_speakers.items()):
                raise HTTPException(409, "Speaker labels changed. Request new suggestions.")
            if any(current.get(i) != text for i, text in body.expected_texts.items()):
                raise HTTPException(409, "The line changed after this suggestion. Request a new suggestion.")
            for line in script.lines:
                if line.i in body.speakers:
                    line.speaker = body.speakers[line.i].strip()
                if line.i in body.line_voices:
                    line.voice_override = body.line_voices[line.i]
            for speaker, vid in body.speaker_voices.items():
                if vid:
                    script.speaker_voices[speaker] = vid
                else:
                    script.speaker_voices.pop(speaker, None)
            if len(script.speaker_voices) > 30:
                raise HTTPException(400, "Use up to 30 speaker assignments")
            dub.save(d, dub.edit(script, body.voice, body.version, body.texts))
        return get_dub(job_id, index)

    @app.get("/api/jobs/{job_id}/clips/{index}/dub/lines/{line_id}/source-audio")
    def dub_source_line(job_id: str, index: int, line_id: int) -> Response:
        _, clip = clip_of(job_id, index)
        script = dub.load(store.dir(job_id), index) or dub.draft(segs_of(job_id), clip)
        line = next((ln for ln in script.lines if ln.i == line_id and clip.start <= ln.start < clip.end), None)
        if line is None or line.end <= line.start:
            raise HTTPException(404, "Source line not found")
        try:
            audio = dub.source_line_audio(source_of(job_id), line.start, min(clip.end, line.end))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/jobs/{job_id}/clips/{index}/dub/speaker-suggestions")
    def suggest_dub_speakers(job_id: str, index: int) -> dict:
        editable(job_id)
        _, clip = clip_of(job_id, index)
        script = dub.load(store.dir(job_id), index) or dub.draft(segs_of(job_id), clip)
        source = source_of(job_id)
        try:
            turns = visible_speaker_turns(source, clip.start, clip.end)
        except (OSError, ImportError, RuntimeError, ValueError) as error:
            raise HTTPException(422, "Could not analyze visible speakers for this clip") from error
        speakers = dub.suggest_speakers(script, clip.start, clip.end, turns)
        return {"speakers": speakers, "expected_texts": {ln.i: ln.text for ln in script.lines if ln.i in speakers}}

    @app.post("/api/jobs/{job_id}/clips/{index}/dub/lines/{line_id}/suggest")
    def suggest_dub_line(job_id: str, index: int, line_id: int, shorten: bool = False) -> dict:
        def propose():
            editable(job_id)
            _, clip = clip_of(job_id, index)
            segs = segs_of(job_id)
            script = dub.load(store.dir(job_id), index) or dub.draft(segs, clip)
            if not shorten and line_id not in dub.script_warnings(segs, clip, script):
                raise ValueError("This line no longer needs a suggested fix")
            line = next((line for line in script.lines if line.i == line_id and line.text.strip()
                         and clip.start <= line.start < clip.end), None)
            if line is None:
                raise ValueError("This narration line is no longer in the short")
            llm = llm_factory()
            try:
                text = dub.suggest_shorter(line, llm) if shorten else dub.suggest_repair(segs, script, line, llm)
                return {"original": line.text, "text": text}
            finally:
                llm.unload()

        return gpu_work(propose)

    @app.post("/api/jobs/{job_id}/clips/{index}/dub/generate")
    def generate_dub(job_id: str, index: int, body: DubGenerate | None = None) -> dict:
        """Speak the lines that need it (or just `lines`) in the chosen voice and lay out the dub track.
        A short rendered with it is marked to re-render."""
        d = store.dir(job_id)

        def generate():
            editable(job_id)
            _, clip = clip_of(job_id, index)
            activate = bool(body and body.activate)
            script = dub.activation_script(d, clip) if activate else dub.load(d, index)
            if script is None:
                raise ValueError("choose a voice first")
            if activate and body.lines is not None:
                raise ValueError("Generate all narration lines before using this voice in the short")
            script = dub.save(d, dub.generate(d, script, clip, body.lines if body else None))

            def use_voice(c: Clip) -> None:
                if activate:
                    c.options = c.options.model_copy(update={"dub": True})
                if c.file and c.options.dub:
                    c.stale = True

            if activate or clip.options.dub:
                store.update_clip(job_id, index, use_voice)

        def locked_generate():
            with dub_lock:
                generate()

        gpu_work(locked_generate)
        return get_dub(job_id, index)

    @app.post("/api/jobs/{job_id}/clips/{index}/dub/preview")
    def preview_dub(job_id: str, index: int, body: NarrationOptions | None = None) -> dict:
        editable(job_id)
        clip_of(job_id, index)
        d = store.dir(job_id)
        with TemporaryDirectory(prefix=".dub-preview-", dir=d) as folder:
            staged = Path(folder)

            def generate():
                editable(job_id)
                _, clip = clip_of(job_id, index)
                if body is not None:
                    clip = clip.model_copy(update={"options": clip.options.model_copy(
                        update={"dub_pacing": body.pacing, "dub_smart_max_seconds": body.smart_max_seconds})})
                script = dub.load(d, index)
                if not script or not script.voice:
                    raise ValueError("Choose a voice before previewing")
                line = next((ln for ln in script.lines
                             if clip.start <= ln.start < clip.end and ln.text.strip()), None)
                if line is None:
                    raise ValueError("There are no narration lines in the current clip range")
                sample = script.model_copy(deep=True)
                for other in sample.lines:
                    other.file, other.seconds = None, None
                sample = dub.generate(staged, sample, clip, [line.i], learn_pace=False)
                return clip, sample

            clip, sample = gpu_work(generate)
            try:
                file = dub.preview(d, staged, clip, sample)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            except RuntimeError as e:
                raise HTTPException(502, str(e)) from None
        return {"file": file.name}

    @app.delete("/api/jobs/{job_id}/clips/{index}/dub")
    def delete_dub(job_id: str, index: int) -> Job:
        """Back to the original voice: the script and its audio go; a short using it is marked to re-render."""
        editable(job_id)
        _, clip = clip_of(job_id, index)
        d = store.dir(job_id)

        def off(c: Clip) -> None:
            if c.options.dub:
                c.options = c.options.model_copy(update={"dub": None})
                c.stale = bool(c.file)
        with dub_lock:
            editable(job_id)
            for f in d.glob(f"dub_{index}_*.wav"):
                f.unlink(missing_ok=True)
            for f in (d / f"dub_{index}.wav", d / f"dub_{index}.preview.mp4", dub.path(d, index)):
                f.unlink(missing_ok=True)
            return store.update_clip(job_id, index, off)

    @app.get("/api/jobs/{job_id}/clips/{index}/people")
    def clip_people(job_id: str, index: int) -> list[dict]:
        """Faces seen in the clip, left to right, with a thumbnail — choose who the crop follows (options.follow)."""
        _, clip = clip_of(job_id, index)
        found = people(source_of(job_id), clip.start, clip.end)
        return [{**{k: v for k, v in p.items() if k != "jpeg"}, "img": data_url(p["jpeg"])} for p in found]

    @app.get("/api/jobs/{job_id}/clips/{index}/crop")
    def clip_crop(job_id: str, index: int) -> dict:
        """For the crop editor: source size, crop width per format (fraction of the source width), where the last
        render's tracker put the crop (SOURCE t, centre x) and your hand-set keys."""
        _, clip = clip_of(job_id, index)
        src_w, src_h = source_size(source_of(job_id))
        if not src_w:
            raise HTTPException(400, "can't read the source video")
        crop = {a: min(1.0, round(int(src_h * w / h) // 2 * 2 / src_w, 4)) for a, ((w, h), _) in ASPECTS.items()}
        tracked = tracked_path(store.dir(job_id), f"short_{index}", src_w, int(crop["9:16"] * src_w), clip.start) \
            if clip.reframe in ("face", "speaker", "manual") else []
        return {"src_w": src_w, "src_h": src_h, "crop": crop, "mode": clip.reframe, "tracked": tracked,
                "keys": [k.model_dump() for k in clip.options.crop_keys or []]}

    @app.get("/api/jobs/{job_id}/clips/{index}/cover/frames")
    def cover_frames(job_id: str, index: int) -> list[dict]:
        """The best few cover frames (source time + small preview), best first."""
        _, clip = clip_of(job_id, index)
        video = source_of(job_id)
        return [{**c, "img": data_url(cover.preview(video, c["t"], c["x"]))} for c in cover.candidates(video, clip)]

    @app.post("/api/jobs/{job_id}/clips/{index}/cover", response_model=Job)
    def edit_cover(job_id: str, index: int, body: CoverEdit) -> Job:
        """(Re)make the cover image: new text and/or frame (`t`), or `auto` for the best frame again."""
        editable(job_id)
        _, clip = clip_of(job_id, index)
        video = source_of(job_id)
        work = clip.model_copy(deep=True)
        if body.text is not None:
            work.cover_text = " ".join(body.text.split())
        if body.style is not None:
            if body.style not in ("cutout", "frame"):
                raise HTTPException(400, "style must be cutout or frame")
            work.cover_style = body.style
        if body.auto:
            work.cover_t = None
        elif body.t is not None:
            if not work.start <= body.t <= work.end:
                raise HTTPException(400, "the cover frame must be inside the clip")
            work.cover_t = round(body.t, 2)
        try:
            cover.make_cover(video, work, store.dir(job_id), get_preset(work.options.caption_preset).font)
        except Exception as e:
            raise HTTPException(500, f"cover failed: {e}") from e

        def apply(c: Clip) -> None:
            c.cover, c.cover_text, c.cover_t = work.cover, work.cover_text, work.cover_t
            c.cover_style, c.cover_cutout = work.cover_style, work.cover_cutout
            c.cover_version = max(c.cover_version, work.cover_version)

        try:
            return store.update_clip(job_id, index, apply)
        except KeyError:
            raise HTTPException(404, "clip not found") from None

    @app.post("/api/jobs/{job_id}/clips/{index}/quote", response_model=Job)
    def edit_quote(job_id: str, index: int, body: QuoteEdit) -> Job:
        """(Re)make the quote cards: your own line (`text`, kept as you wrote it), or "" to pick a line from the
        clip again."""
        editable(job_id)
        _, clip = clip_of(job_id, index)
        d = store.dir(job_id)
        work = clip.model_copy(deep=True)
        if body.text is not None:
            work.quote = quote.tidy(body.text)
            work.quote_custom = bool(work.quote)
        try:
            quote.make_cards(source_of(job_id), work, d, transcript_edit.load(d), load_meta(d),
                             get_preset(work.options.caption_preset).font)
        except Exception as e:
            raise HTTPException(500, f"quote cards failed: {e}") from e

        def apply(c: Clip) -> None:
            c.quote, c.quote_custom, c.quote_cards = work.quote, work.quote_custom, work.quote_cards
            c.quote_version = max(c.quote_version, work.quote_version)

        try:
            return store.update_clip(job_id, index, apply)
        except KeyError:
            raise HTTPException(404, "clip not found") from None

    @app.get("/api/library")
    def library_info() -> dict:
        """Where the history lives (folders + spreadsheet) — shown in the UI so you can open it."""
        root = library.root.resolve()
        return {"dir": str(root), "xlsx": str(root / XLSX), "windows_dir": windows_path(root),
                "source_cache": source_cache_usage()}

    @app.get("/api/library/lookup")
    def library_lookup(url: str) -> dict:
        """Has this video been done before? Earlier jobs for the same video (any link form), newest first."""
        return {"matches": library.lookup(url)}

    # ---------------------------------------------------------------- find videos for a topic

    @app.get("/api/discover")
    def discover_search(topic: str, cc: bool = True, min_minutes: float = 8, max_minutes: float = 90,
                        n: int = 15, queries: list[str] | None = Query(None)) -> dict:  # noqa: B008
        """YouTube search (yt-dlp, no API key) → long-form results; cc=True: Creative Commons only. Each result
        says how many times you've already done that video. Score them with /api/discover/score.
        `queries` (smart search: from /api/discover/expand) are all searched and merged instead of `topic`."""
        if not topic.strip():
            raise HTTPException(400, "type a topic")
        kw = {"n": max(1, min(n, 30)), "cc": cc, "min_minutes": min_minutes, "max_minutes": max_minutes}
        try:
            qs = [q for q in (queries or []) if q.strip()][:5]
            found = discover.search_many(qs, **kw) if qs else discover.search(topic, **kw)
        except Exception as e:
            raise HTTPException(502, f"YouTube search failed: {str(e)[:200]}") from e
        for r in found:
            r["done"] = len(library.lookup(r["url"]))
        return {"results": found}

    def with_gemma(work):
        """Smart search runs Gemma, which needs the GPU: wait our turn briefly, 409 while a job holds it."""
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "Gemma is busy with a job — use plain search, or try again when it finishes")
        try:
            return work(llm_factory())
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(502, f"Gemma: {e}") from e
        except Exception as e:
            raise HTTPException(502, f"Gemma failed: {str(e)[:200]}") from e
        finally:
            GPU_LOCK.release()

    @app.post("/api/discover/expand")
    def discover_expand(body: SmartTopic) -> dict:
        """Smart search, step 1: Gemma turns the topic into a few different YouTube searches (topic always first)."""
        return {"queries": with_gemma(lambda llm: discover.expand_queries(body.topic, llm))}

    @app.post("/api/discover/judge")
    def discover_judge(body: SmartJudge) -> dict:
        """Smart search, step 2: Gemma reads the video's best caption moments and rates it 1-10 for the topic."""
        return with_gemma(lambda llm: discover.judge(body.id, body.topic, llm))

    @app.get("/api/discover/score")
    def discover_score(id: str, topic: str) -> dict:  # noqa: A002 - the video id, as in YouTube's URLs
        """How much of one video is about `topic`, from its captions (no download): score, moments, licence."""
        try:
            return discover.score(id, topic)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except Exception as e:
            raise HTTPException(502, f"couldn't read that video: {str(e)[:200]}") from e

    @app.get("/api/library/history.xlsx")
    def library_xlsx() -> FileResponse:
        library.flush()  # anything saved in the last couple of seconds
        path = library.root / XLSX
        if not path.exists():
            raise HTTPException(404, "no history yet")
        return FileResponse(path, filename=XLSX,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

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
