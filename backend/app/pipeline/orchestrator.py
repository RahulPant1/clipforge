"""Pipeline phases.

Review mode (default):  prepare()  → you edit/approve clips in the UI →  produce()
Auto mode:              run_pipeline() = prepare() + produce()
Single clip re-render:  render_one()

prepare: download → transcribe → Gemma picks candidates (more than you asked for; best N pre-approved)
produce: Gemma writes posts for approved clips (unless already written) → unload Gemma → render approved clips
"""
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Clip, Segment, Stage
from . import hinglish, transcript_edit
from .content import ContentContext, clip_text, generate_all, load_meta, write_posts_md
from .cover import make_cover
from .download import download
from .highlights import select_highlights
from .history import get_history
from .llm import LLM, OllamaClient
from .nospeech import probe_duration, select_no_speech
from .presets import get_preset
from .quote import make_cards
from .render import render_clip
from .transcribe import transcribe, words_per_minute

log = logging.getLogger(__name__)

# (stage, progress 0..1, message)
Reporter = Callable[[Stage, float, str], None]

MIN_WPM = 30  # below this the video is mostly music/gameplay/sport → no-speech mode (nospeech.py)
CANDIDATE_FACTOR = 2  # review mode shows 2x the requested clips so you have real choice


def load_segments(job_dir: Path) -> list[Segment]:
    return [Segment(**s) for s in json.loads((job_dir / "transcript.json").read_text())]


