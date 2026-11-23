"""Your voice (tts.py): recordings → cleaned, split, scored → build / speak through the worker (faked here: the real
one runs in the clipforge-tts env and needs the GPU)."""
import json
import shutil
import subprocess

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.pipeline import tts

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _tone(path, seconds: float, noise: float = 0.0):
    """A 'voice': 220 Hz bursts with gaps (speech-like loud/quiet frames) + optional noise floor."""
    t = np.arange(int(tts.SR * seconds)) / tts.SR
    gate = (np.sin(2 * np.pi * 1.5 * t) > -0.3).astype(np.float32)
    pcm = 0.4 * np.sin(2 * np.pi * 220 * t) * gate + noise * np.random.default_rng(0).standard_normal(len(t))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(tts.SR), "-ac", "1", "-i", "-",
                    str(path)], input=pcm.astype("<f4").tobytes(), check=True)
    return path.read_bytes()


def _words(total: float, every: float = 0.5, sentence_every: int = 8) -> list[dict]:
    out, t, i = [], 0.2, 0
    while t + 0.4 < total:
        i += 1
        out.append({"start": t, "end": t + 0.4, "word": f" w{i}{'.' if i % sentence_every == 0 else ''}"})
        t += every
    return out


@pytest.fixture
def fake_whisper(monkeypatch):
    def heard(paths):
        out = []
        for p in paths:
            secs = len(tts.read_pcm(p)) / tts.SR
            words = _words(secs)
            out.append({"text": "".join(w["word"] for w in words).strip(), "words": words, "logprob": -0.3,
                        "language": "en"})
        return out

    monkeypatch.setattr(tts, "transcribe_many", heard)


# ------------------------------------------------------------------ pure pieces

def test_split_points_cut_at_sentence_ends_and_keep_short_takes_whole():
    assert tts.split_points(_words(12), 12.0) == [(0.0, 12.0)]
    pieces = tts.split_points(_words(60), 60.0)
    assert len(pieces) >= 4 and pieces[0][0] == 0.0 and pieces[-1][1] == 60.0
    assert all(tts.MIN_SECONDS <= b - a <= tts.MAX_SECONDS for a, b in pieces)
    ends = {round(w["end"], 2) for w in _words(60) if w["word"].endswith(".")}
    assert all(round(b, 2) in ends for _, b in pieces[:-1])  # cut after a sentence


def test_quality_and_score_prefer_clean_confident_mid_length():
    rng = np.random.default_rng(1)
    t = np.arange(tts.SR * 8) / tts.SR
    speech = 0.4 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 1.5 * t) > -0.3)
    clean, noisy = tts.quality(speech), tts.quality(speech + 0.1 * rng.standard_normal(len(t)))
    assert clean["seconds"] == 8.0 and clean["snr_db"] > noisy["snr_db"] + 10
    good = tts.score({**clean, "logprob": -0.2})
    assert good > tts.score({**noisy, "logprob": -0.2}) and good > tts.score({**clean, "logprob": -1.2})
    assert tts.score({**clean, "seconds": 2.0, "logprob": -0.2}) < good  # too short for a reference
    assert tts.score({**clean, "clipping": 0.01, "logprob": -0.2}) < good - 20


def test_pick_best_prefers_the_asked_style():
    recs = [{"score": 90, "style": "calm"}, {"score": 70, "style": "energetic"}, {"score": 80, "style": ""}]
    assert tts.pick_best(recs) == 0 and tts.pick_best(recs, "energetic") == 1


# ------------------------------------------------------------------ profiles + recordings

@needs_ffmpeg
def test_recordings_are_cleaned_split_transcribed_and_scored(tmp_path, fake_whisper):
    v = tts.create("My Voice")
    assert v["id"] == "my-voice" and tts.create("My Voice")["id"].startswith("my-voice-")  # unique
    short = _tone(tmp_path / "a.wav", 8.0)
    long = _tone(tmp_path / "b.wav", 50.0)
    v = tts.add_recordings("my-voice", [("a.wav", short), ("b.wav", long)], style="calm")
    recs = v["recordings"]
    assert len(recs) >= 4 and [r["file"] for r in recs][:2] == ["rec_1.wav", "rec_2.wav"]
    assert all(tts.MIN_SECONDS <= r["seconds"] <= tts.MAX_SECONDS + 0.5 for r in recs)
    assert all(r["text"] and r["style"] == "calm" and r["use"] and 0 < r["score"] <= 100 for r in recs)
    wav = tts.voice_dir("my-voice") / "recordings" / "rec_1.wav"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0",
                            str(wav)], capture_output=True, text=True).stdout.strip()
    assert probe == "24000,1"

    v = tts.edit_recording("my-voice", "rec_1.wav", {"text": "  fixed   words ", "use": False, "style": "nope"})
    r1 = v["recordings"][0]
    assert (r1["text"], r1["use"], r1["style"]) == ("fixed words", False, "calm")
    v = tts.remove_recording("my-voice", "rec_1.wav")
    assert "rec_1.wav" not in [r["file"] for r in v["recordings"]] and not wav.exists()
    with pytest.raises(ValueError):
        tts.add_recordings("my-voice", [("x.mp3", b"not audio")])
    with pytest.raises(ValueError):
        tts.voice_dir("../etc")


# ------------------------------------------------------------------ build / speak (fake worker)

