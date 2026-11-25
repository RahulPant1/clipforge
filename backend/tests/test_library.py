"""History: a readable folder per video + "ClipForge history.xlsx", kept even after a job is deleted."""
import json
import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import library as lib
from app.jobs import JobStore
from app.models import Clip, JobStatus, Post

META = {"meta_version": 2, "title": 'How I Manage My Time: 8 Tips?', "channel": "Jeff Su",
        "url": "https://www.youtube.com/watch?v=lriCkWIHEHs", "upload_date": "2024-03-01", "duration": 603,
        "views": 1234567, "categories": ["Education"], "tags": ["productivity", "time management"]}


def _job(store: JobStore, meta=META, files=True):
    job = store.create("https://www.youtube.com/watch?v=lriCkWIHEHs", 2)
    job = store.update(job.id, created_at=datetime(2026, 11, 20, 12, tzinfo=UTC))
    d = store.dir(job.id)
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "transcript_info.json").write_text(json.dumps({"language": "en", "duration": 600.0}))
    if files:
        for n in ("short_1.mp4", "short_1.cover.jpg", "short_1.srt", "short_1.posts.md", "short_1_ig_1.png"):
            (d / n).write_bytes(b"x" * 10)
    posts = {"youtube": Post(platform="youtube", angle="a", text="d", title="God's eye view", status="posted"),
             "instagram": Post(platform="instagram", angle="a", text="t", carousel=["short_1_ig_1.png"],
                               status="approved")}
    clips = [Clip(index=1, start=43.0, end=71.8, title="God's Eye View / Your Life", hook="See it all", score=9,
                  file="short_1.mp4", cover="short_1.cover.jpg", out_duration=28.8, content=posts,
                  keywords=["review"]),
             Clip(index=2, start=100, end=130, title="not made", hook="h", score=7)]  # candidate, never rendered
    return store.update(job.id, status=JobStatus.done, clips=clips)


def test_names_are_windows_safe_and_moments_link_to_the_second():
    assert lib.safe_name('a/b: "c"? <d>|*. ') == "a b c d"
    assert lib.safe_name("") == "untitled"
    assert lib.moment_url("https://www.youtube.com/watch?v=x", 43.9) == "https://www.youtube.com/watch?v=x&t=43s"
    assert lib.moment_url("https://youtu.be/x", 5) == "https://youtu.be/x?t=5s"
    assert lib.moment_url("talk.mp4", 5) == "talk.mp4"
    assert (lib.mmss(71.8), lib.mmss(3725), lib.mmss(None)) == ("1:11", "1:02:05", "")


def test_folder_with_linked_files_info_and_spreadsheet(tmp_path):
    from openpyxl import load_workbook

    store = JobStore(tmp_path / "data")
    job = _job(store)
    library = lib.Library(store, root=tmp_path / "lib", fetch_meta=False)
    library.touch(job.id)
    assert library.flush()

    folder = tmp_path / "lib" / f"2026-11-20 Jeff Su - How I Manage My Time 8 Tips [{job.id}]"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Short 1 - God's Eye View Your Life.mp4", "Short 1 - captions.srt", "Short 1 - carousel 1.png",
        "Short 1 - cover.jpg", "Short 1 - posts.md", "info.md"]
    src, dst = store.dir(job.id) / "short_1.mp4", folder / "Short 1 - God's Eye View Your Life.mp4"
    assert os.stat(src).st_ino == os.stat(dst).st_ino  # hard link: no extra disk space
    info = (folder / "info.md").read_text()
    assert "**Channel:** Jeff Su" in info and "**Views:** 1,234,567" in info and "**Language:** en" in info
    assert "[0:43–1:11](https://www.youtube.com/watch?v=lriCkWIHEHs&t=43s)" in info and "| posted | approved |" in info

    wb = load_workbook(tmp_path / "lib" / lib.XLSX)
    v = [c.value for c in wb["Videos"][2]]
    assert v[1:4] == ["Jeff Su", META["title"], datetime(2024, 3, 1)] and v[4] == "10:03" and v[5] == 1234567
    assert v[9:13] == ["done", 2, 1, 1]  # status, candidates, shorts made, posts marked posted
    assert wb["Videos"].cell(2, 3).hyperlink.target == META["url"]
    s = [c.value for c in wb["Shorts"][2]]
    assert s[3:8] == [1, "God's Eye View / Your Life", "See it all", "0:43", "1:11"]
    assert s[11:15] == ["posted", "approved", None, None]
    assert wb["Shorts"].cell(2, 7).hyperlink.target.endswith("&t=43s")
    assert wb["Shorts"].max_row == 2  # the never-rendered candidate isn't a short
    assert wb["Videos"].freeze_panes == "A2" and wb["Videos"].auto_filter.ref

    replacement = src.with_suffix(".new")
    replacement.write_bytes(b"y" * 10)
    replacement.replace(src)
    assert dst.read_bytes() == b"x" * 10
    library.touch(job.id)
    assert library.flush()
    assert dst.read_bytes() == b"y" * 10
    assert src.samefile(dst)


