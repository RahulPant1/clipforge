"""Scene alignment, sequential image batches, and generated assets entering the B-roll library."""
import shutil
import subprocess

import pytest
from PIL import Image

from app.config import settings
from app.models import VisualPrompt
from app.pipeline import broll, storyboard, visuals


@pytest.mark.parametrize("starts", [[0, 2], [1, 2], [0, 0], [0, 3]])
def test_storyboard_uses_narration_line_boundaries(starts):
    class Gemma:
        def chat_json(self, _prompt, _schema, **_options):
            return {"scenes": [{"line": index, "prompt": "Plant by a window"} for index in starts]}

    script = "Look at the soil. Water near the roots. Let it drain."
    if starts == [0, 2]:
        scenes = storyboard.plan(script, 2, Gemma())
        assert [scene["text"] for scene in scenes] == ["Look at the soil.", "Let it drain."]
    else:
        with pytest.raises(RuntimeError, match="invalid scene timing"):
            storyboard.plan(script, 2, Gemma())


def test_failed_image_stops_batch_and_preserves_prior_result(monkeypatch, tmp_path):
    from contextlib import nullcontext

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(visuals, "_server", lambda: nullcontext((None, "")))
    calls = []

    def generate(_session, _base, item):
        calls.append(item.prompt)
        if item.prompt == "second":
            raise TimeoutError("Image generation timed out")
        return {"id": "first-id"}

    monkeypatch.setattr(visuals, "_generate", generate)
    results = visuals.generate([VisualPrompt(prompt=name) for name in ("first", "second", "third")])
    assert calls == ["first", "second"]
    assert results == [{"id": "first-id"}, {"error": "Image generation timed out"}]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="requires FFmpeg")
def test_save_generated_visual_as_broll(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "broll_dir", None)
    folder = tmp_path / "generated_images"
    folder.mkdir()
    ident = "a" * 32
    Image.new("RGB", (visuals.WIDTH, visuals.HEIGHT), "blue").save(folder / f"{ident}.png")

    motion, still = visuals.save(ident, True), visuals.save(ident, False)
    assert visuals.save(ident, True) == motion
    assert motion["id"] != still["id"]
    push, pan = visuals.save(ident, "push"), visuals.save(ident, "pan")
    assert len({asset["id"] for asset in (motion, still, push, pan)}) == 4
    for asset in (motion, still, push, pan):
        path, name = broll.library_assets()[asset["id"]]
        assert name.as_posix() == asset["name"]
        info = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,nb_frames",
            "-of", "csv=p=0", str(path),
        ], text=True)
        assert info.strip() == "720,1280,192"


def test_gpu_memory_retry_resumes_after_saved_images(monkeypatch, tmp_path):
    from contextlib import nullcontext

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    attempts = []

    def server(low_memory=False):
        return nullcontext((low_memory, ""))

    def generate(low_memory, _base, item):
        attempts.append((item.prompt, low_memory))
        if item.prompt == "second" and not low_memory:
            raise visuals.ImageMemoryError("GPU memory is full")
        return {"id": item.prompt}

    monkeypatch.setattr(visuals, "_server", server)
    monkeypatch.setattr(visuals, "_generate", generate)
    assert visuals.generate([VisualPrompt(prompt=name) for name in ("first", "second", "third")]) == [
        {"id": "first"}, {"id": "second"}, {"id": "third"},
    ]
    assert attempts == [("first", False), ("second", False), ("second", True), ("third", True)]


def test_background_storyboard_persists_progress_and_retries_only_failures(monkeypatch, tmp_path):
    import time
    from threading import Event

    from app.models import ScriptShortDraft
    from app.pipeline import script_short
    from app.pipeline.storyboard_jobs import StoryboardJobs

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    draft = script_short.save_draft(ScriptShortDraft(
        reference_image="a" * 32, visual_reference="Muted daylight",
        storyboard=[{"line": 0, "prompt": "First scene"}, {"line": 1, "prompt": "Second scene"}],
    ))
    ref = draft["id"]
    jobs = StoryboardJobs()
    started, unblock = Event(), Event()
    attempted = []

    def generate(items, *, progress):
        attempted.append([item.prompt for item in items])
        for index, item in enumerate(items):
            progress(index, None)
            if item.prompt.endswith("Second scene") and len(attempted) == 1:
                started.set()
                assert unblock.wait(3)
                progress(index, {"error": "Out of memory"})
            else:
                progress(index, {"id": f"{len(attempted)}-{index}"})

    def await_status(expected):
        for _ in range(150):
            record = jobs.get(ref)
            if record["status"] == expected:
                return record
            time.sleep(0.01)
        pytest.fail(f"Expected {expected}: {record}")

    monkeypatch.setattr(visuals, "generate", generate)
    jobs.submit(ref, [0, 1], lambda fn: fn())
    try:
        assert started.wait(3)
        assert [scene["status"] for scene in jobs.get(ref)["scenes"]] == ["ready", "running"]
    finally:
        unblock.set()
    failed = await_status("failed")
    assert failed["scenes"][0]["image_id"] == "1-0"
    assert failed["scenes"][1]["error"] == "Out of memory"
    jobs.submit(ref, [1], lambda fn: fn())
    ready = await_status("ready")
    assert ready["scenes"][0]["index"] == 1
    first = "Visual continuity: Muted daylight. Scene: First scene"
    second = "Visual continuity: Muted daylight. Scene: Second scene"
    assert attempted == [[first, second], [second]]


def test_theme_matching_reuses_assets_and_leaves_misses(monkeypatch, tmp_path):
    from app.models import ThemeAsset, ThemeMatchRequest
    from app.pipeline import themes

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "broll_dir", None)
    images = tmp_path / "generated_images"
    images.mkdir()
    image = "a" * 32
    Image.new("RGB", (12, 12)).save(images / f"{image}.png")
    track = tmp_path / "sounds" / "music" / "Piano.wav"
    track.parent.mkdir(parents=True)
    track.touch()
    themes.save(ThemeAsset(theme="Tutorial", kind="music", asset=track.name, description="Sparse piano"))
    themes.save(ThemeAsset(theme="Tutorial", kind="image", asset=image, description="Terracotta plant pot"))

    class Gemma:
        def chat_json(self, prompt, schema, **kwargs):
            if kwargs.get("images"):
                return {"reference": "Green plant in a terracotta pot"}
            assert "Terracotta plant pot" in prompt and "Sparse piano" in prompt
            return {
                "scenes": [{"candidate": 0, "reason": "Matches the pot"}, {"candidate": 0, "reason": "Duplicate"}],
                "music": {"candidate": 1, "reason": "Leaves room for speech"},
                "music_prompt": "Soft piano, instrumental.",
            }

    model = Gemma()
    assert themes.describe_reference(image, model) == "Green plant in a terracotta pot"
    matched = themes.match(ThemeMatchRequest(
        theme="tutorial", text="Look at the pot. Then water it.",
        scenes=[{"line": 0, "prompt": "Show the pot"}, {"line": 1, "prompt": "Show pouring water"}],
    ), model)
    assert matched["scenes"][0]["asset"]["asset"] == image
    assert matched["scenes"][1]["asset"] is None
    assert matched["music"]["asset"]["asset"] == "Piano.wav"
