"""Hindi (and other Indic scripts) burned in with a font that has the letters — not rows of empty boxes."""
import shutil

import pytest

from app.models import Word
from app.pipeline import fonts
from app.pipeline.captions import build_ass, build_hook_ass

HINDI = "इक दिन जब लेला रूठी, मजनू की किस्मत फूटी"


@pytest.fixture
def installed(monkeypatch):
    """Pretend exactly these font families are installed."""
    have: set[str] = set()
    monkeypatch.setattr(fonts, "font_available", lambda fam: fam in have)
    fonts._script_font.cache_clear()
    yield have
    fonts._script_font.cache_clear()


def test_script_detection():
    assert fonts.script_of(HINDI) == "Devanagari"
    assert fonts.script_of("Hinglish: kya baat hai, bro!") is None  # Latin letters: the caption font is fine
    assert fonts.script_of("Mostly English with one शब्द") == "Devanagari"
    assert fonts.script_of("வணக்கம்") == "Tamil" and fonts.script_of("নমস্কার") == "Bengali"


def test_font_for_switches_only_when_the_font_cant_draw_it(installed):
    installed.add("Noto Sans Devanagari")
    assert fonts.font_for("Hello there", "Montserrat") == "Montserrat"
    assert fonts.font_for(HINDI, "Montserrat") == "Noto Sans Devanagari"
    installed.add("Nirmala UI")
    fonts._script_font.cache_clear()
    assert fonts.font_for(HINDI, "Montserrat") == "Nirmala UI"  # Windows' font wins: covers every Indic script
    assert fonts.font_for(HINDI, "Mangal") == "Mangal"  # already a Devanagari font: keep it


def test_no_font_for_the_script_keeps_the_family_and_warns(installed, caplog):
    assert fonts.font_for(HINDI, "Arial") == "Arial"
    assert "fonts-noto-core" in caplog.text


def test_ass_styles_take_the_script_font():
    words = [Word(start=0, end=0.5, word=" लेला"), Word(start=0.5, end=1, word=" रूठी")]
    assert "Style: Default,Nirmala UI," in build_ass(words, [], font="Nirmala UI")
    assert "Style: Default,Nirmala UI," not in build_ass(words, [])
    assert "Style: Hook,Nirmala UI," in build_hook_ass("लैला को करना हो इंप्रेस", 5, font="Nirmala UI")


def test_render_uses_the_script_font_for_hindi_captions_and_hook(tmp_path, monkeypatch, installed):
    from app.models import Clip, RenderOptions, Segment
    from app.pipeline import render

    from .test_render import _video, needs_ffmpeg  # noqa: F401

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    installed.add("Nirmala UI")
    words = [Word(start=0.5 + i, end=1.3 + i, word=f" {w}") for i, w in enumerate(HINDI.split()[:4])]
    segs = [Segment(start=0.5, end=4.3, text=HINDI, words=words)]
    clip = Clip(index=1, start=0, end=5, title="t", hook="लैला को करना हो इंप्रेस", score=7,
                options=RenderOptions(jumpcut=False, reframe="center"))
    render.render_clip(_video(tmp_path, 6), clip, segs, tmp_path)
    assert "Style: Default,Nirmala UI," in (tmp_path / "short_1.ass").read_text()
    assert "Style: Hook,Nirmala UI," in (tmp_path / "short_1.hook.ass").read_text()


def test_carousel_and_cover_draw_hindi_with_the_script_font(tmp_path, monkeypatch, installed):
    from PIL import ImageFont

    from app.pipeline import carousel, cover

    installed.add("Nirmala UI")
    asked = []
    real = ImageFont.load_default

    def fake_pil_font(family, size):
        asked.append(family)
        return real(size)

    monkeypatch.setattr(carousel, "pil_font", fake_pil_font)
    monkeypatch.setattr(cover, "pil_font", fake_pil_font)
    carousel.render_carousel([{"title": "मिंटो फ्रेश", "text": HINDI}], tmp_path, "c")
    assert asked and set(asked) == {"Nirmala UI"}
    asked.clear()
    carousel.render_carousel([{"title": "English only", "text": "fine"}], tmp_path, "e")
    assert asked == []  # Latin: the usual carousel font
    import numpy as np

    cover.draw_cover(np.full((720, 1280, 3), 90, np.uint8), None, "लैला को करना हो इंप्रेस", tmp_path / "c.jpg")
    assert asked and set(asked) == {"Nirmala UI"}

