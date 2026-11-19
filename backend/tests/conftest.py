import pytest

from app.models import Segment, Word


def make_segs(n: int = 30, seg_len: float = 8.0) -> list[Segment]:
    """Synthetic transcript: n segments of 4 words each."""
    segs = []
    for i in range(n):
        s = i * seg_len
        words = [Word(start=s + j * 2, end=s + j * 2 + 1.8, word=f" w{i}_{j}") for j in range(4)]
        text = " ".join(w.word.strip() for w in words)
        segs.append(Segment(start=s, end=s + seg_len - 0.2, text=text, words=words))
    return segs


@pytest.fixture(autouse=True)
def _offline_defaults(monkeypatch, tmp_path):
    """Tests never download the face model (centre crop unless a test opts in) and never
    write to backend/data (history, jobs) — everything goes to a temp dir."""
    from app.config import settings

    monkeypatch.setattr(settings, "reframe_mode", "center")
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    from app.pipeline import fonts

    monkeypatch.setattr(fonts, "WINDOWS_FONTS", tmp_path / "no-windows-fonts")  # never scan /mnt/c in tests


@pytest.fixture
def segs():
    return make_segs()


class FakeLLM:
    def __init__(self, clips):
        self.clips = clips
        self.calls = 0
        self.unloaded = False

    def chat_json(self, prompt, schema, temperature=None, think=None):
        self.calls += 1
        return {"clips": self.clips}

    def unload(self):
        self.unloaded = True


CANNED = {
    "posts": {"posts": ["Most people ignore the 8th wonder of the world. Big mistake.",
                        "Start 5 years earlier and your final wealth can double.", "Would you start today?"],
              "hashtags": ["money", "#investing", "finance"]},
    "caption_hook": {"caption_hook": "POV: you just found the 8th wonder of the world",
                     "caption_body": "Start early.\nStay consistent.", "cta": "Save this for later 📌",
                     "hashtags": ["money", "investing", "money"], "cover_text": "Money doubles",
                     "carousel": [{"title": "Your money can double", "text": "Here is how"},
                                  {"title": "Start early", "text": "Five years earlier can double wealth"},
                                  {"title": "Follow for more", "text": "One money idea a day"}]},
    "pinned_comment": {"title": "The 8th wonder most people never use", "description": "Compound interest, simply.",
                       "hashtags": ["money"], "tags": ["compound interest", "investing"],
                       "pinned_comment": "When did you start investing?"},
    "takeaway": {"hook": "I almost ignored this 30-second clip.", "body": "It explained compounding.\n\nSimply.",
                 "takeaway": "Time beats timing.", "question": "When did you start?",
                 "hashtags": ["finance", "career"]},
}


class SchemaFakeLLM:
    """Answers content prompts with canned, schema-shaped JSON (keyed by a distinctive schema field)."""

    def __init__(self, fail_on: str | None = None):
        self.prompts: list[str] = []
        self.fail_on = fail_on

    def chat_json(self, prompt, schema, temperature=None, think=None):
        self.prompts.append(prompt)
        props = schema["properties"]
        for key, answer in CANNED.items():
            if key in props:
                if self.fail_on == key:
                    raise ConnectionError("boom")
                return answer
        raise AssertionError(f"unexpected schema {list(props)}")

    def unload(self):
        pass