def prepare(source: str, job_dir: Path, num_clips: int, report: Reporter,
            llm: LLM | None = None, candidates: int | None = None, topic: str = "",
            romanize: bool = False) -> list[Clip]:
    job_dir.mkdir(parents=True, exist_ok=True)
    llm = llm or OllamaClient()
    llm.unload()  # in case a "new angle" request left Gemma in VRAM — Whisper needs the room

    local = Path(source)
    if local.exists():
        video = job_dir / "source.mp4"
        if not video.exists():
            shutil.copy(local, video)
        if not (job_dir / "meta.json").exists():
            (job_dir / "meta.json").write_text(json.dumps({"title": local.stem, "channel": "", "url": ""}))
    else:
        report(Stage.downloading, 0, "Starting download")
        video = download(source, job_dir, lambda p, m: report(Stage.downloading, p, m))

    report(Stage.transcribing, 0, "Loading Whisper")
    segs = transcribe(video, job_dir, lambda p, m: report(Stage.transcribing, p, m))
    if romanize and segs and hinglish.romanize(job_dir):  # Hindi in Devanagari → Hinglish before anything reads it
        report(Stage.transcribing, 1.0, "Hindi → Hinglish (Latin letters)")
        segs = transcript_edit.load(job_dir)
    duration = probe_duration(video)
    no_speech = not segs or words_per_minute(segs, duration) < MIN_WPM
    if no_speech and not settings.no_speech_mode:
        raise RuntimeError("Not enough speech found (and NO_SPEECH_MODE is off).")

    trace: dict = {}
    on_select = lambda p, m: report(Stage.selecting, p, m)  # noqa: E731
    try:
        if no_speech:
            report(Stage.selecting, 0, "Hardly any speech: finding highlights in the sound and picture")
            clips = select_no_speech(video, num_clips, llm, on_select, keep=candidates or num_clips,
                                     title=load_meta(job_dir).get("title", ""), cache=job_dir / "signals.json",
                                     trace=trace, topic=topic)
        else:
            report(Stage.selecting, 0, f"Asking Gemma for moments about “{topic}”" if topic
                   else "Asking Gemma for highlights")
            clips = select_highlights(segs, num_clips, llm, on_select, keep=candidates or num_clips, trace=trace,
                                      topic=topic)
    finally:
        llm.unload()
    if not clips and 0 < duration <= settings.max_clip_seconds:
        clips = [whole_video_clip(duration, trace, load_meta(job_dir).get("title", ""))]
    # every pick Gemma made and why it was kept/dropped — raw data for prompt eval (python -m app.eval)
    (job_dir / "candidates.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False, default=str))
    if not clips and topic:
        raise RuntimeError(f"No moments about “{topic}” found in this video. Retry with a broader topic "
                           f"(or none) — the transcript is reused.")
    if not clips:
        raise RuntimeError("Gemma returned no usable clips. Try a bigger model or tweak the prompt.")
    report(Stage.selecting, 1.0, f"{len(clips)} candidates ready for review")
    return clips


def whole_video_clip(duration: float, trace: dict, title: str = "") -> Clip:
    """The source already fits in a Short (an ad, a reel): Gemma's picks inside it are all too short, so the whole
    video is the clip — titled with the best pick's title/hook/keywords when there was one."""
    picks = [i["raw"] for i in trace.get("items", []) if isinstance(i.get("raw"), dict)]
    best = max(picks, key=lambda r: r.get("score") if isinstance(r.get("score"), int) else 0, default={})
    score = best.get("score") if isinstance(best.get("score"), int) else 5
    trace["whole_video"] = True
    return Clip(index=1, start=0.0, end=round(duration, 2), title=str(best.get("title") or title or "Full video")[:80],
                hook=str(best.get("hook") or "")[:60], score=max(1, min(10, score)),
                keywords=[str(k) for k in best.get("keywords") or []][:8] if isinstance(best.get("keywords"), list)
                else [])


def produce(job_dir: Path, clips: list[Clip], report: Reporter, llm: LLM | None = None) -> list[Clip]:
    """Posts + renders for approved clips. Keeps hand-edited posts; renders only what's missing/stale."""
    llm = llm or OllamaClient()
    segs = load_segments(job_dir)
    todo = [c for c in clips if c.approved]
    if not todo:
        raise RuntimeError("No clips approved — approve at least one clip first.")

    need_posts = [c for c in todo if not c.content] if settings.content_enabled else []
    if need_posts:
        meta, history = load_meta(job_dir), get_history()
        try:
            for i, clip in enumerate(need_posts):
                ctx = ContentContext(clip=clip, text=clip_text(segs, clip), video_title=meta.get("title", ""),
                                     channel=meta.get("channel", ""), url=meta.get("url", ""))
                clip.content = generate_all(
                    ctx, llm, history, job_dir,
                    lambda p, m, i=i, n=clip.index: report(Stage.writing, (i + p) / len(need_posts), f"Short {n}: {m}"))
                write_posts_md(clip, job_dir)
        finally:
            if settings.unload_llm_after_job:
                llm.unload()

    video = job_dir / "source.mp4"
    to_render = [c for c in todo if not c.file or c.stale]
    for i, clip in enumerate(to_render):
        report(Stage.rendering, i / len(to_render), f"Rendering short {clip.index} ({i + 1}/{len(to_render)})")
        try:
            clip.file = render_clip(video, clip, segs, job_dir)
        except Exception as e:  # one bad render shouldn't lose the others
            log.exception("render failed for clip %s", clip.index)
            clip.render_error = str(e)[:300]
            continue
        cover_for(video, clip, job_dir)
    for clip in todo:  # rendered before covers / quote cards existed
        if clip.file and (not clip.cover or (settings.quote_cards and not clip.quote_cards)):
            cover_for(video, clip, job_dir)
    report(Stage.done, 1.0, f"{sum(1 for c in todo if c.file)} shorts ready")
    return clips


def cover_for(video: Path, clip: Clip, job_dir: Path) -> None:
    """Reel cover + quote cards next to the short; a failure is logged, never fails the render."""
    font = get_preset(clip.options.caption_preset).font
    try:
        make_cover(video, clip, job_dir, font)
    except Exception:
        log.exception("cover failed for clip %s", clip.index)
    if settings.quote_cards:
        try:  # after the cover: same frame, so the speaker cutout is reused
            make_cards(video, clip, job_dir, load_segments(job_dir), load_meta(job_dir), font)
        except Exception:
            log.exception("quote cards failed for clip %s", clip.index)


def render_one(job_dir: Path, clip: Clip) -> Clip:
    clip.file = render_clip(job_dir / "source.mp4", clip, load_segments(job_dir), job_dir)
    cover_for(job_dir / "source.mp4", clip, job_dir)
    return clip


def run_pipeline(source: str, job_dir: Path, num_clips: int, report: Reporter,
                 llm: LLM | None = None, topic: str = "", romanize: bool = False) -> list[Clip]:
    """Auto mode: everything end to end."""
    llm = llm or OllamaClient()
    clips = prepare(source, job_dir, num_clips, report, llm, topic=topic, romanize=romanize)
    clips = produce(job_dir, clips, report, llm)
    if not any(c.file for c in clips):
        raise RuntimeError("All renders failed: " + "; ".join(c.render_error or "" for c in clips)[:300])
    return clips


@dataclass
class Steps:
    """Injectable pipeline phases (tests swap in fakes)."""
    prepare: Callable = prepare
    produce: Callable = produce
    render_one: Callable = render_one
    run: Callable = run_pipeline
