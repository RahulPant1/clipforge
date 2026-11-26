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
        r = c.post(f"/api/revoice/{rid}/lines/0/pickup", json={"expected_text": "Earlier wording."})
        assert r.status_code == 400
        r = c.post(f"/api/revoice/{rid}/lines/0/pickup", json={"expected_text": "Hello again."})
        assert r.status_code == 200 and r.json()["pickup_output"] == "pickup.mp4" and r.json()["pickup_line"] == 0
        assert c.get(f"/api/revoice/{rid}/files/pickup.mp4").status_code == 200
        r = c.post(f"/api/revoice/{rid}/pickups",
                   json={"expected_texts": {"0": "Hello again.", "1": "Three ways today."}})
        assert r.status_code == 200 and r.json()["pickup_lines"] == [0, 1]
        assert c.get(f"/api/revoice/{rid}/files/revoiced.mp4").status_code == 200
        assert c.get(f"/api/revoice/{rid}/files/meta.json").status_code == 404
        assert c.get(f"/api/revoice/{rid}/files/..%2F..%2Fx").status_code == 404
        assert c.get("/api/revoice/zzzz/files/revoiced.mp4").status_code == 404
        assert [x["id"] for x in c.get("/api/revoice").json()["items"]] == [rid]
        assert c.delete(f"/api/revoice/{rid}").json() == {"ok": True}
        assert c.get(f"/api/revoice/{rid}").status_code == 404


@pytest.mark.parametrize("video", [True, False])
def test_pickup_only_replaces_the_selected_sentence(tmp_path, fakes, video):
    extension = "mp4" if video else "m4a"
    item = revoice.create(f"recording.{extension}", media(tmp_path / f"input.{extension}", video=video))
    vid = tts.create("Target", consent=True)["id"]
    revoice.edit(item["id"], vid, None, {1: "Three quick ways today."})
    result = revoice.pickup(item["id"], 1)
    assert result["pickup_line"] == 1 and result["output"] is None
    assert fakes == [["Three quick ways today."]]
    out = revoice.folder(item["id"]) / result["pickup_output"]

    def pitch(at):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(at), "-t", "0.25", "-i", str(out), "-ac", "1", "-ar", "8000",
             "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout
        samples = np.frombuffer(raw, "<f4")
        return np.fft.rfftfreq(len(samples), 1 / 8000)[np.abs(np.fft.rfft(samples)).argmax()]

    assert pitch(0.4) == pytest.approx(300, abs=8)
    assert pitch(2.2) == pytest.approx(1000, abs=8)
    assert pitch(4.0) == pytest.approx(300, abs=8)
    duration, has_video, has_audio = revoice.probe(out)
    assert duration == pytest.approx(6.0, abs=0.06) and has_video == video and has_audio


def test_browser_capture_without_duration_is_imported(tmp_path, fakes):
    from app.pipeline import capture

    source = tmp_path / "browser.webm"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=24:d=5",
        "-f", "lavfi", "-i", "sine=f=300:d=5", "-c:v", "libvpx", "-c:a", "libopus",
        "-f", "webm", "-live", "1", str(source),
    ], check=True)
    assert revoice.probe(source)[0] == 0
    normalized = tmp_path / "normalized.mp4"
    capture.normalize(source, normalized)
    assert revoice.probe(normalized)[0] == pytest.approx(5, abs=0.1)
    item = revoice.create("screen-recording.webm", source.read_bytes(), recorded=True)
    assert item["has_video"] and item["source"] == "source.mp4"


def test_multiple_pickups_keep_the_audio_between_them(tmp_path, fakes):
    item = revoice.create("take.mp4", media(tmp_path / "source.mp4"))
    vid = tts.create("Target", consent=True)["id"]
    revoice.edit(item["id"], vid, None, {0: "Welcome again.", 1: "Three examples."})
    result = revoice.pickups(item["id"], {1: "Three examples.", 0: "Welcome again."})
    assert result["pickup_lines"] == [0, 1] and fakes == [["Welcome again.", "Three examples."]]
    path = revoice.folder(item["id"]) / result["pickup_output"]
    audio = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                            "-ar", "8000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    data = np.frombuffer(audio, "<f4")
    for start, frequency in [(0.35, 1000), (1.25, 300), (2.2, 1000), (4.0, 300)]:
        samples = data[round(start * 8000):round((start + 0.2) * 8000)]
        peak = np.fft.rfftfreq(len(samples), 1 / 8000)[np.abs(np.fft.rfft(samples)).argmax()]
        assert peak == pytest.approx(frequency, abs=5)
    with pytest.raises(ValueError, match="changed"):
        revoice.pickups(item["id"], {0: "Old wording"})
    assert revoice.meta(item["id"])["pickup_updated"] == result["pickup_updated"]


