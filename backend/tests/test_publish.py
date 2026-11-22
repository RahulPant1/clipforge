"""Publish: when a post went live and its link, kept on the post and carried into the history."""
from fastapi.testclient import TestClient

from app import library as lib
from app.models import Clip, JobStatus, Post

URL = "https://www.youtube.com/shorts/abcdefghijk"


def _app():
    from app.main import create_app

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    store = app.state.store
    job = store.create("https://www.youtube.com/watch?v=lriCkWIHEHs", 1)
    posts = {p: Post(platform=p, angle="a", text="t", title="T" if p == "youtube" else None)
             for p in ("youtube", "x")}
    store.update(job.id, status=JobStatus.done,
                 clips=[Clip(index=1, start=0, end=30, title="Short", hook="h", score=8, file="short_1.mp4",
                             content=posts)])
    (store.dir(job.id) / "short_1.mp4").write_bytes(b"x")
    return app, store, job.id


def test_posted_at_follows_the_status():
    app, store, jid = _app()
    with TestClient(app) as c:
        u = f"/api/jobs/{jid}/clips/1/content/youtube"
        assert c.patch(u, json={"status": "approved"}).json()["posted_at"] is None
        posted = c.patch(u, json={"status": "posted"}).json()
        assert posted["posted_at"]
        assert c.patch(u, json={"status": "posted"}).json()["posted_at"] == posted["posted_at"]  # not re-stamped
        assert c.patch(u, json={"status": "draft"}).json()["posted_at"] is None


def test_pasting_the_live_link_marks_it_posted_and_is_validated():
    app, store, jid = _app()
    with TestClient(app) as c:
        u = f"/api/jobs/{jid}/clips/1/content/youtube"
        assert c.patch(u, json={"url": "youtube.com/shorts/x"}).status_code == 400
        p = c.patch(u, json={"url": f"  {URL} "}).json()
        assert (p["url"], p["status"]) == (URL, "posted") and p["posted_at"]
        assert c.patch(u, json={"url": ""}).json()["url"] == ""  # cleared; status stays yours to change
        assert c.patch(u, json={"url": "x" * 501}).status_code == 422


def test_history_links_each_posted_status_to_the_live_post(tmp_path):
    from openpyxl import load_workbook

    app, store, jid = _app()
    with TestClient(app) as c:
        c.patch(f"/api/jobs/{jid}/clips/1/content/youtube", json={"url": URL})
    library = lib.Library(store, root=tmp_path / "lib", fetch_meta=False)
    library.touch(jid)
    library.flush()
    ws = load_workbook(tmp_path / "lib" / lib.XLSX)["Shorts"]
    assert ws.cell(2, 12).value == "posted" and ws.cell(2, 12).hyperlink.target == URL
    assert ws.cell(2, 14).value == "draft" and ws.cell(2, 14).hyperlink is None
    info = next((tmp_path / "lib").glob("*/info.md")).read_text()
    assert f"[posted]({URL})" in info
