"""Step 4 — jump cuts + face-tracking crop + captions + FFmpeg 9:16 render (+ 4:5 / 1:1 feed versions)."""
import logging
import math
import subprocess
from dataclasses import replace
from pathlib import Path

from ..config import settings
from ..files import atomic_output
from ..models import BrandKit, Clip, RenderOptions, Segment
from . import ambience, brand, broll, dub, emoji, insert_render, inserts, sfx
from .captions import _text as caption_text
from .captions import build_ass, build_hook_ass, build_srt, chunk_words, text_scale
from .fonts import font_for, fontsdir_option
from .jumpcut import CutPlan, audio_cut_graph, audio_silences, plan_cuts, video_select_expr, zoom_graph
from .presets import get_preset, placement
from .reframe import ReframePlan, plan_reframe

log = logging.getLogger(__name__)


def _encoder_args() -> list[str]:
    if "nvenc" in settings.encoder:
        return ["-c:v", settings.encoder, "-preset", "p5", "-cq", "23"]
    return ["-c:v", settings.encoder, "-preset", "veryfast", "-crf", "21"]


# Output formats: canvas size and file-name suffix. 9:16 is the short itself (short_N.mp4); the others are feed
# versions rendered next to it from the same cut plan, effects track and brand kit (short_N.45.mp4, short_N.11.mp4).
ASPECTS: dict[str, tuple[tuple[int, int], str]] = {
    "9:16": ((1080, 1920), ""),
    "4:5": ((1080, 1350), ".45"),  # Instagram feed, LinkedIn, Facebook
    "1:1": ((1080, 1080), ".11"),  # X, LinkedIn
}
FEED = [a for a in ASPECTS if a != "9:16"]


def extra_aspects(o: RenderOptions) -> list[str]:
    """Feed versions this short gets besides the 9:16: its own choice, else EXTRA_ASPECTS."""
    want = o.aspects if o.aspects is not None else [a.strip() for a in settings.extra_aspects.split(",")]
    return [a for a in FEED if a in want]


def aspect_file(index: int, aspect: str) -> str:
    return f"short_{index}{ASPECTS[aspect][1]}.mp4"


