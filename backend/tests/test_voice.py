from app.config import settings
from app.models import BrandVoice, Clip, JobStatus, Post
from app.pipeline import voice
from app.pipeline.content import ANGLES, NAMES, build_prompt, generate_post
from app.pipeline.history import ContentHistory

from .conftest import SchemaFakeLLM
from .test_content import _ctx

LI = ["shipped a thing today.\n\nno launch video. no thread. just a link.\n\n— r",
      "hiring is broken and we all know it.\n\nhere's what i do instead.\n\n— r"]


def test_clean_trims_dedupes_and_caps():
    raw = BrandVoice(notes="  lowercase, dry  ", examples={
        "linkedin": ["  a  ", "a", "", *[f"post {i}" for i in range(10)]],
        "tiktok": ["unknown platform"],
        "x": ["y" * 5000],
    })
    v = voice.clean(raw, list(ANGLES))
    assert v.notes == "lowercase, dry" and "tiktok" not in v.examples
    assert v.examples["linkedin"] == ["a", "post 0", "post 1", "post 2", "post 3"]  # MAX_EXAMPLES, no dupes
    assert len(v.examples["x"][0]) == voice.MAX_CHARS


def test_save_load_roundtrip_and_broken_file():
    assert voice.load() == BrandVoice()  # nothing saved yet
    v = voice.save(BrandVoice(notes="dry", examples={"linkedin": LI}))
    assert voice.load() == v
    (settings.data_dir / "brand_voice.json").write_text("{nope")
    assert voice.load() == BrandVoice()  # never blocks writing posts


def test_prompt_gets_examples_notes_and_keeps_the_guardrail():
    v = BrandVoice(notes="lowercase, dry humour, sign off with — r", examples={"linkedin": LI})
    p = build_prompt("linkedin", "framework", _ctx(), [], v)
    assert "<example 1>\n" + LI[0] + "\n</example 1>" in p and "<example 2>" in p
    assert "Voice: lowercase, dry humour" in p and settings.content_voice not in p
    assert "never reuse their facts" in p and "Never invent facts" in p
    assert p.index("</example 2>") < p.index("Hard rules")
    # off, or no brand voice: the prompt is exactly as before
    assert build_prompt("linkedin", "framework", _ctx(), [], v.model_copy(update={"enabled": False})) == \
        build_prompt("linkedin", "framework", _ctx(), [])
    assert "<example" not in build_prompt("linkedin", "framework", _ctx(), [], BrandVoice())


def test_platform_without_examples_borrows_a_few_from_the_others():
    v = BrandVoice(examples={"linkedin": LI + ["third"]})
    p = build_prompt("x", "hot_take", _ctx(), [], v)
    assert "on other platforms" in p and f"<example 1 ({NAMES['linkedin']})>" in p
    assert "<example 3" not in p and f"Adapt it to {NAMES['x']}" in p  # BORROW = 2


def test_generate_post_uses_the_saved_voice(tmp_path):
    voice.save(BrandVoice(examples={"linkedin": LI}))
    llm = SchemaFakeLLM()
    generate_post("linkedin", _ctx(), llm, ContentHistory(tmp_path / "h.json"), tmp_path)
    assert LI[1] in llm.prompts[-1]


def test_api_save_and_suggestions():
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(pipeline=lambda *a: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    li = lambda text, **kw: Post(platform="linkedin", angle="framework", text=text, **kw)  # noqa: E731
    clip = Clip(index=1, start=0, end=30, title="t", hook="h", score=7, content={
        "linkedin": li("my edited post", edited=True),
        "youtube": Post(platform="youtube", angle="a", text="desc", title="My title", status="posted"),
        "x": Post(platform="x", angle="hot_take", text="untouched draft"),
    })
    store.update(job.id, status=JobStatus.done, clips=[clip])
    with TestClient(app) as c:
        body = c.get("/api/brand-voice").json()
        assert body["voice"] == BrandVoice().model_dump() and {p["key"] for p in body["platforms"]} == set(ANGLES)
        sug = c.get("/api/brand-voice/suggestions").json()
        assert sug["linkedin"] == ["my edited post"] and sug["youtube"] == ["My title\n\ndesc"] and sug["x"] == []
        r = c.put("/api/brand-voice", json={"notes": " dry ", "examples": {"linkedin": ["my edited post", " "]}})
        assert r.json()["voice"]["examples"] == {"linkedin": ["my edited post"]}
        assert voice.load().notes == "dry"
        assert c.get("/api/brand-voice/suggestions").json()["linkedin"] == []  # already an example
        assert c.put("/api/brand-voice", json={"examples": {"x": "not a list"}}).status_code == 422
