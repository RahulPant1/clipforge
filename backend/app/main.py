"""FastAPI app. Run: uvicorn app.main:app --reload --port 8000 (from backend/)."""
import base64
import json
import logging
import re
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .config import settings
from .events import job_events
from .jobs import GPU_LOCK, JobStore, Worker
from .library import XLSX, Library, video_key, windows_path
from .models import (
    BatchCreate,
    BatchLinks,
    BrandKit,
    BrandVoice,
    Clip,
    ClipAdd,
    ClipEdit,
    CompareRequest,
    CoverEdit,
    Insert,
    Job,
    JobCreate,
    JobMode,
    JobStatus,
    Post,
    PostEdit,
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
    WordEdit,
)
from .pipeline import (
    batch,
    brand,
    cover,
    discover,
    emoji,
    hinglish,
    inserts,
    judge,
    learn,
    quote,
    scenes,
    sfx,
    transcript_edit,
    tts,
    vocabulary,
    voice,
    waveform,
)
from .pipeline.content import ANGLES, NAMES, ContentContext, clip_text, generate_post, load_meta, write_posts_md
from .pipeline.export import build_pack
from .pipeline.history import get_history
from .pipeline.hooks import write_variants
from .pipeline.jumpcut import clean_removed
from .pipeline.llm import OllamaClient
from .pipeline.presets import POSITIONS, all_presets, get_preset
from .pipeline.reframe import people, source_size, tracked_path
from .pipeline.render import ASPECTS, FEED, extra_aspects
from .watch import Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SAFE_NAME = re.compile(r"^[\w.\-]+$")


def create_app(pipeline=None, start_worker: bool = True, llm_factory=OllamaClient, steps=None) -> FastAPI:
    store = JobStore(settings.data_dir)
    worker = Worker(store, pipeline, steps)
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
        out = gpu_work(lambda: tts.speak(vid, [body.text], body.language or None, version=body.version))
        return {**out[0], "voice": tts.load(vid)}

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
                           channel=body.channel, purpose=body.purpose)
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
                               title=known.title if known else "", channel=known.channel if known else "")
            worker.submit(job.id, u)
            jobs.append(job)
        return {"jobs": jobs}

    @app.post("/api/jobs/upload", response_model=Job, status_code=201)
    def upload_job(file: UploadFile = File(...), num_clips: int = Form(5), mode: JobMode = Form(JobMode.review),
                   topic: str = Form("", max_length=200), hinglish: bool | None = Form(None),
                   purpose: Purpose = Form("create")) -> Job:
        job = store.create(file.filename or "upload.mp4", num_clips, mode, topic, title=Path(file.filename or "").stem,
                           hinglish=settings.hinglish_default if hinglish is None else hinglish, purpose=purpose)
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
        worker.submit_learn(job.id)
        return store.get(job_id)

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
        job = store.update(job_id, status=JobStatus.queued, stage=Stage.queued, progress=0.0, error=None,
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
        _, clip = clip_of(job_id, index)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
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
        _, clip = clip_of(job_id, index)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
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

    @app.post("/api/jobs/{job_id}/clips/{index}/review", response_model=Job)
    def review_clip(job_id: str, index: int) -> Job:
        """Clip judge on the clip as it is now (after your trims): verdict, what's missing, a suggested fix."""
        editable(job_id)
        _, clip = clip_of(job_id, index)
        if not GPU_LOCK.acquire(timeout=2):
            raise HTTPException(409, "GPU is busy with a job — try again when it finishes")
        d = store.dir(job_id)
        try:
            try:
                segs = [Segment(**x) for x in json.loads((d / "transcript.json").read_text())]
                result = judge.review(segs, clip, llm_factory())
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
        return {"dir": str(root), "xlsx": str(root / XLSX), "windows_dir": windows_path(root)}

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
