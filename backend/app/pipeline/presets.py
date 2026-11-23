"""Caption presets: the look of burned-in captions (font, colours, outline/box, case, words per line, animation).

"classic" is built from the CAPTION_* settings, so an existing .env keeps its look. The others are fixed looks;
add your own in data/caption_presets.json:

    {"mine": {"label": "Mine", "fonts": ["Bahnschrift", "DejaVu Sans"], "size": 84,
              "text": "&H00FFFFFF", "active": "&H0000A5FF", "keyword": "&H00FFFF00"}}

Missing fields take the classic values. Colours are ASS &HAABBGGRR (BGR order, AA 00 = opaque).
Each preset lists fonts in order of preference; the first one libass can actually render is used.
"""
import json
import logging
from dataclasses import asdict, dataclass, fields, replace

from ..config import settings
from .fonts import font_available

log = logging.getLogger(__name__)

# No "top": face tracking puts faces in the upper half (top captions sat on the eyes) and the hook card owns the top.
POSITIONS = ("bottom", "middle")


@dataclass(frozen=True)
class CaptionPreset:
    id: str
    label: str
    description: str = ""
    fonts: tuple[str, ...] = ("Arial",)
    size: int = 78
    bold: bool = True
    uppercase: bool = True
    words: int = 3  # max words per caption line
    text: str = "&H00FFFFFF"  # inactive words
    active: str = "&H0000FFFF"  # the word being said (karaoke)
    keyword: str = "&H0055FF55"  # Gemma keywords + numbers (karaoke)
    outline_color: str = "&H00000000"  # outline, or the box colour when box=True
    outline: float = 7  # outline width, or the box padding when box=True
    shadow: float = 3
    box: bool = False  # opaque box behind each line (ASS BorderStyle 3)
    blur: float = 0  # soft edges (\blur) — a glow when the outline is coloured
    active_scale: int = 110  # active word size in %
    pop: bool = True  # each line pops in (80% -> 100% over 90 ms)

    @property
    def font(self) -> str:
        """First font in `fonts` that libass can render; the first one (substituted, with a warning) otherwise."""
        return next((f for f in self.fonts if font_available(f)), self.fonts[0])

    def transform(self, word: str) -> str:
        return word.upper() if self.uppercase else word

    def public(self) -> dict:
        """For the UI: everything plus the font that will really be used."""
        return {**asdict(self), "fonts": list(self.fonts), "font": self.font}


def _classic() -> CaptionPreset:
    s = settings
    return CaptionPreset(id="classic", label="Classic", description="White, active word yellow, keywords green",
                         fonts=(s.caption_font,), size=s.caption_size, words=s.caption_words, text=s.caption_color,
                         active=s.highlight_color, keyword=s.keyword_color, active_scale=s.highlight_scale)


def _builtin() -> list[CaptionPreset]:
    c = _classic()
    return [
        c,
        replace(c, id="punch", label="Punch", description="Big condensed type, gold active word, cyan keywords",
                fonts=("Impact", "Anton", "Bebas Neue", "Arial Black", "DejaVu Sans"), size=96, bold=False, words=2,
                active="&H0000D7FF", keyword="&H00FFFF00", outline=9, shadow=0, active_scale=115),
        replace(c, id="clean", label="Clean", description="Sentence case, soft shadow, the spoken word lights up",
                fonts=("Segoe UI", "Calibri", "Ubuntu", "DejaVu Sans"), size=84, uppercase=False, words=4,
                text="&H50FFFFFF", active="&H00FFFFFF", keyword="&H00FFFFFF", outline=2, shadow=4, blur=1,
                active_scale=104, pop=False),
        replace(c, id="boxed", label="Boxed", description="Text on a dark box — readable on any background",
                fonts=("Arial Black", "Arial", "DejaVu Sans"), size=68, bold=False, words=3,
                outline_color="&H50000000", outline=16, shadow=0, box=True, active_scale=100),
        replace(c, id="neon", label="Neon", description="Magenta glow, cyan active word, yellow keywords",
                fonts=("Arial Black", "Arial", "DejaVu Sans"), size=80, bold=False, active="&H00FFFF00",
                keyword="&H0000FFFF", outline_color="&H00FF00FF", outline=5, shadow=0, blur=4),
    ]


def _custom() -> list[CaptionPreset]:
    path = settings.data_dir / "caption_presets.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("ignoring %s: %s", path, e)
        return []
    names = {f.name for f in fields(CaptionPreset)} - {"id"}
    out = []
    for pid, spec in (raw.items() if isinstance(raw, dict) else []):
        try:
            unknown = set(spec) - names
            if unknown:
                raise ValueError(f"unknown fields {sorted(unknown)}")
            if "fonts" in spec:
                spec = {**spec, "fonts": tuple(spec["fonts"]) if isinstance(spec["fonts"], list) else (spec["fonts"],)}
            out.append(replace(_classic(), **{"label": str(pid), "description": "", **spec, "id": str(pid)}))
        except (TypeError, ValueError, AttributeError) as e:  # one bad preset mustn't hide the others
            log.warning("caption preset %r in %s skipped: %s", pid, path, e)
    return out


def all_presets() -> dict[str, CaptionPreset]:
    presets = {p.id: p for p in _builtin()}
    presets.update({p.id: p for p in _custom()})  # a custom preset may also replace a built-in one
    return presets


def get_preset(pid: str | None = None) -> CaptionPreset:
    presets = all_presets()
    pid = pid or settings.caption_preset
    if pid not in presets:
        log.warning("unknown caption preset %r, using classic", pid)
    return presets.get(pid) or presets["classic"]


def placement(position: str | None, height: int = 1920) -> tuple[int, int]:
    """(ASS Alignment, MarginV) for a caption position on a 1080-wide canvas `height` tall (1920 = 9:16).

    Bottom margin scales with the height: 4:5 / 1:1 feed posts have less app UI over the bottom than Shorts."""
    position = position or settings.caption_position
    if position == "middle":  # centre of the canvas: under the chin in a tracked talking head
        return 5, 0
    return 2, round(settings.caption_margin_v * height / 1920)
