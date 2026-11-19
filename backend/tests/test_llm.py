"""OllamaClient request shape + per-phase think wiring — requests.post is faked, Ollama is never called."""
import pytest

from app.config import Settings, settings
from app.pipeline import llm as llm_mod
from app.pipeline.llm import OllamaClient


class FakeResponse:
    def __init__(self, status=200, payload=None, reason="OK"):
        self.status_code, self._payload, self.reason, self.text = status, payload or {}, reason, ""

    def json(self):
        return self._payload


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []
    reply = {"response": FakeResponse(payload={"message": {"content": '{"ok": true}'}, "done_reason": "stop"})}

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return reply["response"]

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    return calls, reply


@pytest.mark.parametrize("think", [None, False, True])
def test_think_is_only_sent_when_given(sent, think):
    calls, _ = sent
    assert OllamaClient().chat_json("hi", {"type": "object"}, think=think) == {"ok": True}
    if think is None:
        assert "think" not in calls[0]  # model default — works for thinking and non-thinking models
    else:
        assert calls[0]["think"] is think


def test_ollama_error_message_is_kept(sent):
    _, reply = sent
    reply["response"] = FakeResponse(400, {"error": '"gemma3:4b" does not support thinking'}, "Bad Request")
    with pytest.raises(RuntimeError, match="does not support thinking"):
        OllamaClient().chat_json("hi", {"type": "object"})


def test_running_out_of_context_is_a_clear_error(sent):
    # Seen with gemma4: thinking used the whole num_ctx, Ollama stopped with done_reason=length and empty content,
    # which used to surface as "JSONDecodeError: Expecting value".
    _, reply = sent
    reply["response"] = FakeResponse(payload={"message": {"content": ""}, "done_reason": "length"})
    with pytest.raises(RuntimeError, match="ran out of context"):
        OllamaClient().chat_json("hi", {"type": "object"})


@pytest.mark.parametrize("raw,expected", [("false", False), ("true", True)])
def test_think_settings_parse_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("CONTENT_THINK", raw)
    monkeypatch.setenv("SELECT_THINK", raw)
    s = Settings(_env_file=None)
    assert s.content_think is expected and s.select_think is expected


def test_think_settings_default_to_unset(monkeypatch):
    monkeypatch.delenv("CONTENT_THINK", raising=False)
    monkeypatch.delenv("SELECT_THINK", raising=False)
    s = Settings(_env_file=None)
    assert s.content_think is None and s.select_think is None


class ThinkRecorder:
    def __init__(self, answer):
        self.answer, self.thinks = answer, []

    def chat_json(self, prompt, schema, temperature=None, think=None):
        self.thinks.append(think)
        return self.answer(schema)

    def unload(self):
        pass


def test_each_phase_uses_its_own_think_setting(monkeypatch, tmp_path, segs):
    from app.models import Clip
    from app.pipeline.content import ContentContext, generate_post
    from app.pipeline.highlights import select_highlights
    from app.pipeline.history import ContentHistory

    from .conftest import CANNED

    monkeypatch.setattr(settings, "select_think", None)
    monkeypatch.setattr(settings, "content_think", False)

    picker = ThinkRecorder(lambda schema: {"clips": []})
    assert select_highlights(segs, 2, picker) == []  # no clips is fine — we only check what was sent
    assert picker.thinks and all(t is None for t in picker.thinks)

    writer = ThinkRecorder(lambda schema: CANNED["posts"])
    clip = Clip(index=1, start=0, end=30, title="t", hook="h", score=8)
    ctx = ContentContext(clip=clip, text="hello", video_title="v", channel="c", url="")
    generate_post("x", ctx, writer, ContentHistory(tmp_path / "h.json"), tmp_path)
    assert writer.thinks == [False]
