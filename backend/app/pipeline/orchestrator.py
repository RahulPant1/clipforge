"""Pipeline phases.

Review mode (default):  prepare()  → you edit/approve clips in the UI →  produce()
Auto mode:              run_pipeline() = prepare() + produce()
Single clip re-render:  render_one()

prepare: download → transcribe → Gemma picks candidates (more than you asked for; best N pre-approved)
produce: render approved clips → generate optional posts and covers
"""
import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Clip, LearnNotes, Segment, Stage
from . import hinglish, inserts, judge, learn, scenes, transcript_edit
from .content import ContentContext, clip_text, generate_all, load_meta, write_posts_md
from .cover import make_cover
from .download import download
from .highlights import select_highlights
from .history import get_history
from .ingest_cache import restore_transcript, source_for
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


def ingest(source: str, job_dir: Path, report: Reporter, llm: LLM,
           romanize: bool = False, *, whisper_model: str | None = None) -> tuple[Path, list[Segment], float]:
    """Video in (download / copy an upload — both reused when already there) → transcript (cached) → Hinglish if
    asked. Shared by prepare() (shorts) and learn_pipeline() (study notes)."""
    job_dir.mkdir(parents=True, exist_ok=True)

    if source.startswith(("http://", "https://")) and not (job_dir / "source.mp4").is_file():
        report(Stage.downloading, 0, "Getting source video")
    video = source_for(source, job_dir, lambda url, staged: download(
        url, staged, lambda p, m: report(Stage.downloading, p, m)))

    def build_transcript(staged: Path) -> None:
        llm.unload()  # Whisper may need the memory held by a previous interactive model call.
        report(Stage.transcribing, 0, f"Loading Whisper ({whisper_model or settings.whisper_model})")
        raw = transcribe(video, staged, lambda p, m: report(Stage.transcribing, p, m),
                         **({"whisper_model": whisper_model} if whisper_model else {}))
        if not (staged / "transcript.json").exists():
            transcript_edit.save(staged, raw)

    restore_transcript(video, job_dir, build_transcript, whisper_model=whisper_model)
    segs = load_segments(job_dir)
    if romanize and segs and hinglish.romanize(job_dir):  # Hindi in Devanagari → Hinglish before anything reads it
        report(Stage.transcribing, 1.0, "Hindi → Hinglish (Latin letters)")
        segs = transcript_edit.load(job_dir)
    return video, segs, probe_duration(video)


def prepare(source: str, job_dir: Path, num_clips: int, report: Reporter,
            llm: LLM | None = None, candidates: int | None = None, topic: str = "",
            romanize: bool = False, *, whisper_model: str | None = None) -> list[Clip]:
    llm = llm or OllamaClient()
    video, segs, duration = ingest(source, job_dir, report, llm, romanize,
                                   **({"whisper_model": whisper_model} if whisper_model else {}))
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
            seen = scene_lines(video, job_dir, llm, report, trace)
            base = 0.4 if seen is not None else 0.0  # the scene pass had the first part of the bar
            report(Stage.selecting, base, f"Asking Gemma for moments about “{topic}”" if topic
                   else "Asking Gemma for highlights")
            clips = select_highlights(segs, num_clips, llm, lambda p, m: on_select(base + (1 - base) * p, m),
                                      keep=candidates or num_clips, trace=trace, topic=topic,
                                      **({"scenes": seen} if seen else {}))  # only when there are any
            judge_clips(segs, clips, num_clips, llm, report, trace, scenes=seen or None)
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


def learn_pipeline(source: str, job_dir: Path, report: Reporter, llm: LLM | None = None,
                   romanize: bool = False, *, whisper_model: str | None = None) -> LearnNotes:
    """Learning mode: video in → transcript → (scene index) → study notes in learn.json + notes.md. Works on an
    existing shorts job too (download and transcript are reused)."""
    llm = llm or OllamaClient()
    video, segs, duration = ingest(source, job_dir, report, llm, romanize,
                                   **({"whisper_model": whisper_model} if whisper_model else {}))
    meta = load_meta(job_dir)
    try:
        if settings.scene_index != "off":  # screens/slides: what's shown is part of what's taught
            scene_lines(video, job_dir, llm, report, {})
        notes = learn.build(segs, llm, duration, meta.get("title", ""),
                            lambda p, m: report(Stage.writing, p, m))
    finally:
        llm.unload()
    learn.save(notes, job_dir, meta.get("url", ""))
    report(Stage.done, 1.0, f"Study notes ready: {len(notes.notes)} notes, {len(notes.quiz)} quiz questions")
    return notes


def judge_clips(segs: list[Segment], clips: list[Clip], n: int, llm: LLM, report: Reporter, trace: dict,
                scenes: list | None = None) -> None:
    """Clip judge on every candidate while Gemma is loaded (judge.py); a rejected pre-approved clip gives its place
    to the best spare that isn't rejected. Never fails prepare."""
    if not settings.judge_enabled or not clips or not any(s.words for s in segs):
        return
    try:
        counts = judge.review_all(segs, clips, llm, lambda p, m: report(Stage.selecting, 0.9 + 0.1 * p, m),
                                  scenes=scenes)
        trace["judge"] = {"verdicts": counts, "swapped": judge.rebalance(clips, n), "scenes": bool(scenes),
                          "validation": None,
                          "clips": [{"start": c.start, "end": c.end, "verdict": c.review.verdict,
                                     "fix": c.review.fix.model_dump() if c.review.fix else None,
                                     "issues": [f"{x.key}: {x.note}" for x in c.review.checks if not x.ok]}
                                    for c in clips if c.review]}
        trace["judge"]["validation"] = judge.validate(clips, n)
    except Exception as e:
        log.exception("clip judge failed — candidates kept unjudged")
        trace["judge"] = {"error": str(e)[:300]}


