"""Brand kit: logo watermark, end card, brand colour on every short."""
import io
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models import BrandKit, Clip, RenderOptions
from app.pipeline import brand

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _png(w=300, h=200, colour=(0, 200, 255, 255), margin=50) -> bytes:
    """A coloured block with transparent margins around it (as exported logos often are)."""
    from PIL import Image

    img = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (w, h), colour), (margin, margin))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_no_kit_means_shorts_look_as_before():
    kit = brand.load()
    assert not kit.enabled and brand.watermark(kit, RenderOptions()) is None
    assert brand.end_card_seconds(kit, RenderOptions(), 30) == 0
    assert brand.accent(kit) == settings.carousel_accent and brand.handle(kit) == settings.brand_handle


def test_save_bumps_rev_only_for_what_is_burned_in():
    k = brand.save(BrandKit(end_text="  Follow  "))
    assert k.rev == 1 and k.end_text == "Follow" and not k.logo  # no file → logo can't be claimed
    assert brand.save(k).rev == 1  # nothing changed
    assert brand.save(k.model_copy(update={"accent": "#112233"})).rev == 2
    assert brand.accent() == "#112233" and brand.ass_colour("#112233") == "&H00332211"  # BGR
    assert brand.save(BrandKit(enabled=False, accent="#112233", end_text="Follow")).rev == 3
    assert brand.accent() == settings.carousel_accent  # a switched-off kit gives the .env defaults back


def test_logo_upload_trims_transparent_margins_and_turns_the_kit_on():
    k = brand.save_logo(_png(300, 200))
    assert k.logo and k.enabled
    from PIL import Image

    assert Image.open(brand.logo_path()).size == (300, 200)
    with pytest.raises(ValueError):
        brand.save_logo(b"not an image")
    assert not brand.remove_logo().logo and not brand.logo_path().exists()


def test_end_card_never_pushes_a_short_past_59_seconds():
    kit = BrandKit(end_seconds=3, end_text="Follow")
    assert brand.end_card_seconds(kit, RenderOptions(), 40) == 3
    assert brand.end_card_seconds(kit, RenderOptions(), 57.5) == 1.5
    assert brand.end_card_seconds(kit, RenderOptions(), 58.5) == 0  # < 1 s: skipped
    assert brand.end_card_seconds(kit, RenderOptions(end_card=False), 40) == 0  # per-short off
    assert brand.end_card_seconds(kit.model_copy(update={"end_card": False}), RenderOptions(end_card=True), 40) == 3
    assert brand.end_card_seconds(BrandKit(end_text=""), RenderOptions(), 40) == 0  # nothing to show


def test_overlay_graph_hides_the_logo_under_the_hook_and_on_the_end_card():
    g = brand.overlay_graph("b", logo_input=1, kit=BrandKit(position="top-right", size=20), card_input=2,
                            start=3.0, end=20.0, card_from=20.0)
    assert "[1:v]scale=216:-1" in g and "overlay=W-w-48:120:enable='between(t,3.000,20.000)'" in g
    assert "overlay=0:0:enable='gte(t,20.000)'" in g and g.endswith("null[v]")


def test_api_brand_kit(tmp_path):
    from app.main import create_app

    app = create_app(pipeline=lambda *a, **k: [], start_worker=False)
    with TestClient(app) as c:
        k = c.get("/api/brand-kit").json()
        assert not k["enabled"] and k["accent_now"] == settings.carousel_accent
        k = c.post("/api/brand-kit/logo", files={"file": ("logo.png", _png(), "image/png")}).json()
        assert k["logo"] and k["enabled"]
        assert c.get("/api/brand-kit/logo.png").status_code == 200
        assert c.post("/api/brand-kit/logo", files={"file": ("x.png", b"nope", "image/png")}).status_code == 400
        body = {**{f: k[f] for f in BrandKit.model_fields}, "accent": "#00aa88", "handle": "@me"}
        k = c.put("/api/brand-kit", json=body).json()
        assert k["accent_now"] == "#00aa88" and k["handle_now"] == "@me"
        assert c.put("/api/brand-kit", json={**body, "accent": "red"}).status_code == 422
        assert c.put("/api/brand-kit", json={**body, "position": "bottom-right"}).status_code == 422
        r = c.get("/api/brand-kit/end-card.png")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
        assert not c.delete("/api/brand-kit/logo").json()["logo"]


@needs_ffmpeg
def test_render_burns_in_the_logo_and_appends_the_end_card(tmp_path, segs):
    import numpy as np

    from app.pipeline.render import render_clip

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x404040:s=1080x1920:r=25:d=4",
                    "-f", "lavfi", "-i", "sine=f=300:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    brand.save_logo(_png(300, 300, colour=(255, 0, 0, 255)))
    kit = brand.save(brand.load().model_copy(update={"position": "top-left", "size": 20, "opacity": 1.0,
                                                     "end_seconds": 1.5, "end_text": "Follow for more"}))
    clip = Clip(index=1, start=0.0, end=4.0, title="t", hook="A hook", score=7,
                options=RenderOptions(jumpcut=False, caption_style="none", reframe="center"))
    render_clip(src, clip, segs, tmp_path)

    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                                       "-of", "json", str(tmp_path / "short_1.mp4")],
                                      capture_output=True, text=True).stdout)["streams"]
    durs = {s["codec_type"]: float(s["duration"]) for s in probe}
    assert abs(durs["video"] - 5.5) < 0.15 and abs(durs["audio"] - 5.5) < 0.15  # 4 s + 1.5 s card, in sync
    assert clip.out_duration == 5.5 and clip.brand_rev == kit.rev

    def frame(t):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(tmp_path / "short_1.mp4"),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    logo = lambda f: f[140:300, 60:250].mean(axis=(0, 1))  # noqa: E731 — where a 216 px logo sits top-left
    under_hook, shown, card = frame(1.0), frame(3.5), frame(5.0)
    assert logo(shown)[0] > 200 and logo(shown)[1] < 60  # red logo after the hook card
    assert logo(under_hook)[0] < 120  # not under the hook card
    assert card[600:700, 440:640, 0].mean() > 200 and logo(card)[0] < 120  # end card: big logo centred, no corner one
    assert (tmp_path / "short_1.end.png").exists()

    clip.options = RenderOptions(jumpcut=False, caption_style="none", reframe="center", watermark=False,
                                 end_card=False)
    render_clip(src, clip, segs, tmp_path)
    assert clip.out_duration == 4.0 and logo(frame(3.5))[0] < 120
