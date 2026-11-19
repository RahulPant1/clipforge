import json

import pytest

from app.config import settings
from app.models import Word
from app.pipeline import presets as pr
from app.pipeline.captions import build_ass, build_hook_ass

WORDS = [Word(start=0.0, end=0.4, word=" Invest"), Word(start=0.4, end=0.8, word=" 500"),
         Word(start=0.8, end=1.2, word=" Rupees."), Word(start=1.5, end=1.9, word=" Today")]


def _style(ass: str, name: str = "Default") -> list[str]:
    line = next(x for x in ass.splitlines() if x.startswith(f"Style: {name},"))
    return line[len("Style: "):].split(",")


@pytest.fixture(autouse=True)
def _fonts_present(monkeypatch):
    """Every font 'exists' unless a test says otherwise (no fontconfig/Windows lookups in unit tests)."""
    monkeypatch.setattr(pr, "font_available", lambda family: True)


def test_classic_is_built_from_the_caption_settings(monkeypatch):
    monkeypatch.setattr(settings, "caption_font", "Verdana")
    monkeypatch.setattr(settings, "highlight_color", "&H000000FF")
    monkeypatch.setattr(settings, "caption_words", 4)
    p = pr.get_preset("classic")
    assert (p.font, p.active, p.words) == ("Verdana", "&H000000FF", 4)
    assert pr.get_preset(None).id == settings.caption_preset == "classic"
    assert pr.get_preset("nope").id == "classic"  # unknown id: warn, fall back


def test_every_builtin_preset_makes_a_valid_style():
    assert set(pr.all_presets()) >= {"classic", "punch", "clean", "boxed", "neon"}
    for p in pr.all_presets().values():
        style = _style(build_ass(WORDS, ["invest"], preset=p))
        assert len(style) == 23 and style[1] == p.font and style[2] == str(p.size)
        assert style[15] == ("3" if p.box else "1")  # BorderStyle: box per line vs outline
    boxed = _style(build_ass(WORDS, [], preset=pr.get_preset("boxed")))
    assert boxed[5] == pr.get_preset("boxed").outline_color  # the box colour is OutlineColour


def test_font_falls_back_to_the_first_one_libass_can_render(monkeypatch):
    monkeypatch.setattr(pr, "font_available", lambda family: family == "DejaVu Sans")
    assert pr.get_preset("punch").font == "DejaVu Sans"
    monkeypatch.setattr(pr, "font_available", lambda family: False)
    assert pr.get_preset("punch").font == "Impact"  # nothing found: first choice (libass substitutes, fonts.py warns)


def test_position_and_the_two_speaker_seam():
    p = pr.get_preset("classic")
    assert _style(build_ass(WORDS, [], preset=p, position="bottom"))[18:22] == ["2", "60", "60",
                                                                               str(settings.caption_margin_v)]
    assert _style(build_ass(WORDS, [], preset=p, position="middle"))[18:22] == ["5", "60", "60", "0"]
    assert _style(build_ass(WORDS, [], preset=p, position="bottom", middle=True))[18] == "5"  # seam wins
    assert pr.POSITIONS == ("bottom", "middle")


def test_karaoke_sets_alpha_on_every_word_and_keeps_case():
    clean = pr.get_preset("clean")
    ass = build_ass(WORDS, [], preset=clean)
    first = next(x for x in ass.splitlines() if x.startswith("Dialogue:"))
    # active word opaque, the others see-through — explicit on each word, since overrides carry over
    assert r"\1a&H00&\c&H00FFFFFF&\fscx104" in first and r"\1a&H50&\c&H50FFFFFF&" in first
    assert "Invest" in first and "INVEST" not in first and r"\blur1" in first
    assert r"\t(" not in ass  # clean doesn't pop
    punch = build_ass(WORDS, [], preset=pr.get_preset("punch"))
    assert "INVEST" in punch and r"\t(0,90," in punch
    assert sum(x.startswith("Dialogue:") for x in punch.splitlines()) == 4  # one event per word


def test_simple_style_is_one_plain_line_per_chunk():
    ass = build_ass(WORDS, ["invest"], preset=pr.get_preset("clean"), animate=False)
    events = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert len(events) == 2  # "Invest 500 Rupees." | "Today" (sentence end splits)
    assert events[0].endswith("Invest 500 Rupees.") and r"\1a&H00&" in events[0]  # opaque, no keyword colour
    assert r"\fscx" not in ass and r"\t(" not in ass


def test_hook_card_uses_the_preset_font():
    assert _style(build_hook_ass("Big claim", 30, font="Impact"), "Hook")[1] == "Impact"
    assert _style(build_hook_ass("Big claim", 30), "Hook")[1] == settings.caption_font


def test_custom_presets_from_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "caption_presets.json").write_text(json.dumps({
        "mine": {"label": "Mine", "fonts": ["Bahnschrift", "DejaVu Sans"], "size": 90, "active": "&H0000A5FF"},
        "solo": {"fonts": "Georgia", "uppercase": False},
        "broken": {"colour": "&H00FFFFFF"},
        "punch": {"label": "My punch", "size": 120},
    }))
    ps = pr.all_presets()
    assert ps["mine"].font == "Bahnschrift" and ps["mine"].size == 90 and ps["mine"].active == "&H0000A5FF"
    assert ps["mine"].text == pr.get_preset("classic").text  # missing fields: classic values
    assert ps["solo"].fonts == ("Georgia",) and ps["solo"].label == "solo" and not ps["solo"].uppercase
    assert "broken" not in ps and "unknown fields" in caplog.text
    assert ps["punch"].label == "My punch" and ps["punch"].size == 120  # replaces the built-in
    (tmp_path / "caption_presets.json").write_text("{not json")
    assert set(pr.all_presets()) == {"classic", "punch", "clean", "boxed", "neon"}


def test_api_lists_presets_and_preset_edits_mark_the_short_stale(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.models import Clip, JobStatus

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    app = create_app(pipeline=lambda *a: [], start_worker=False)
    store = app.state.store
    job = store.create("https://youtube.com/watch?v=x", 1)
    store.update(job.id, status=JobStatus.done,
                 clips=[Clip(index=1, start=0, end=30, title="t", hook="h", score=7, file="short_1.mp4")])
    with TestClient(app) as c:
        body = c.get("/api/caption-presets").json()
        assert body["default"] == "classic" and body["positions"] == ["bottom", "middle"]
        ids = [p["id"] for p in body["presets"]]
        assert ids[:5] == ["classic", "punch", "clean", "boxed", "neon"] and body["presets"][1]["font"] == "Impact"
        r = c.patch(f"/api/jobs/{job.id}/clips/1", json={"options": {"caption_preset": "neon",
                                                                     "caption_position": "middle"}})
        clip = r.json()["clips"][0]
        assert clip["stale"] and clip["options"]["caption_preset"] == "neon"
