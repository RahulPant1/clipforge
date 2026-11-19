"""Caption font resolution. fontconfig answers are faked so this runs the same on any machine."""
import logging
import shutil
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline import fonts


@pytest.fixture(autouse=True)
def _fresh_cache():
    fonts._resolve.cache_clear()
    yield
    fonts._resolve.cache_clear()


def fake_fc(system: dict[str, str], scans: dict[str, str]):
    """system: family -> what `fc-match` answers; scans: folder -> what `fc-scan` prints."""
    calls = []

    def _fc(*args):
        calls.append(args)
        if args[0] == "match":
            return system.get(args[-1], "DejaVu Sans")
        if args[0] == "scan":
            return scans.get(args[-1], "")
        return ""

    return _fc, calls


def test_installed_font_needs_no_fontsdir(monkeypatch):
    _fc, _ = fake_fc({"Arial": "Arial"}, {})
    monkeypatch.setattr(fonts, "_fc", _fc)
    assert fonts.caption_fonts_dir("Arial") is None
    assert fonts.fontsdir_option("Arial") == ""


def test_windows_font_is_copied_once_and_used(monkeypatch, tmp_path):
    win = tmp_path / "Windows" / "Fonts"
    win.mkdir(parents=True)
    (win / "arialbd.ttf").write_bytes(b"fake-bold")
    (win / "arial.ttf").write_bytes(b"fake-regular")
    (win / "segoeui.ttf").write_bytes(b"other")
    monkeypatch.setattr(fonts, "WINDOWS_FONTS", win)
    scan = (f"Arial\t{win}/arial.ttf\nArial,Arial Negreta\t{win}/arialbd.ttf\nSegoe UI\t{win}/segoeui.ttf\n")
    _fc, calls = fake_fc({}, {str(win): scan})
    monkeypatch.setattr(fonts, "_fc", _fc)

    local = fonts.caption_fonts_dir("Arial")

    assert local == (settings.data_dir / "fonts").resolve()
    assert sorted(p.name for p in local.glob("*.ttf")) == ["arial.ttf", "arialbd.ttf"]  # only that family
    assert (local / fonts.SCAN_CACHE).exists()  # the slow scan is cached…
    fonts._resolve.cache_clear()
    calls.clear()
    monkeypatch.setattr(fonts, "_fc", fake_fc({}, {str(local): f"Arial\t{local}/arial.ttf\n"})[0])
    assert fonts.caption_fonts_dir("Arial") == local  # …and next time the local copy is found directly
    assert not any(a[0] == "scan" and a[-1] == str(win) for a in calls)


def test_missing_font_warns_and_falls_back(monkeypatch, caplog):
    _fc, _ = fake_fc({}, {})
    monkeypatch.setattr(fonts, "_fc", _fc)
    with caplog.at_level(logging.WARNING):
        assert fonts.caption_fonts_dir("Montserrat ExtraBold") is None
    assert "not installed" in caplog.text and "DejaVu Sans" in caplog.text


def test_fontsdir_option_escapes_filter_syntax(monkeypatch, tmp_path):
    odd = tmp_path / "it's: here"
    monkeypatch.setattr(fonts, "caption_fonts_dir", lambda family: odd)
    opt = fonts.fontsdir_option("Arial")
    assert opt.startswith(":fontsdir='") and r"\:" in opt and r"'\''" in opt


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("fc-scan")), reason="needs ffmpeg + fontconfig")
@pytest.mark.parametrize("style", ["karaoke", "simple"])
def test_render_with_local_fonts_dir(tmp_path, segs, monkeypatch, style):
    """Real ffmpeg: the fontsdir= option must be valid filter syntax for both caption styles."""
    from app.models import Clip
    from app.pipeline.render import render_clip

    from .test_render import _probe, _video

    src = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not src.exists():
        pytest.skip("no DejaVu font to copy")
    local = (settings.data_dir / "fonts").resolve()
    local.mkdir(parents=True)
    shutil.copy(src, local)
    monkeypatch.setattr(settings, "caption_font", "DejaVu Sans")
    monkeypatch.setattr(settings, "caption_style", style)
    assert fonts.caption_fonts_dir("DejaVu Sans") == local

    clip = Clip(index=1, start=8.0, end=20.0, title="t", hook="h", score=7)
    out = tmp_path / render_clip(_video(tmp_path, 22), clip, segs, tmp_path)
    assert _probe(out, "stream=width,height").splitlines()[0] == "1080,1920"
