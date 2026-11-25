from app.config import settings
from app.models import Segment
from app.pipeline import orchestrator, transcript_edit
from app.pipeline.ingest_cache import source_cache_usage
from tests.conftest import FakeLLM


def test_jobs_share_download_and_raw_transcript_but_keep_edits(tmp_path, monkeypatch):
    downloads, transcriptions = [], []

    def fetch(url, directory, report):
        downloads.append(url)
        path = directory / "source.mp4"
        path.write_bytes(b"video")
        return path

    def transcribe(video, directory, report):
        transcriptions.append(video)
        return [Segment(start=0, end=2, text="original")]

    monkeypatch.setattr(orchestrator, "download", fetch)
    monkeypatch.setattr(orchestrator, "transcribe", transcribe)
    monkeypatch.setattr(orchestrator, "probe_duration", lambda _: 2)

    def ingest(url, name):
        return orchestrator.ingest(url, tmp_path / name, lambda *args: None, FakeLLM([]))

    first, _, _ = ingest("https://youtu.be/lriCkWIHEHs", "first")
    transcript_edit.save(first.parent, [Segment(start=0, end=2, text="edited")])
    second, segs, _ = ingest("https://www.youtube.com/watch?v=lriCkWIHEHs", "second")
    assert first.samefile(second)
    assert segs[0].text == "original"
    assert len(downloads) == len(transcriptions) == 1
    assert source_cache_usage()["reclaimable_bytes"] == 0

    monkeypatch.setattr(settings, "whisper_language", "fr")
    third, _, _ = ingest("https://youtu.be/lriCkWIHEHs", "third")
    assert third.samefile(first)
    assert len(downloads) == 1 and len(transcriptions) == 2
    for path in (first, second, third):
        path.unlink()
    assert source_cache_usage()["reclaimable_bytes"] == len(b"video")


def test_local_source_reuse_keeps_original_independent(tmp_path):
    from app.pipeline.ingest_cache import source_for

    user_file = tmp_path / "input.mp4"
    user_file.write_bytes(b"original")
    dirs = [tmp_path / name for name in ("first", "second")]
    for directory in dirs:
        directory.mkdir()
    def no_download(*args):
        raise AssertionError("local files should never be downloaded")

    first, second = [source_for(str(user_file), directory, no_download) for directory in dirs]
    user_file.write_bytes(b"modified")
    assert first.samefile(second)
    assert second.read_bytes() == b"original"


def test_transcript_cache_respects_model_override_without_redownloading(tmp_path, monkeypatch):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"same source")
    calls = []

    def transcribe(video, directory, report, *, whisper_model=None):
        selected = whisper_model or settings.whisper_model
        calls.append(selected)
        return [Segment(start=0, end=2, text=selected)]

    monkeypatch.setattr(settings, "whisper_model", "small")
    monkeypatch.setattr(orchestrator, "transcribe", transcribe)
    monkeypatch.setattr(orchestrator, "probe_duration", lambda _: 2)

    def ingest(name, model=None):
        return orchestrator.ingest(str(source), tmp_path / name, lambda *args: None, FakeLLM([]),
                                   whisper_model=model)

    default_video, default, _ = ingest("default")
    turbo_video, turbo, _ = ingest("turbo", "large-v3-turbo")
    _, cached, _ = ingest("turbo-again", "large-v3-turbo")
    assert calls == ["small", "large-v3-turbo"]
    assert [default[0].text, turbo[0].text, cached[0].text] == ["small", "large-v3-turbo", "large-v3-turbo"]
    assert default_video.samefile(turbo_video)


def test_turbo_prefers_complete_local_weights():
    from app.pipeline.transcribe import model_location

    directory = settings.data_dir / "models" / "whisper" / "large-v3-turbo"
    directory.mkdir(parents=True)
    assert model_location("large-v3-turbo") == "large-v3-turbo"
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (directory / name).touch()
    assert model_location("large-v3-turbo") == str(directory.resolve())
    assert model_location("small") == "small"