def render_clip(video: Path, clip: Clip, segs: list[Segment], job_dir: Path, *, preview: bool = False,
                preview_range: tuple[float, float] = (0, 10)) -> str:
    """Renders short_<n>.mp4 (+ .srt export, .jpg thumbnail) in job_dir, plus the feed versions the short asks for
    (short_<n>.45.mp4 / .11.mp4; a failed feed version never fails the short — it's left out of clip.formats).

    Also fills clip.cut_seconds / out_duration / reframe / fit_seconds / formats. Returns the mp4 filename.
    """
    name = f"short_{clip.index}" + (".preview" if preview else "")
    o = clip.options
    jumpcut = settings.jumpcut_enabled if o.jumpcut is None else o.jumpcut
    removed = clip.removed
    dubbed = dub.track_for(job_dir, clip)  # Replace voice: the new speech + its words instead of the original
    voice_track = None
    duration = clip.duration
    ambience_track = ()
    if dubbed:
        voice_track, segs = dubbed
        jumpcut, removed = False, []  # the new speech has its own pauses; struck words were in the old one
        if o.dub_pacing != "source":
            duration = dub.output_duration(job_dir, clip)

    silences = audio_silences(video, clip.start, clip.end) if jumpcut and settings.jumpcut_audio else None
    plan = plan_cuts(segs, clip.start, clip.start + duration, enabled=jumpcut, silences=silences, removed=removed)

    kit = brand.load()
    card = brand.end_card_seconds(kit, o, plan.duration)
    if dubbed and o.dub_pacing == "smart" and o.dub_smart_max_seconds is not None:
        room = round(o.dub_smart_max_seconds - plan.duration, 6)
        card = min(card, room) if room >= 1.0 else 0.0
    total = plan.duration + card
    if preview and total - preview_range[0] < 1 / settings.output_fps:
        raise ValueError(f"Preview start must leave at least one frame in the {total:.2f} second export")
    if dubbed and o.dub_ambience and o.dub_ambience_level > 0:
        ambience_track = (ambience.track_for(video, job_dir, clip),)
    # Sound design: effects pre-mixed on one track (sfx.py), shared by every format
    timed = inserts.schedule(clip, plan, segs)  # stat cards: output-time windows, same in every format
    want_fx = settings.sfx_default if o.sfx is None else o.sfx
    evts = sorted([*sfx.events(plan, clip.keywords), *((t.start, "pop") for t in timed)]) if want_fx else []
    fx = sfx.write_track(evts, total, job_dir / f"{name}.sfx.wav") if want_fx else None
    if not fx:
        (job_dir / f"{name}.sfx.wav").unlink(missing_ok=True)

    errors: list[str] = []
    used: set[str] = set()  # insert frame folders this render uses (the rest are pruned)
    if preview:
        _encode(video, clip, plan, job_dir, name, (360, 640), kit, card, fx, timed, used, errors,
                voice_track, *ambience_track, preview_range=preview_range)
        return f"{name}.mp4"
    with atomic_output(job_dir / f"{name}.srt") as captions:
        captions.write_text(build_srt(plan.words), encoding="utf-8")
        try:
            rf = _encode(video, clip, plan, job_dir, name, ASPECTS["9:16"][0], kit, card, fx, timed, used, errors,
                         voice_track, *ambience_track)
        except RuntimeError as e:
            if not timed:
                raise
            log.warning("%s failed with stat cards, rendering without: %s", name, e)  # a card never costs the short
            errors.append(f"stat cards left out: {e}"[:200])
            timed = []
            rf = _encode(video, clip, plan, job_dir, name, ASPECTS["9:16"][0], kit, card, fx, timed, used, errors,
                         voice_track, *ambience_track)
    clip.cut_seconds = round(plan.removed, 2)
    clip.reframe = rf.mode
    clip.fit_seconds = round(clip.duration if rf.mode == "fit" else sum(b - a for a, b in rf.fit), 1)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", f"{name}.mp4",
                    "-frames:v", "1", "-vf", "scale=360:-1", f"{name}.jpg"],
                   cwd=job_dir, capture_output=True)

    formats = []
    wanted = extra_aspects(o)
    for aspect in FEED:
        base = f"{name}{ASPECTS[aspect][1]}"
        if aspect not in wanted:  # switched off: no stale feed file left behind
            for ext in (".mp4", ".ass", ".hook.ass", ".crop.cmd", ".end.png"):
                (job_dir / f"{base}{ext}").unlink(missing_ok=True)
            continue
        try:
            _encode(video, clip, plan, job_dir, base, ASPECTS[aspect][0], kit, card, fx, timed, used, errors,
                    voice_track, *ambience_track)
            formats.append(aspect)
        except Exception as e:  # the 9:16 is done and fine; say what's missing
            log.warning("%s %s version failed: %s", name, aspect, e)
            errors.append(f"{aspect} version failed: {e}")

    insert_render.prune(job_dir / "inserts" / name, used)
    clip.out_duration = round(total, 2)
    clip.formats = formats
    clip.brand_rev = kit.rev if brand.in_use(kit) else -1
    clip.version += 1
    clip.stale = False
    clip.render_error = "; ".join(dict.fromkeys(errors))[:500] or None
    return f"{name}.mp4"


def _retime_expression(anchors: list[tuple[float, float]]) -> str:
    expression = ""
    for (start, out), (end, out_end) in reversed(list(zip(anchors, anchors[1:], strict=False))):
        rate = (out_end - out) / (end - start)
        segment = f"({out:.8f}+(T-{start:.8f})*{rate:.8f})/TB"
        expression = f"if(lt(T,{end:.8f}),{segment},{expression})" if expression else segment
    return expression


