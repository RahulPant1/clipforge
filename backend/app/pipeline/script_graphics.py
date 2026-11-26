"""Render narration-timed explainer graphics with the same libass pipeline as captions."""
from dataclasses import replace

from ..models import ScriptGraphic
from . import brand, captions
from .fonts import font_for, strip_emoji
from .presets import get_preset

_X = {"left": 230, "center": 540, "right": 850}
_Y = {"top": 380, "middle": 890, "bottom": 1320}
_ARROW = "m -100 -12 l 35 -12 35 -45 105 0 35 45 35 12 -100 12"
_HIGHLIGHT = "m -150 -90 l 150 -90 150 90 -150 90"


def make_ass(graphics: list[ScriptGraphic], narration: dict) -> tuple[str, str]:
    kit = brand.load()
    accent = brand.ass_colour(kit.accent) if kit.enabled and kit.accent else "&H006D4DFF"
    colour = accent[4:]
    preset = replace(get_preset(), size=52, outline=4, shadow=2, box=False, uppercase=False)
    labels = " ".join(item.text for item in graphics if item.kind == "label")
    font = font_for(labels, preset.font)
    events = []
    for graphic in graphics:
        if graphic.line >= len(narration["lines"]):
            raise ValueError("A graphic starts at a narration line that no longer exists")
        start = narration["lines"][graphic.line]["start"]
        end = min(start + graphic.duration, narration["duration"])
        if end <= start:
            continue
        x, y = _X[graphic.horizontal], _Y[graphic.vertical]
        tags = rf"\an5\pos({x},{y})\fad(160,180)"
        if graphic.kind == "label":
            text = captions.hook_text(strip_emoji(graphic.text))
            if not text:
                raise ValueError("Enter text for each label")
            label_align = {"left": 4, "center": 5, "right": 6}[graphic.horizontal]
            tags = tags.replace(r"\an5", rf"\an{label_align}") + r"\q0\fs48"
        elif graphic.kind == "arrow":
            text = _ARROW
            rotation = {"right": 0, "up": 90, "left": 180, "down": 270}[graphic.direction]
            tags += rf"\bord2\shad0\1c&H{colour}&\fscx82\fscy82\t(0,200,\fscx100\fscy100)\frz{rotation}\p1"
        else:
            text = _HIGHLIGHT
            tags += rf"\1a&HFF&\3c&H{colour}&\bord8\shad0\p1"
        events.append(f"Dialogue: 0,{captions.ts_ass(start)},{captions.ts_ass(end)},Default,,0,0,0,,{{{tags}}}{text}")
    header = captions.build_ass([], [], preset=preset, font=font, animate=False).rstrip()
    return "\n".join([header, *events, ""]), font
