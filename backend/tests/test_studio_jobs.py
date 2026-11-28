"""Studio API jobs survive client reconnects and report only that draft's files."""
import json
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.pipeline import script_short


class DraftModel:
    def unload(self):
        pass

    def chat_json(self, prompt, schema, **kwargs):
        if "scenes" in schema.get("properties", {}):
            return {"scenes": [{"line": 0, "prompt": "A moon above Earth"},
                               {"line": 1, "prompt": "A satellite in orbit"}]}
        return {"title": "Orbit", "text": "Gravity bends the path. A satellite stays in orbit."}


def test_draft_job_reconnect_and_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = create_app(start_worker=False, llm_factory=DraftModel)
    with TestClient(app) as starter:
        started = starter.post("/api/script-shorts/studio-jobs", json={"brief": "Explain an orbit"})
        assert started.status_code == 202
        ident = started.json()["id"]
    with TestClient(app) as client:
        route = f"/api/script-shorts/studio-jobs/{ident}"
        until = time.monotonic() + 5
        while time.monotonic() < until:
            state = client.get(route).json()
            if state["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert state == {"id": ident, "status": "ready", "draft_id": ident}
        assert script_short.drafts()[0]["storyboard"][1]["line"] == 1
        outputs = client.get(f"{route}/outputs").json()
        assert outputs["items"] == [{"kind": "draft", "path": str(
            tmp_path / "script_shorts" / "_drafts" / f"{ident}.json")}]
        for export_id, draft_id in (("0123456789", ident), ("abcdef0123", None)):
            folder = script_short.root() / export_id
            folder.mkdir()
            (folder / "short.json").write_text(json.dumps({"id": export_id, "draft_id": draft_id, "created": 1}))
            (folder / "short.mp4").touch()
        listed = client.get(f"{route}/outputs").json()["items"]
        assert listed == outputs["items"] + [
            {"kind": "short.mp4", "path": str(script_short.root() / "0123456789" / "short.mp4")},
        ]
        assert client.get("/api/script-shorts/studio-jobs/invalid").status_code == 404