def _encode(video: Path, clip: Clip, plan: CutPlan, job_dir: Path, name: str, size: tuple[int, int],
            kit: BrandKit, card: float, fx: Path | None, timed: list[inserts.Timed] | None = None,
            used: set[str] | None = None, errors: list[str] | None = None,
            voice_track: Path | None = None, ambience_track: Path | None = None, *,
            preview_range: tuple[float, float] | None = None) -> ReframePlan:
    """One output file `name`.mp4 on a `size` canvas (1080 wide). Returns how it was framed."""
    w, h = size
    o = clip.options
    reframe_mode = o.reframe or settings.reframe_mode
    caption_style = o.caption_style or settings.caption_style
    zoom = settings.jumpcut_zoom if o.zoom is None else o.zoom
    hook = settings.hook_overlay if o.hook is None else o.hook

    # Speaker-following crop (runs before jump-cut select, so it works in source-relative time)
    rf = plan_reframe(video, clip.start, clip.end, mode=reframe_mode, follow=o.follow, size=size, keys=o.crop_keys)
    cmd_file = None
    if rf.mode in ("face", "speaker", "manual"):
        cmd_file = f"{name}.crop.cmd"
        (job_dir / cmd_file).write_text(rf.sendcmd(), encoding="utf-8")

    middle = rf.mode == "split"  # stacked speakers: captions on the seam, not over the bottom face
    preset = get_preset(o.caption_preset)
    if kit.enabled and kit.accent and kit.accent_captions:  # karaoke active word in the brand colour
        preset = replace(preset, active=brand.ass_colour(kit.accent))
    if h != 1920:  # feed versions: smaller text for the smaller frame
        preset = replace(preset, size=text_scale(preset.size, h))
    font = preset.font
    cap_font = font
    if caption_style == "none":  # no-speech clips: nothing to caption (and Whisper invents words on music)
        subs = ""
    else:  # karaoke = word-by-word highlight; simple = whole line in the same look, no animation
        cap_font = font_for("".join(wd.word for wd in plan.words), font)  # Hindi → a font with Devanagari
        ass = build_ass(plan.words, clip.keywords, middle=middle, preset=preset, position=o.caption_position,
                        animate=caption_style != "simple", font=cap_font, height=h)
        (job_dir / f"{name}.ass").write_text(ass, encoding="utf-8")
        subs = f"ass={name}.ass{fontsdir_option(cap_font)}"

    fps = settings.output_fps
    vf = [f"fps={fps}", *rf.filters(cmd_file)]
    stretch = plan.duration / clip.duration if voice_track and o.dub_pacing != "source" else 1.0
    timing = dub.scene_timing(job_dir, clip, plan.duration) if voice_track and o.dub_pacing == "scene" else []
    if timing or stretch > 1.0001:
        expression = _retime_expression(timing) if timing else f"{stretch:.9f}*PTS"
        tail_stretch = ((timing[-1][1] - timing[-2][1]) / (timing[-1][0] - timing[-2][0])) if timing else stretch
        vf += [f"setpts='{expression}'", f"fps={fps}",
               f"tpad=stop_mode=clone:stop_duration={max(tail_stretch, 1) / fps:.6f}",
               f"trim=duration={plan.duration:.3f}"]
    af = []
    if len(plan.keeps) > 1 or plan.removed > 0.05:
        # Same frame-grid intervals for both streams (see jumpcut.snap_to_frames) → no A/V drift
        vf += [f"select='{video_select_expr(plan.keeps, fps)}'", f"setpts=N/{fps}/TB"]
        af = [audio_cut_graph(plan.keeps, settings.jumpcut_crossfade_ms / 1000)]
    vf += [f"scale={w}:{h}:force_original_aspect_ratio=decrease", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    if zoom and af:  # punch-in on alternate segments; output time, before captions so text never zooms
        vf.append(zoom_graph(plan.keeps, fps, settings.jumpcut_zoom_scale, size))
    picture_filters = len(vf)
    # Brand kit end card: the last frame held, blurred + darkened, after the last word (before captions, so
    # no caption freezes on it); the card itself is overlaid below. Never past max_clip_seconds.
    if card:
        vf += brand.freeze_tail(plan.duration, card)
        af = [*af, f"apad=pad_dur={card:.3f}"]
    if voice_track and o.dub_pacing == "smart" and o.dub_smart_max_seconds is not None:
        # A frame starting just before the limit can still extend past it at the output frame rate.
        last_frame = math.floor(o.dub_smart_max_seconds * fps + 1e-6)
        vf.append(f"trim=end_frame={last_frame}")
        af.append(f"atrim=duration={o.dub_smart_max_seconds:.6f}")
    if subs:
        vf.append(subs)
    hook_font = font_for(clip.hook, font)
    hook_ass = build_hook_ass(clip.hook, plan.duration, seam=middle, font=hook_font, height=h) if hook else ""
    if hook_ass:  # last, so the title card sits above captions (and never zooms)
        (job_dir / f"{name}.hook.ass").write_text(hook_ass, encoding="utf-8")
        vf.append(f"ass={name}.hook.ass{fontsdir_option(hook_font)}")

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video.resolve())]
    total = plan.duration + card
    inputs: list[Path] = []  # extra inputs after the source: images, music, effects track

    def add(p: Path, loop: bool = False, opts: tuple[str, ...] = ()) -> int:
        nonlocal cmd
        cmd += [*(["-stream_loop", "-1"] if loop else []), *opts, "-i", str(p.resolve())]
        inputs.append(p)
        return len(inputs)

    want_emoji = settings.caption_emoji if o.emoji is None else o.emoji
    placed = place_emoji(clip, plan, preset, cap_font, middle, size, captions=bool(subs),
                         hook=bool(hook_ass)) if want_emoji else []
    timed = timed or []
    placed = [e for e in placed if not any(e.start < t.end and t.start < e.end for t in timed)]  # card's moment
    pngs = [(e, emoji.png(e.emoji, e.px)) for e in placed]  # None: no font / not drawable → left out
    placed = [e for e, f in pngs if f]
    emoji_in = [add(f) for _, f in pngs if f]
    layers = []
    for t in timed:  # stat cards: frames drawn once per spec (cached), placed clear of face / hook / captions
        try:
            spec = insert_render.make_spec(t.insert, t.end - t.start, inserts.box_size(h), brand.accent(kit), font)
            fr = insert_render.frames(spec, job_dir / "inserts" / f"short_{clip.index}")
            x, y = inserts.placement(t.insert, size, split=middle, caption_position=o.caption_position,
                                     captions=bool(subs))
            layers.append(inserts.Layer(add(fr.pattern, opts=("-framerate", str(fr.fps))), x, y, t.start, t.end))
            if used is not None:
                used.add(fr.folder.name)
        except Exception as e:  # one card failing never fails the short
            log.warning("%s: stat card %s failed: %s", name, t.insert.id, e)
            if errors is not None:
                errors.append(f"stat card failed: {e}"[:200])
    logo = brand.watermark(kit, o)
    card_png = brand.end_card_png(kit, job_dir / f"{name}.end.png", height=h) if card else None
    logo_in = add(logo) if logo else None
    card_in = add(card_png) if card_png else None
    dub_in = add(voice_track) if voice_track else None  # Replace voice: clip-relative, starts at 0 like the cut
    ambience_in = add(ambience_track) if ambience_track else None
    music = sfx.music_path(settings.music_default if o.music is None else o.music)
    music_in = add(music, loop=True) if music else None
    fx_in = add(fx) if fx else None
    cutaways = []
    for cutaway in o.cutaways():
        cutoff = min(cutaway.end, plan.duration)
        if cutoff - cutaway.start < 1 / fps:
            raise RuntimeError("B-roll starts beyond the usable picture. Move it earlier or remove it.")
        footage = broll.path(job_dir, clip.index, cutaway.asset)
        if not footage.is_file():
            raise RuntimeError("The local B-roll file is missing. Replace or remove it before rendering.")
        cutaway_in = add(footage, loop=True, opts=("-protocol_whitelist", "file", "-f", "mov",
                                                   "-t", f"{cutoff - cutaway.start:.3f}"))
        cutaways.append((cutaway_in, cutaway.start, cutoff))

    graphs, maps = [], []
    if logo_in or card_in or placed or layers or cutaways:
        logo_from = min(settings.hook_seconds, plan.duration) if hook_ass else 0.0  # the hook card owns the top
        if cutaways:
            picture = "picture"
            graphs.append(f"[0:v]{','.join(vf[:picture_filters])}[{picture}]")
            for i, (cutaway_in, start, end) in enumerate(cutaways):
                overlay = f"cutaway{i}"
                mixed = f"withroll{i}"
                graphs.extend([
                    f"[{cutaway_in}:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
                    f"crop={w}:{h},setsar=1,setpts=PTS-STARTPTS+{start:.3f}/TB[{overlay}]",
                    f"[{picture}][{overlay}]overlay=eof_action=pass:enable='gte(t,{start:.3f})*"
                    f"lt(t,{end:.3f})'[{mixed}]",
                ])
                picture = mixed
            graphs.append(f"[{picture}]{','.join(vf[picture_filters:]) or 'null'}[b]")
        else:
            graphs.append(f"[0:v]{','.join(vf)}[b]")
        base = "b"
        if layers:  # stat cards: over captions + hook card, under emoji / logo / end card
            g, base = inserts.graph(base, layers)
            graphs.append(g)
        if placed:  # over captions + hook card, under the logo / end card
            g, base = emoji.graph(base, placed, emoji_in[0])
            graphs.append(g)
        graphs.append(brand.overlay_graph(base, logo_input=logo_in, kit=kit, card_input=card_in, start=logo_from,
                                          end=plan.duration, card_from=plan.duration, height=h))
        maps += ["-map", "[v]"]
    if music_in or fx_in or dub_in:
        graphs.append(sfx.audio_graph(af, music_in, fx_in, total, voice_src=f"{dub_in}:a" if dub_in else "0:a",
                                      ambience_input=ambience_in, stretch=stretch, ambience_timing=timing,
                                      ambience_level=o.dub_ambience_level))
        maps += ["-map", "[aout]"]
    if graphs:
        if "[v]" not in maps:
            graphs.insert(0, f"[0:v]{','.join(vf)}[v]")
            maps = ["-map", "[v]", *maps]
        if "[aout]" not in maps:
            maps += ["-map", "0:a?"]
        cmd += ["-filter_complex", ";".join(graphs), *maps]
        if af and "[aout]" not in maps:
            cmd += ["-af", ",".join(af)]
    else:
        cmd += ["-vf", ",".join(vf)]
        if af:
            cmd += ["-af", ",".join(af)]
    if preview_range is not None:
        start, length = preview_range
        cmd += ["-ss", f"{start:.3f}", "-t", f"{min(length, total - start):.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24"]
    else:
        cmd += _encoder_args()
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    with atomic_output(job_dir / f"{name}.mp4") as staged:
        res = subprocess.run([*cmd, str(staged.relative_to(job_dir))], cwd=job_dir, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {name}: {res.stderr.strip()[-500:]}")
    return rf


def place_emoji(clip: Clip, plan: CutPlan, preset, font: str, middle: bool, size: tuple[int, int],
                captions: bool, hook: bool) -> list[emoji.Placed]:
    """Where each colour emoji goes on this canvas: above its caption line (same placement rules as the ASS
    captions: bottom / middle / seam, margins, measured line wrap) and above the hook card (top layout only)."""
    w, h = size
    out: list[emoji.Placed] = []
    if hook and not middle:  # seam layout: the card's top edge depends on its wrapping — no sticker there
        e = emoji.hook_emoji(clip.hook)
        end = min(settings.hook_seconds, plan.duration)
        if e and end > 0.3:
            px = round(text_scale(settings.hook_size, h) * 1.3)
            top = round(settings.hook_margin_v * h / 1920) - 18  # the card's box starts 18 px (padding) higher
            out.append(emoji.Placed(0.0, end, e, (w - px) // 2, max(top - emoji.GAP - px, 8), px))
    if captions:
        chunks = chunk_words(plan.words, preset.words)
        align, margin = placement("middle" if middle else clip.options.caption_position, h)
        px = round(preset.size * emoji.SIZE)
        line_h = preset.size * 1.2 + 2 * preset.outline
        for start, end, e, i in emoji.caption_events(chunks, clip.keywords):
            text = " ".join(t for t in (caption_text(wd, preset) for wd in chunks[i]) if t)
            block = emoji.text_lines(text, font, preset.size, w - 120) * line_h
            top = (h - margin - block) if align == 2 else (h - block) / 2
            out.append(emoji.Placed(start, end, e, (w - px) // 2, max(round(top - emoji.GAP - px), 8), px))
    return out
