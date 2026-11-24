"""Re-voice a recording (revoice.py): narration → sentences → a Voice Library voice at your timing → same picture,
new voice. Whisper and the voice engine are faked; ffmpeg is real."""
import shutil
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.pipeline import dub, revoice, tts

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def media(path, seconds=6.0, video=True):
    args = ["ffmpeg", "-y", "-v", "error"]
    if video:
        args += ["-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=25:d={seconds}"]
    args += ["-f", "lavfi", "-i", f"sine=f=300:d={seconds}"]
    args += (["-c:v", "libx264", "-pix_fmt", "yuv420p"] if video else []) + ["-c:a", "aac", "-shortest", str(path)]
    subprocess.run(args, check=True)
    return path.read_bytes()


def tone(path, seconds, freq=1000.0):
    t = np.arange(int(dub.SR * seconds)) / dub.SR
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(dub.SR), "-ac", "1", "-i", "-", str(path)],
                   input=(0.3 * np.sin(2 * np.pi * freq * t)).astype("<f4").tobytes(), check=True)


WORDS = [{"start": 0.2, "end": 0.6, "word": " Welcome"}, {"start": 0.7, "end": 1.0, "word": " back."},
         {"start": 2.0, "end": 2.4, "word": " Three"}, {"start": 2.5, "end": 2.9, "word": " ways"},
         {"start": 3.0, "end": 3.4, "word": " today."}]


@pytest.fixture
def fakes(monkeypatch):
    spoken = []

    def speak(vid, texts, language=None, out_dir=None, names=None, version=None):
        spoken.append(list(texts))
        for n in names:
            tone(out_dir / n, 0.8)
        return [{"file": n, "seconds": 0.8, "took": 1.0} for n in names]

    def heard(paths):
        return [{"text": "x", "words": WORDS if "source" in str(p) else [{"start": 0.05, "end": 0.7, "word": " w"}],
                 "logprob": -0.2, "language": "en"} for p in paths]

    monkeypatch.setattr(tts, "speak", speak)
    monkeypatch.setattr(tts, "transcribe_many", heard)
    monkeypatch.setattr(tts, "prompt_for", lambda v, version=None: v)
    return spoken


def test_video_keeps_its_picture_and_gets_the_new_voice(tmp_path, fakes):
    vid = tts.create("Target", consent=True)["id"]
    m = revoice.create("my take.MOV", media(tmp_path / "in.mp4"), "")
    assert m["has_video"] and m["duration"] == pytest.approx(6.0, abs=0.1) and m["name"] == "my take"
    assert (revoice.folder(m["id"]) / "source.mov").exists()
    s = revoice.script(m["id"])
    assert [ln.original for ln in s.lines] == ["Welcome back.", "Three ways today."] and s.lines[1].start == 2.0
    revoice.edit(m["id"], vid, None, {1: "Three quick ways today."})
    m = revoice.generate(m["id"])
    assert fakes == [["Welcome back.", "Three quick ways today."]] and m["output"] == "revoiced.mp4"
    out = revoice.folder(m["id"]) / "revoiced.mp4"
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0",
                              str(out)], capture_output=True, text=True).stdout
    assert "video" in streams and "audio" in streams
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2.1", "-t", "0.5", "-i", str(out), "-ac", "1", "-ar", "8000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, "<f4")
    peak = np.fft.rfftfreq(len(x), 1 / 8000)[np.abs(np.fft.rfft(x)).argmax()]
    assert abs(peak - 1000) < 30  # the new voice at sentence 2's time, not the original 300 Hz
    assert [ln.at for ln in revoice.script(m["id"]).lines] == [0.2, 2.0]  # your timing


def test_audio_in_audio_out_and_the_limits(tmp_path, fakes, monkeypatch):
    vid = tts.create("Target", consent=True)["id"]
    m = revoice.create("note.m4a", media(tmp_path / "in.m4a", video=False))
    assert not m["has_video"]
    revoice.edit(m["id"], vid, None, {})
    assert revoice.generate(m["id"])["output"] == "revoiced.wav"
    monkeypatch.setattr(revoice, "MAX_SECONDS", 3.0)
    with pytest.raises(ValueError, match="under"):
        revoice.create("long.m4a", media(tmp_path / "long.m4a", video=False))
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(silent)], check=True)
    with pytest.raises(ValueError, match="no audio"):
        revoice.create("silent.mp4", silent.read_bytes())
    assert len(revoice.all_items()) == 1  # failed ones leave nothing behind
    monkeypatch.setattr(tts, "transcribe_many", lambda paths: [{"words": [], "text": "", "language": "en"}])
    monkeypatch.setattr(revoice, "MAX_SECONDS", 60.0)
    with pytest.raises(ValueError, match="no speech"):
        revoice.create("hum.m4a", media(tmp_path / "hum.m4a", video=False))


def test_revoice_api(tmp_path, monkeypatch, fakes):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from app.main import create_app

    from .conftest import SchemaFakeLLM

    with TestClient(create_app(start_worker=False, llm_factory=SchemaFakeLLM)) as c:
        vid = c.post("/api/voices", json={"name": "Target", "consent": True}).json()["id"]
        r = c.post("/api/revoice", files={"file": ("take.mp4", media(tmp_path / "in.mp4"), "video/mp4")},
                   data={"name": "Intro"})
        assert r.status_code == 201
        rid = r.json()["id"]
        assert r.json()["name"] == "Intro" and len(r.json()["script"]["lines"]) == 2
        assert c.post(f"/api/revoice/{rid}/generate").status_code == 400  # no voice yet
        assert c.put(f"/api/revoice/{rid}", json={"voice": "nobody"}).status_code == 404
        r = c.put(f"/api/revoice/{rid}", json={"voice": vid, "texts": {"0": "Hello again."}})
        assert r.json()["stale"] == [0, 1]
        r = c.post(f"/api/revoice/{rid}/generate")
        assert r.status_code == 200 and r.json()["output"] == "revoiced.mp4" and r.json()["stale"] == []
        assert c.get(f"/api/revoice/{rid}/files/revoiced.mp4").status_code == 200
        assert c.get(f"/api/revoice/{rid}/files/meta.json").status_code == 404
        assert c.get(f"/api/revoice/{rid}/files/..%2F..%2Fx").status_code == 404
        assert c.get("/api/revoice/zzzz/files/revoiced.mp4").status_code == 404
        assert [x["id"] for x in c.get("/api/revoice").json()["items"]] == [rid]
        assert c.delete(f"/api/revoice/{rid}").json() == {"ok": True}
        assert c.get(f"/api/revoice/{rid}").status_code == 404
