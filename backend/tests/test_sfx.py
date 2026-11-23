"""Sound design: whoosh on cuts, pop on keywords, music ducked under the voice."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from app.config import settings
from app.models import Clip, RenderOptions, Segment, Word
from app.pipeline import sfx
from app.pipeline.jumpcut import CutPlan

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _w(text, start):
    return Word(word=f" {text}", start=start, end=start + 0.3)


def test_whooshes_only_on_real_cuts_and_not_too_often():
    # cuts at output 3.0 (gap 1.0), 5.0 (0.1 s breath), 5.9 (gap 0.8), 10.1 (gap 0.5)
    plan = CutPlan(keeps=[(0, 3), (4, 6), (6.1, 7), (7.8, 12), (12.5, 14)], words=[], source_duration=14)
    assert [t for t, k in sfx.events(plan, [])] == [3.0, 5.9, 10.1]  # the breath trim stays silent
    close = CutPlan(keeps=[(0, 3), (4, 4.5), (5.5, 10)], words=[], source_duration=10)
    assert [t for t, k in sfx.events(close, [])] == [3.0]  # 3.5 is < WHOOSH_SPACING after it


def test_pops_on_keywords_spaced_and_capped():
    words = [_w("the", 0.1), _w("Salary", 0.5), _w("salary,", 1.0), _w("is", 2), _w("NEGOTIABLE", 3.0)]
    words += [_w("salary", 5.0 + 2.1 * i) for i in range(10)]
    plan = CutPlan(keeps=[(0, 30)], words=words, source_duration=30)
    pops = [t for t, k in sfx.events(plan, ["salary negotiable", "of"]) if k == "pop"]
    assert pops[:3] == [0.5, 3.0, 5.0]  # 1.0 is too soon after 0.5; "the"/"is"/"of" never pop
    assert len(pops) == sfx.POP_MAX


def test_starter_sounds_are_made_once_and_yours_win(tmp_path):
    sfx.ensure_starters()
    whoosh = sfx.sounds_dir() / "whoosh.wav"
    assert whoosh.exists() and (sfx.sounds_dir() / "pop.wav").exists()
    if shutil.which("ffmpeg"):
        s = sfx.load_sound("whoosh")
        assert 0.4 < len(s) / sfx.RATE < 0.5 and 0.3 < np.abs(s).max() <= 0.55
        mine = sfx.sounds_dir() / "pop.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:d=0.2", str(mine)], check=True)
        (sfx.sounds_dir() / "pop.wav").unlink()
        assert abs(len(sfx.load_sound("pop")) / sfx.RATE - 0.2) < 0.06  # your mp3 is used


def test_music_folder_lists_tracks_and_refuses_paths():
    sfx.music_dir().mkdir(parents=True)
    (sfx.music_dir() / "calm.mp3").write_bytes(b"x")
    (sfx.music_dir() / "notes.txt").write_text("x")
    assert sfx.music_tracks() == ["calm.mp3"]
    assert sfx.music_path("calm.mp3") and not sfx.music_path("../calm.mp3") and not sfx.music_path("")
    assert sfx.music_path("missing.mp3") is None


def test_audio_graph_shapes():
    g = sfx.audio_graph(["atrim=start=0:end=5"], music_input=2, sfx_input=3, duration=10)
    assert g.startswith("[0:a]atrim=start=0:end=5,aformat=") and "channel_layouts=stereo[voice]" in g
    assert "[mus][key]sidechaincompress" in g and "afade=t=out:st=8.500" in g
    assert g.endswith("[vo][duck][fx]amix=inputs=3:duration=first:normalize=0[aout]")
    assert sfx.audio_graph([], None, 1, 5).endswith("[vo][fx]amix=inputs=2:duration=first:normalize=0[aout]")


@needs_ffmpeg
def test_render_mixes_effects_and_ducks_music_under_the_voice(tmp_path, monkeypatch):
    from app.pipeline.render import render_clip

    # 8 s: "voice" (300 Hz) for 0-3 s, silence 3-5 s (cut away by the jump cut), voice again 5-8 s
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25:d=8",
                    "-f", "lavfi", "-i", "sine=f=300:d=8", "-af", "volume='if(between(t,3,5),0,2)':eval=frame",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    sfx.music_dir().mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=2000:d=3",
                    str(sfx.music_dir() / "tone.wav")], check=True)  # shorter than the short: must loop
    words = [_w("money", 0.5), _w("talk", 1.0), _w("money", 5.5), _w("again", 6.5)]
    segs = [Segment(start=0, end=3, text="money talk", words=words[:2]),
            Segment(start=5, end=8, text="money again", words=words[2:])]
    clip = Clip(index=1, start=0, end=8, title="t", hook="", score=7, keywords=["money"],
                options=RenderOptions(caption_style="none", hook=False, sfx=True, music="tone.wav"))
    monkeypatch.setattr(settings, "jumpcut_audio", False)  # cut by word gaps (a test tone has no room noise)
    render_clip(src, clip, segs, tmp_path)
    assert clip.cut_seconds > 1.0 and (tmp_path / "short_1.sfx.wav").exists()

    out = tmp_path / "short_1.mp4"
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                                       "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"]
    durs = {s["codec_type"]: float(s["duration"]) for s in probe}
    assert abs(durs["video"] - durs["audio"]) < 0.1 and abs(durs["video"] - clip.out_duration) < 0.1

    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "48000", "-f",
                                        "f32le", "-"], capture_output=True).stdout, "<f4")

    def band(t0, t1, f):  # level of one frequency in [t0, t1)
        x = pcm[int(t0 * 48000): int(t1 * 48000)]
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        k = int(f * len(x) / 48000)
        return spec[k - 3:k + 4].max() / len(x)

    voice, music = band(1.2, 2.2, 300), band(1.2, 2.2, 2000)
    assert voice > 0.005 and 0 < music < voice / 5  # music there, well under the voice
    # with no voice (music alone) the same music is louder: the ducking let go
    alone = tmp_path / "alone.mp4"
    clip.options = RenderOptions(caption_style="none", hook=False, sfx=False, music="tone.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", "volume=0", "-c:v", "copy",
                    str(tmp_path / "silent.mp4")], check=True)
    render_clip(tmp_path / "silent.mp4", clip, [], tmp_path)
    shutil.copy(out, alone)
    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(alone), "-ac", "1", "-ar", "48000", "-f",
                                        "f32le", "-"], capture_output=True).stdout, "<f4")
    assert band(1.2, 2.2, 2000) > music * 2  # ≥ 6 dB louder with nobody talking
    assert not (tmp_path / "short_1.sfx.wav").exists()  # effects off → the old track is removed
