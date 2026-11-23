"""Step 4 — jump cuts + face-tracking crop + captions + FFmpeg 9:16 render."""
import subprocess
from dataclasses import replace
from pathlib import Path

from ..config import settings
from ..models import Clip, Segment
from . import brand, sfx
from .captions import build_ass, build_hook_ass, build_srt
from .fonts import font_for, fontsdir_option
from .jumpcut import audio_cut_graph, audio_silences, plan_cuts, video_select_expr, zoom_graph
from .presets import get_preset
from .reframe import plan_reframe


def _encoder_args() -> list[str]:
    if "nvenc" in settings.encoder:
        return ["-c:v", settings.encoder, "-preset", "p5", "-cq", "23"]
    return ["-c:v", settings.encoder, "-preset", "veryfast", "-crf", "21"]


def render_clip(video: Path, clip: Clip, segs: list[Segment], job_dir: Path) -> str:
    """Renders short_<n>.mp4 (+ .srt export, .jpg thumbnail) in job_dir.

    Also fills clip.cut_seconds / clip.out_duration / clip.reframe / clip.fit_seconds. Returns the mp4 filename.
    """
    name = f"short_{clip.index}"
    o = clip.options
    jumpcut = settings.jumpcut_enabled if o.jumpcut is None else o.jumpcut
    reframe_mode = o.reframe or settings.reframe_mode
    caption_style = o.caption_style or settings.caption_style
    zoom = settings.jumpcut_zoom if o.zoom is None else o.zoom
    hook = settings.hook_overlay if o.hook is None else o.hook

    silences = audio_silences(video, clip.start, clip.end) if jumpcut and settings.jumpcut_audio else None
    plan = plan_cuts(segs, clip.start, clip.end, enabled=jumpcut, silences=silences, removed=clip.removed)
    clip.cut_seconds = round(plan.removed, 2)
    clip.out_duration = round(plan.duration, 2)

    (job_dir / f"{name}.srt").write_text(build_srt(plan.words), encoding="utf-8")  # for YouTube upload

    # Speaker-following crop (runs before jump-cut select, so it works in source-relative time)
    rf = plan_reframe(video, clip.start, clip.end, mode=reframe_mode, follow=o.follow)
    clip.reframe = rf.mode
    clip.fit_seconds = round(clip.duration if rf.mode == "fit" else sum(b - a for a, b in rf.fit), 1)
    cmd_file = None
    if rf.mode in ("face", "speaker"):
        cmd_file = f"{name}.crop.cmd"
        (job_dir / cmd_file).write_text(rf.sendcmd(), encoding="utf-8")

    middle = rf.mode == "split"  # stacked speakers: captions on the seam, not over the bottom face
    kit = brand.load()
    preset = get_preset(o.caption_preset)
    if kit.enabled and kit.accent and kit.accent_captions:  # karaoke active word in the brand colour
        preset = replace(preset, active=brand.ass_colour(kit.accent))
    font = preset.font
    if caption_style == "none":  # no-speech clips: nothing to caption (and Whisper invents words on music)
        subs = ""
    else:  # karaoke = word-by-word highlight; simple = whole line in the same look, no animation
        cap_font = font_for("".join(w.word for w in plan.words), font)  # Hindi → a font with Devanagari
        ass = build_ass(plan.words, clip.keywords, middle=middle, preset=preset,
                        position=o.caption_position, animate=caption_style != "simple", font=cap_font)
        (job_dir / f"{name}.ass").write_text(ass, encoding="utf-8")
        subs = f"ass={name}.ass{fontsdir_option(cap_font)}"

    fps = settings.output_fps
    vf = [f"fps={fps}", *rf.filters(cmd_file)]
    af = []
    if len(plan.keeps) > 1 or plan.removed > 0.05:
        # Same frame-grid intervals for both streams (see jumpcut.snap_to_frames) → no A/V drift
        vf += [f"select='{video_select_expr(plan.keeps, fps)}'", f"setpts=N/{fps}/TB"]
        af = [audio_cut_graph(plan.keeps, settings.jumpcut_crossfade_ms / 1000)]
    vf += ["scale=1080:1920:force_original_aspect_ratio=decrease",
           "pad=1080:1920:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    if zoom and af:  # punch-in on alternate segments; output time, before captions so text never zooms
        vf.append(zoom_graph(plan.keeps, fps, settings.jumpcut_zoom_scale))
    # Brand kit end card: the last frame held, blurred + darkened, after the last word (before captions, so
    # no caption freezes on it); the card itself is overlaid below. Never past max_clip_seconds.
    card = brand.end_card_seconds(kit, o, plan.duration)
    if card:
        vf += brand.freeze_tail(plan.duration, card)
        af = [*af, f"apad=pad_dur={card:.3f}"]
    if subs:
        vf.append(subs)
    hook_font = font_for(clip.hook, font)
    hook_ass = build_hook_ass(clip.hook, plan.duration, seam=middle, font=hook_font) if hook else ""
    if hook_ass:  # last, so the title card sits above captions (and never zooms)
        (job_dir / f"{name}.hook.ass").write_text(hook_ass, encoding="utf-8")
        vf.append(f"ass={name}.hook.ass{fontsdir_option(hook_font)}")

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video.resolve())]
    total = plan.duration + card
    inputs: list[Path] = []  # extra inputs after the source: images, music, effects track

    def add(p: Path, loop: bool = False) -> int:
        nonlocal cmd
        cmd += [*(["-stream_loop", "-1"] if loop else []), "-i", str(p.resolve())]
        inputs.append(p)
        return len(inputs)

    logo = brand.watermark(kit, o)
    card_png = brand.end_card_png(kit, job_dir / f"{name}.end.png") if card else None
    logo_in = add(logo) if logo else None
    card_in = add(card_png) if card_png else None
    # Sound design: music ducked under the voice, effects on one pre-mixed track (sfx.py)
    music = sfx.music_path(settings.music_default if o.music is None else o.music)
    want_fx = settings.sfx_default if o.sfx is None else o.sfx
    fx = sfx.write_track(sfx.events(plan, clip.keywords), total, job_dir / f"{name}.sfx.wav") if want_fx else None
    music_in = add(music, loop=True) if music else None
    fx_in = add(fx) if fx else None
    if not fx:
        (job_dir / f"{name}.sfx.wav").unlink(missing_ok=True)

    graphs, maps = [], []
    if logo_in or card_in:  # images need their own inputs → filter_complex instead of -vf
        logo_from = min(settings.hook_seconds, plan.duration) if hook_ass else 0.0  # the hook card owns the top
        graphs += [f"[0:v]{','.join(vf)}[b]",
                   brand.overlay_graph("b", logo_input=logo_in, kit=kit, card_input=card_in, start=logo_from,
                                       end=plan.duration, card_from=plan.duration)]
        maps += ["-map", "[v]"]
    if music_in or fx_in:
        graphs.append(sfx.audio_graph(af, music_in, fx_in, total))
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
    cmd += [*_encoder_args(), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", f"{name}.mp4"]

    res = subprocess.run(cmd, cwd=job_dir, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {name}: {res.stderr.strip()[-500:]}")

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", f"{name}.mp4",
                    "-frames:v", "1", "-vf", "scale=360:-1", f"{name}.jpg"],
                   cwd=job_dir, capture_output=True)
    clip.out_duration = round(plan.duration + card, 2)
    clip.brand_rev = kit.rev if brand.in_use(kit) else -1
    clip.version += 1
    clip.stale = False
    clip.render_error = None
    return f"{name}.mp4"