def test_script_short_renders_ordered_broll_and_captions(tmp_path, monkeypatch, fakes):
    from app.models import ScriptShortRequest
    from app.pipeline import script_short

    library = tmp_path / "footage"
    library.mkdir()
    monkeypatch.setattr(settings, "broll_dir", library)
    for color in ("red", "blue"):
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=24:d=0.25",
            "-c:v", "libx264", str(library / f"{color}.mp4"),
        ], check=True)

    voice = tts.create("Target", consent=True)
    voice["builds"] = [{"version": 1, "prompt": "test.pt"}]
    tts.save(voice)
    ids = {asset["name"]: asset["id"] for asset in script_short.assets()}
    ordered = [ids["red.mp4"], ids["blue.mp4"]]
    request = ScriptShortRequest(title="Demo", text="Capture this example. Explain this step. Now show the result.",
                                 voice=voice["id"], assets=ordered)
    preview = script_short.preview(request)
    assert script_short.preview_file(preview["id"]).is_file()
    assert [line["start"] for line in preview["lines"]] == [0, 0.8, 1.6]
    assert script_short.preview(request) == preview
    from app.pipeline import broll_plan

    class Planner:
        vision_calls = 0

        def chat_json(self, prompt, schema, **kwargs):
            if kwargs.get("images"):
                self.vision_calls += 1
                return {"description": "A solid color plate", "kind": "other"}
            return {"scenes": [{"asset": ordered[0], "line": 0, "reason": "Start with red."},
                               {"asset": ordered[1], "line": 2, "reason": "Switch to blue."}]}

    assert preview["lines"][0]["heard"] == "x" and preview["lines"][0]["issues"]
    planner = Planner()
    plan = broll_plan.plan(preview["id"], ordered, planner)
    assert [(scene["asset"], scene["line"]) for scene in plan] == list(zip(ordered, [0, 2], strict=True))
    assert broll_plan.plan(preview["id"], ordered, planner) == plan
    assert planner.vision_calls == 2
    assert len(list((settings.data_dir / "broll_descriptions").glob("*.jpg"))) == 2
    with pytest.raises(ValueError, match="duplicates"):
        broll_plan.plan(preview["id"], [ordered[0], ordered[0]], planner)
    request.preview_id = preview["id"]
    request.scene_starts = [0, 2]
    with pytest.raises(ValueError, match="changed"):
        script_short.create(request.model_copy(update={"text": "Changed wording."}))
    with pytest.raises(ValueError, match="different script line"):
        script_short.create(request.model_copy(update={"scene_starts": [0, 0]}))
    from app.models import ScriptShortDraft

    draft = script_short.save_draft(ScriptShortDraft(
        title=request.title, text=request.text, voice=request.voice, version=1, assets=ordered,
        scene_lines={ordered[1]: 2}, timing="lines", section="export", preview_id=preview["id"],
    ))
    assert script_short.drafts() == [draft]
    assert script_short.preview_view(draft["preview_id"]) == preview

    import wave

    def read_audio(ident):
        with wave.open(str(script_short.preview_file(ident)), "rb") as wav:
            return wav.getframerate(), wav.readframes(wav.getnframes())

    def retake_voice(vid, texts, out_dir, names, version):
        fakes.append(texts)
        tone(out_dir / names[0], 0.6, freq=450)
        return [{"seconds": 0.6}]

    monkeypatch.setattr(tts, "speak", retake_voice)
    revised = script_short.retake(preview["id"], 1, request)
    assert [line["start"] for line in revised["lines"]] == pytest.approx([0, 0.8, 1.4])
    rate, before = read_audio(preview["id"])
    _, after = read_audio(revised["id"])
    assert before[:int(0.8 * rate) * 2] == after[:int(0.8 * rate) * 2]
    assert before[int(1.6 * rate) * 2:] == after[int(1.4 * rate) * 2:]
    assert before[int(0.8 * rate) * 2:int(1.4 * rate) * 2] != after[int(0.8 * rate) * 2:int(1.4 * rate) * 2]
    assert script_short.preview_view(preview["id"]) == preview
    request.preview_id = revised["id"]
    result = script_short.create(request)
    assert result["duration"] == 2.2 and result["version"] == 1
    assert fakes == [["Capture this example.", "Explain this step.", "Now show the result."], ["Explain this step."]]
    assert script_short.recent() == [result]
    video = script_short.file(result["id"], "short.mp4")
    assert revoice.probe(video) == pytest.approx((2.2, True, True), abs=0.06)
    srt = script_short.file(result["id"], "captions.srt").read_text()
    assert all(text in srt for text in ("CAPTURE THIS EXAMPLE.", "NOW SHOW THE", "RESULT."))

    for timestamp, channel in [(1.2, 0), (1.7, 2)]:
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(video), "-frames:v", "1",
            "-vf", "crop=2:2:0:0,format=rgb24", "-f", "rawvideo", "-",
        ], capture_output=True, check=True)
        assert result.stdout[channel] > 200 and sum(result.stdout[:3]) < 300