def test_retitle_renames_and_deleting_the_job_keeps_its_history(tmp_path):
    from openpyxl import load_workbook

    store = JobStore(tmp_path / "data")
    job = _job(store)
    library = lib.Library(store, root=tmp_path / "lib", fetch_meta=False)
    store.listeners.append(library.touch)  # as in create_app: every save queues the job
    library.flush()
    store.update_clip(job.id, 1, lambda c: setattr(c, "title", "Better title"))
    library.flush()
    folder = next((tmp_path / "lib").glob("2026-11-20*"))
    assert [p.name for p in folder.glob("*.mp4")] == ["Short 1 - Better title.mp4"]  # old name removed

    store.delete(job.id)
    library.flush()
    assert (folder / "Short 1 - Better title.mp4").read_bytes() == b"x" * 10  # the short outlives the job
    assert "**Deleted from ClipForge:**" in (folder / "info.md").read_text()
    row = [c.value for c in load_workbook(tmp_path / "lib" / lib.XLSX)["Videos"][2]]
    assert row[9] == "deleted"


def test_old_jobs_get_their_metadata_once_without_downloading(tmp_path, monkeypatch):
    from app.pipeline import download

    calls = []

    def fake_fetch(url):
        calls.append(url)
        return {**META, "channel": "Jeff Su", "title": "From YouTube"}

    monkeypatch.setattr(download, "fetch_meta", fake_fetch)
    store = JobStore(tmp_path / "data")
    job = _job(store, meta={"title": "Old", "channel": "", "url": META["url"]})
    library = lib.Library(store, root=tmp_path / "lib")
    library.touch(job.id)
    library.flush()
    saved = json.loads((store.dir(job.id) / "meta.json").read_text())
    assert saved["meta_version"] == 2 and saved["views"] == 1234567 and saved["title"] == "From YouTube"
    library.touch(job.id)
    library.flush()
    assert len(calls) == 1  # never fetched again

    monkeypatch.setattr(download, "fetch_meta", lambda url: (_ for _ in ()).throw(RuntimeError("offline")))
    other = _job(store, meta={"title": "Old", "channel": "", "url": META["url"]})
    library.touch(other.id)
    library.flush()  # a failed lookup doesn't fail the history, and isn't retried forever
    assert json.loads((store.dir(other.id) / "meta.json").read_text())["meta_error"] == "offline"


def test_busy_jobs_wait_and_a_locked_spreadsheet_is_retried(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "data")
    job = _job(store)
    store.update(job.id, status=JobStatus.running)
    library = lib.Library(store, root=tmp_path / "lib", fetch_meta=False)
    library.touch(job.id)
    library.flush()
    assert not list((tmp_path / "lib").glob("2026*"))  # running: recorded when it finishes

    store.update(job.id, status=JobStatus.done)
    real = os.replace
    monkeypatch.setattr(lib.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError())
                        if str(b).endswith(".xlsx") else real(a, b))
    library.touch(job.id)
    assert library.flush() is False  # open in Excel: the record is saved, the spreadsheet isn't
    monkeypatch.setattr(lib.os, "replace", real)
    assert library.flush() is True  # nothing new touched, but the missed write is retried
    from openpyxl import load_workbook

    assert load_workbook(tmp_path / "lib" / lib.XLSX)["Videos"].cell(2, 15).value == job.id


def test_api_serves_the_spreadsheet(tmp_path):
    from app.config import settings
    from app.main import create_app

    app = create_app(pipeline=lambda *a: [], start_worker=False)
    app.state.library.fetch_meta = False
    job = _job(app.state.store)
    with TestClient(app) as c:
        info = c.get("/api/library").json()
        assert info["dir"] == str((settings.data_dir / "library").resolve())
        r = c.get("/api/library/history.xlsx")
        assert r.status_code == 200 and r.content[:2] == b"PK"  # xlsx = zip
    assert any(job.id in p.name for p in (settings.data_dir / "library").iterdir())


@pytest.mark.parametrize("path,expected", [("/mnt/d/Videos/ClipForge", "D:\\Videos\\ClipForge")])
def test_windows_path(path, expected):
    from pathlib import Path

    assert lib.windows_path(Path(path)) == expected


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=lriCkWIHEHs", "https://youtu.be/lriCkWIHEHs?si=abc",
                                 "https://www.youtube.com/watch?app=desktop&v=lriCkWIHEHs&t=42s",
                                 "https://m.youtube.com/shorts/lriCkWIHEHs", "https://www.youtube.com/embed/lriCkWIHEHs"])
def test_any_link_form_is_the_same_video(url):
    assert lib.video_key(url) == "yt:lriCkWIHEHs"


def test_lookup_finds_live_and_deleted_jobs_for_the_video(tmp_path):
    store = JobStore(tmp_path / "data")
    old = _job(store)
    library = lib.Library(store, root=tmp_path / "lib", fetch_meta=False)
    store.listeners.append(library.touch)
    library.touch(old.id)
    library.flush()
    store.delete(old.id)
    library.flush()
    live = _job(store)
    store.update(live.id, created_at=datetime(2026, 11, 21, tzinfo=UTC))
    store.create("https://www.youtube.com/watch?v=someOtherVid", 1)

    found = library.lookup("https://youtu.be/lriCkWIHEHs")
    assert [(m["id"], m["exists"]) for m in found] == [(live.id, True), (old.id, False)]  # newest first
    assert found[0]["shorts"] == ["God's Eye View / Your Life"] and found[0]["posted"] == 1
    assert found[1]["status"] == "deleted" and found[1]["folder_kept"] and found[1]["shorts"]
    assert library.lookup("https://www.youtube.com/watch?v=neverDoneIt") == []
    assert library.lookup("not a url") == []


def test_api_lookup(tmp_path):
    from app.main import create_app

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    job = _job(app.state.store)
    with TestClient(app) as c:
        r = c.get("/api/library/lookup", params={"url": "https://youtu.be/lriCkWIHEHs"}).json()
        assert [m["id"] for m in r["matches"]] == [job.id] and r["matches"][0]["exists"]
