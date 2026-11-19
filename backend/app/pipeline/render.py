"""Step 4 — jump cuts + face-tracking crop + captions + FFmpeg 9:16 render."""
import subprocess
from pathlib import Path

from ..config import settings
from ..models import Clip, Segment
from .captions import build_ass, build_srt
from .fonts import fontsdir_option
from .jumpcut import audio_cut_graph, plan_cuts, video_select_expr, zoom_graph
from .reframe import plan_reframe


def _encoder_args() -> list[str]:
    if "nvenc" in settings.encoder:
        return ["-c:v", settings.encoder, "-preset", "p5", "-cq", "23"]
    return ["-c:v", settings.encoder, "-preset", "veryfast", "-crf", "21"]


def _simple_style() -> str:
    return (f"FontName={settings.caption_font},FontSize=16,Bold=1,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=70")


def render_clip(video: Path, clip: Clip, segs: list[Segment], job_dir: Path) -> str:
    """Renders short_<n>.mp4 (+ .srt export, .jpg thumbnail) in job_dir.

    Also fills clip.cut_seconds / clip.out_duration / clip.reframe. Returns the mp4 filename.
    """
    name = f"short_{clip.index}"
    o = clip.options
    jumpcut = settings.jumpcut_enabled if o.jumpcut is None else o.jumpcut
    reframe_mode = o.reframe or settings.reframe_mode
    caption_style = o.caption_style or settings.caption_style
    zoom = settings.jumpcut_zoom if o.zoom is None else o.zoom

    plan = plan_cuts(segs, clip.start, clip.end, enabled=jumpcut)
    clip.cut_seconds = round(plan.removed, 2)
    clip.out_duration = round(plan.duration, 2)

    (job_dir / f"{name}.srt").write_text(build_srt(plan.words), encoding="utf-8")  # for YouTube upload
    if caption_style == "karaoke":
        (job_dir / f"{name}.ass").write_text(build_ass(plan.words, clip.keywords), encoding="utf-8")
        subs = f"ass={name}.ass{fontsdir_option(settings.caption_font)}"
    else:
        subs = f"subtitles={name}.srt:force_style='{_simple_style()}'{fontsdir_option(settings.caption_font)}"

    # Speaker-following crop (runs before jump-cut select, so it works in source-relative time)
    rf = plan_reframe(video, clip.start, clip.end, mode=reframe_mode)
    clip.reframe = rf.mode
    cmd_file = None
    if rf.mode == "face":
        cmd_file = f"{name}.crop.cmd"
        (job_dir / cmd_file).write_text(rf.sendcmd(), encoding="utf-8")

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
    vf.append(subs)

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video.resolve()),
           "-vf", ",".join(vf)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += [*_encoder_args(), "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", f"{name}.mp4"]

    res = subprocess.run(cmd, cwd=job_dir, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {name}: {res.stderr.strip()[-500:]}")

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", f"{name}.mp4",
                    "-frames:v", "1", "-vf", "scale=360:-1", f"{name}.jpg"],
                   cwd=job_dir, capture_output=True)
    clip.version += 1
    clip.stale = False
    clip.render_error = None
    return f"{name}.mp4"
