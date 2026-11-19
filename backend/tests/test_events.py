import asyncio
import json
import threading

from app import events as ev
from app.jobs import JobStore
from app.models import JobStatus


def _parse(msgs: list[str]) -> list[tuple[str, object]]:
    out = []
    for m in msgs:
        if m.startswith(":"):
            out.append(("ping", None))
            continue
        lines = dict(x.split(": ", 1) for x in m.strip().splitlines() if ": " in x and not x.startswith("retry"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _collect(store, n, job_id=None, during=None):
    """Run the stream until it has yielded n messages (or n(message) is true); `during(store)` makes changes
    from another thread."""
    done = n if callable(n) else lambda m: len(got) >= n
    got: list[str] = []

    async def go():
        async def disconnected():
            return bool(got) and done(got[-1])

        if during:
            threading.Timer(0.05, during, args=(store,)).start()
        async for m in ev.job_events(store, disconnected, job_id):
            got.append(m)
            if done(m):
                break
        return got

    return asyncio.run(asyncio.wait_for(go(), timeout=5))


def test_store_change_feed(tmp_path):
    store = JobStore(tmp_path)
    v0 = store.version
    a = store.create("https://x/a", 1)
    b = store.create("https://x/b", 1)
    store.update(a.id, progress=0.5)
    v, jobs, gone = store.changes(v0, timeout=0)
    assert v == v0 + 3 and {j.id for j in jobs} == {a.id, b.id} and gone == []
    assert next(j for j in jobs if j.id == a.id).progress == 0.5  # latest state, once
    store.delete(b.id)
    assert store.changes(v, timeout=0)[1:] == ([], [b.id])
    assert store.changes(store.version, timeout=0.01)[1:] == ([], [])  # nothing new: times out empty


def test_changes_wakes_up_when_another_thread_saves(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("https://x/a", 1)
    v = store.version
    threading.Timer(0.05, lambda: store.update(job.id, message="hi")).start()
    new, jobs, _ = store.changes(v, timeout=5)
    assert new > v and jobs[0].message == "hi"


def test_stream_sends_snapshot_then_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "MIN_GAP", 0)
    store = JobStore(tmp_path)
    a = store.create("https://x/a", 1)

    def work(s):
        s.update(a.id, status=JobStatus.running, progress=0.3, message="Transcribing")

    msgs = _collect(store, 2, during=work)
    assert msgs[0].startswith("retry: ")
    (k0, snap), (k1, job) = _parse(msgs)
    assert k0 == "snapshot" and [j["id"] for j in snap] == [a.id]
    assert k1 == "job" and job["id"] == a.id and job["progress"] == 0.3 and job["status"] == "running"


def test_job_stream_only_sends_its_job_and_its_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "MIN_GAP", 0)
    store = JobStore(tmp_path)
    a = store.create("https://x/a", 1)
    b = store.create("https://x/b", 1)

    def work(s):
        s.update(b.id, progress=0.9)  # other job: filtered out
        s.update(a.id, progress=0.1)
        s.delete(a.id)

    # the three changes may reach the stream in one wake-up (then only "deleted": the job is gone)
    kinds = _parse(_collect(store, lambda m: "event: deleted" in m, job_id=a.id, during=work))
    assert kinds[0] == ("snapshot", kinds[0][1]) and [j["id"] for j in kinds[0][1]] == [a.id]
    rest = [k for k in kinds[1:] if k[0] != "ping"]
    assert all(k[0] == "deleted" and k[1] == {"id": a.id} or k[1]["id"] == a.id for k in rest)
    assert rest[-1] == ("deleted", {"id": a.id})


def test_stream_pings_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "HEARTBEAT", 0.05)
    store = JobStore(tmp_path)
    assert _parse(_collect(store, 2)) == [("snapshot", []), ("ping", None)]


def test_events_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = create_app(pipeline=lambda *a: [], start_worker=False)
    with TestClient(app) as c:
        assert c.get("/api/jobs/nope/events").status_code == 404
    routes = {r.path for r in app.routes}
    assert {"/api/events", "/api/jobs/{job_id}/events"} <= routes


def test_rapid_progress_is_coalesced(tmp_path, monkeypatch):
    """A burst of progress ticks reaches the stream as a few messages, the last one carrying the final state."""
    monkeypatch.setattr(ev, "MIN_GAP", 0.1)
    store = JobStore(tmp_path)
    a = store.create("https://x/a", 1)

    def work(s):
        for i in range(1, 201):
            s.update(a.id, progress=i / 200)

    async def go():
        got = []
        threading.Timer(0.05, work, args=(store,)).start()

        async def disconnected():
            return False

        async for m in ev.job_events(store, disconnected):
            got.append(m)
            if '"progress":1.0' in m:
                return got

    msgs = _parse(asyncio.run(asyncio.wait_for(go(), timeout=10)))
    assert msgs[-1][1]["progress"] == 1.0 and len(msgs) < 30  # 200 saves, far fewer messages