def apply_fixes(clips: list[Clip]) -> int:
    """Auto mode (nobody reviews): move "trim" clips to their suggested boundaries. Returns how many moved."""
    moved = 0
    for c in clips:
        if c.review and c.review.verdict == "trim" and c.review.fix:
            c.start, c.end = c.review.fix.start, c.review.fix.end
            moved += 1
    return moved


def scene_lines(video: Path, job_dir: Path, llm: LLM, report: Reporter, trace: dict) -> list | None:
    """Scene index for a speech video (scenes.py) → the scenes worth an ON SCREEN line in the selection prompt.
    None = the pass didn't run (SCENE_INDEX=off); [] = ran but nothing to add. Never fails the job."""
    if settings.scene_index == "off":
        return None
    try:
        index = scenes.build(video, job_dir, llm, lambda p, m: report(Stage.selecting, 0.4 * p, m),
                             title=load_meta(job_dir).get("title", ""))
    except Exception as e:
        log.exception("scene index failed — selecting from the transcript only")
        trace["scene_index"] = {"mode": "failed", "reason": str(e)[:300]}
        return []
    trace["scene_index"] = {"mode": index.mode, "reason": index.reason, "described": len(index.described),
                            "cuts_per_min": index.cuts_per_min, "face_share": index.face_share}
    return index.for_prompt()


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


def produce(job_dir: Path, clips: list[Clip], report: Reporter, llm: LLM | None = None, *,
            on_clip: Callable[[Clip], None] | None = None, model_lock=None) -> list[Clip]:
    """Make approved shorts available before generating their optional posts."""
    llm = llm or OllamaClient()
    segs = load_segments(job_dir)
    todo = [c for c in clips if c.approved]
    if not todo:
        raise RuntimeError("No clips approved — approve at least one clip first.")

    need_cards = [c for c in todo if not c.inserts_checked] if settings.inserts_suggest else []
    if need_cards and settings.inserts_auto:  # Approved cards affect the video itself.
        with model_lock or nullcontext():
            try:
                for i, clip in enumerate(need_cards, 1):
                    report(Stage.rendering, 0.0, f"Short {clip.index}: stat cards ({i}/{len(need_cards)})")
                    suggest_cards(clip, segs, llm)
            finally:
                if settings.unload_llm_after_job:
                    llm.unload()
        need_cards = []

    video = job_dir / "source.mp4"
    to_render = [c for c in todo if not c.file or c.stale]
    rendered = set()
    for i, clip in enumerate(to_render):
        report(Stage.rendering, i / len(to_render), f"Rendering short {clip.index} ({i + 1}/{len(to_render)})")
        try:
            clip.file = render_clip(video, clip, segs, job_dir)
            rendered.add(clip.index)
        except Exception as e:
            log.exception("render failed for clip %s", clip.index)
            clip.render_error = str(e)[:300]
        if on_clip:
            on_clip(clip)

    need_posts = [c for c in todo if not c.content] if settings.content_enabled else []
    if need_posts or need_cards:
        meta, history = load_meta(job_dir), get_history()
        with model_lock or nullcontext():
            try:
                for i, clip in enumerate(need_cards, 1):
                    report(Stage.writing, 0.0, f"Short {clip.index}: stat cards ({i}/{len(need_cards)})")
                    suggest_cards(clip, segs, llm)
                for i, clip in enumerate(need_posts):
                    ctx = ContentContext(clip=clip, text=clip_text(segs, clip), video_title=meta.get("title", ""),
                                         channel=meta.get("channel", ""), url=meta.get("url", ""))
                    clip.content = generate_all(
                        ctx, llm, history, job_dir,
                        lambda p, m, i=i, n=clip.index: report(
                            Stage.writing, (i + p) / len(need_posts), f"Short {n}: {m}"))
                    write_posts_md(clip, job_dir)
                    if on_clip:
                        on_clip(clip)
            finally:
                if settings.unload_llm_after_job:
                    llm.unload()

    for clip in todo:
        if clip.file and (clip.index in rendered or not clip.cover or (settings.quote_cards and not clip.quote_cards)):
            cover_for(video, clip, job_dir)
    report(Stage.done, 1.0, f"{sum(1 for c in todo if c.file)} shorts ready")
    return clips


def suggest_cards(clip: Clip, segs: list[Segment], llm: LLM) -> None:
    """Stat card suggestions for one clip (INSERTS_AUTO: approved, and a rendered short goes stale). Never raises."""
    try:
        new = inserts.suggest(clip, segs, llm, approve=settings.inserts_auto)
    except Exception:
        log.exception("stat cards failed for clip %s", clip.index)
        return
    clip.inserts = inserts.merge(clip, new)
    clip.inserts_checked = True
    if clip.file and any(i.approved for i in new):
        clip.stale = True


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
                 llm: LLM | None = None, topic: str = "", romanize: bool = False, *,
                 on_clip: Callable[[Clip], None] | None = None, whisper_model: str | None = None) -> list[Clip]:
    """Auto mode: everything end to end."""
    llm = llm or OllamaClient()
    clips = prepare(source, job_dir, num_clips, report, llm, topic=topic, romanize=romanize,
                    **({"whisper_model": whisper_model} if whisper_model else {}))
    apply_fixes(clips)  # no review step in auto mode: the judge's trims are taken as they are
    clips = produce(job_dir, clips, report, llm, on_clip=on_clip)
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
    learn: Callable = learn_pipeline