class FakeWorker:
    def __init__(self, sims=None, fail=None):
        self.jobs, self.sims, self.fail = [], sims, fail

    def __call__(self, job, timeout=None):
        self.jobs.append(job)
        if self.fail:
            return {"ok": False, "error": self.fail}
        if job["cmd"] == "build":
            n = len(job["recordings"])
            sims = self.sims or [0.95] * n
            open(job["out"], "wb").write(b"prompt")
            return {"ok": True, "similarity": sims, "used": [i for i, s in enumerate(sims)
                                                              if s >= job["min_similarity"]], "peak_vram_mb": 5000}
        if job["cmd"] == "speak":
            items = []
            for it in job["items"]:
                open(it["out"], "wb").write(b"RIFF")
                items.append({"out": it["out"], "seconds": 3.2, "took": 4.0})
            return {"ok": True, "items": items, "sample_rate": 24000}
        return {"ok": True, "cuda_ok": True, "torch": "2.9", "cuda": "12.8", "gpu": "RTX"}


def _voice_with(recs: list[dict]) -> str:
    v = tts.create("Me")
    v["recordings"] = [{"file": f"rec_{i}.wav", "text": f"line {i}", "use": True, "style": "", "seconds": 8,
                        "score": 60, **r} for i, r in enumerate(recs, 1)]
    tts.save(v)
    return v["id"]


def test_build_uses_the_best_take_and_flags_outliers(monkeypatch):
    worker = FakeWorker(sims=[0.93, 0.95, 0.52, 0.91])
    monkeypatch.setattr(tts, "run_worker", worker)
    vid = _voice_with([{}, {"score": 88}, {}, {"use": False}, {"score": 70}])
    v = tts.build(vid)
    job = worker.jobs[0]
    assert job["cmd"] == "build" and len(job["recordings"]) == 4 and job["best"] == 1  # unused take not sent
    assert job["min_similarity"] == tts.MIN_SIMILARITY and job["model"] == settings.tts_model
    assert v["built"]["best"] == "rec_2.wav" and v["built"]["used"] == 3
    flagged = {r["file"]: r.get("outlier") for r in v["recordings"]}
    assert flagged["rec_3.wav"] is True and flagged["rec_1.wav"] is False
    v = tts.edit_recording(vid, "rec_1.wav", {"text": "changed"})
    assert v["built"] is None  # recordings changed → build again


def test_speak_builds_first_and_writes_samples(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(tts, "run_worker", worker)
    vid = _voice_with([{}])
    out = tts.speak(vid, ["Hello there."], "English")
    assert [j["cmd"] for j in worker.jobs] == ["build", "speak"]
    item = worker.jobs[1]["items"][0]
    assert item["text"] == "Hello there." and item["language"] == "English"
    assert out[0]["seconds"] == 3.2 and (tts.voice_dir(vid) / "samples" / out[0]["file"]).exists()
    tts.speak(vid, ["Again."])
    assert [j["cmd"] for j in worker.jobs] == ["build", "speak", "speak"]  # built once
    monkeypatch.setattr(tts, "run_worker", FakeWorker(fail="CUDA out of memory"))
    with pytest.raises(RuntimeError, match="out of memory"):
        tts.speak(vid, ["x"])


def test_worker_output_is_parsed_and_failures_explained(monkeypatch, tmp_path):
    fake = tmp_path / "py"
    fake.write_text("#!/bin/sh\necho 'model loaded' >&2\necho '{\"ok\": true, \"cuda_ok\": true}'\n")
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "tts_python", str(fake))
    assert tts.run_worker({"cmd": "check"}) == {"ok": True, "cuda_ok": True}
    fake.write_text("#!/bin/sh\necho 'Segmentation fault' >&2\nexit 139\n")
    res = tts.run_worker({"cmd": "check"})
    assert not res["ok"] and "139" in res["error"] and "Segmentation" in res["error"]
    monkeypatch.setattr(settings, "tts_python", str(tmp_path / "missing"))
    assert tts.status()["installed"] is False


# ------------------------------------------------------------------ API

@needs_ffmpeg
def test_voice_api(tmp_path, monkeypatch, fake_whisper):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(tts, "run_worker", FakeWorker())
    from app.jobs import GPU_LOCK
    from app.main import create_app

    from .conftest import SchemaFakeLLM

    with TestClient(create_app(start_worker=False, llm_factory=SchemaFakeLLM)) as c:
        vid = c.post("/api/voices", json={"name": "Rahul"}).json()["id"]
        audio = _tone(tmp_path / "take.wav", 7.0)
        with GPU_LOCK:
            busy = c.post(f"/api/voices/{vid}/recordings", files=[("files", ("take.wav", audio, "audio/wav"))])
            assert busy.status_code == 409
        r = c.post(f"/api/voices/{vid}/recordings", files=[("files", ("take.wav", audio, "audio/wav"))],
                   data={"style": "energetic"})
        assert r.status_code == 200 and r.json()["recordings"][0]["style"] == "energetic"
        f = r.json()["recordings"][0]["file"]
        assert c.get(f"/api/voices/{vid}/audio/recordings/{f}").status_code == 200
        assert c.get(f"/api/voices/{vid}/audio/recordings/..%2Fvoice.json").status_code == 404
        assert c.patch(f"/api/voices/{vid}/recordings/{f}", json={"text": "my words"}).json()["recordings"][0][
            "text"] == "my words"
        said = c.post(f"/api/voices/{vid}/speak", json={"text": "Hello from my voice."}).json()
        assert said["seconds"] == 3.2 and said["voice"]["built"]
        assert c.get(f"/api/voices/{vid}/audio/samples/{said['file']}").status_code == 200
        listed = c.get("/api/voices").json()
        assert [x["id"] for x in listed["voices"]] == [vid] and "hinglish" in listed["styles"]
        assert c.get("/api/voices/nope/audio/samples/x.wav").status_code == 404
        assert c.delete(f"/api/voices/{vid}").json() == {"ok": True}
        assert json.loads(json.dumps(c.get("/api/voices").json()))["voices"] == []
