"""Title + hook variants for one clip: the hook (first 3 s) decides whether people keep watching, so you get
several different takes to choose from instead of the single one picked during selection.

On demand per clip (Pick step "✨ More hooks"), not during selection: it only costs Gemma time for clips you
actually care about.
"""
import logging

from ..config import settings
from ..models import Clip, HookVariant
from .llm import LLM

log = logging.getLogger(__name__)

# style id -> what it means; the model must use each style at most once, so the variants really differ
STYLES = {
    "question": "a question the viewer wants answered",
    "bold_claim": "a bold, confident statement",
    "number": "a specific number or result from the clip",
    "curiosity": "a curiosity gap: hint at the payoff without giving it away",
    "pov": 'a "POV:" or "When you..." line the viewer sees themselves in',
    "contrarian": "goes against what most people believe",
    "mistake": "a mistake or warning (\"Stop doing X\")",
}
COUNT = 5

PROMPT = """You write hooks for YouTube Shorts.

Video: "{video_title}"
What is said in this short:
\"\"\"{text}\"\"\"

Current title: {title}
Current hook: {hook}

Write {n} NEW title + hook pairs, each in a DIFFERENT style from this list (use each style at most once):
{styles}

Rules:
- hook: on-screen text for the first 3 seconds, at most 8 words. Make it stop the scroll.
- title: under 60 characters, works as the YouTube Shorts title.
- Only use facts, numbers and claims that are in the text above. Never invent results, names or numbers.
- Skip a style the text gives you nothing for (no number in the text → no "number" hook).
- Don't repeat the current title or hook.
- Same language as the text."""

SCHEMA = {
    "type": "object",
    "properties": {"variants": {"type": "array", "items": {
        "type": "object",
        "properties": {"style": {"type": "string", "enum": list(STYLES)}, "title": {"type": "string"},
                       "hook": {"type": "string"}},
        "required": ["style", "title", "hook"]}}},
    "required": ["variants"],
}


def build_prompt(clip: Clip, text: str, video_title: str = "", n: int = COUNT) -> str:
    return PROMPT.format(video_title=video_title or "untitled", text=text[:2500], title=clip.title, hook=clip.hook,
                         n=n, styles="\n".join(f"- {k}: {v}" for k, v in STYLES.items()))


def _clean(s, limit: int) -> str:
    return " ".join(str(s or "").replace("*", "").split()).strip().strip('"“”').strip()[:limit]  # no markdown


def write_variants(clip: Clip, text: str, llm: LLM, video_title: str = "", n: int = COUNT) -> list[HookVariant]:
    """Up to n distinct variants (different from each other and from the clip's current title/hook).

    Raises if the model returns nothing usable — the caller reports it; the clip is unchanged.
    """
    raw = llm.chat_json(build_prompt(clip, text, video_title, n), SCHEMA, think=settings.content_think)
    items = raw.get("variants", []) if isinstance(raw, dict) else []
    seen = {clip.hook.casefold(), clip.title.casefold()}
    out: list[HookVariant] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if len(str(item.get("hook") or "").split()) > 12:  # a sentence, not a hook (counted before trimming)
            continue
        title, hook = _clean(item.get("title"), 80), _clean(item.get("hook"), 60)
        if not title or not hook or hook.casefold() in seen:
            continue
        style = item.get("style") if item.get("style") in STYLES else "other"
        seen.add(hook.casefold())
        out.append(HookVariant(style=style, title=title, hook=hook))
        if len(out) == n:
            break
    if not out:
        raise ValueError("Gemma returned no usable hooks")
    return out
