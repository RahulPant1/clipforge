"""Brand voice: a few of your own best posts per platform become few-shot examples in the Content Studio prompt.

Stored once for all jobs in data/brand_voice.json. The examples set the VOICE (tone, rhythm, casing, emoji and
hashtag habits, how you open and close); the creative angle still sets the structure, and the facts still come
only from the clip — the prompt says so explicitly, since your old posts are about other topics.
"""
import logging

from ..config import settings
from ..models import BrandVoice

log = logging.getLogger(__name__)

MAX_EXAMPLES = 5  # per platform in a prompt (and kept per platform)
MAX_CHARS = 1500  # per example; longer ones are cut (keeps the prompt well inside num_ctx)
BORROW = 2  # platform with no examples of its own: borrow this many from your other platforms


def _path():
    return settings.data_dir / "brand_voice.json"


def clean(v: BrandVoice, platforms) -> BrandVoice:
    """Trim, drop empties/duplicates/unknown platforms, cap counts and lengths."""
    ex = {}
    for p in platforms:
        seen, keep = set(), []
        for t in v.examples.get(p, []):
            t = t.strip()[:MAX_CHARS].strip()
            if t and t not in seen:
                seen.add(t)
                keep.append(t)
        if keep:
            ex[p] = keep[:MAX_EXAMPLES]
    return BrandVoice(enabled=v.enabled, notes=v.notes.strip()[:1000], examples=ex)


def load() -> BrandVoice:
    p = _path()
    if not p.exists():
        return BrandVoice()
    try:
        return BrandVoice.model_validate_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # a broken file must not stop posts being written
        log.warning("ignoring %s: %s", p, e)
        return BrandVoice()


def save(v: BrandVoice) -> BrandVoice:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(v.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
    return v


def examples_for(platform: str, v: BrandVoice, names: dict[str, str]) -> list[tuple[str, str]]:
    """[(where it was posted, text)] — this platform's own examples, else a few borrowed from the others."""
    own = v.examples.get(platform, [])[:MAX_EXAMPLES]
    if own:
        return [(names.get(platform, platform), t) for t in own]
    borrowed = [(names.get(p, p), t) for p, ts in v.examples.items() if p != platform for t in ts]
    return borrowed[:BORROW]


def voice_block(platform: str, v: BrandVoice | None, names: dict[str, str]) -> str:
    """Prompt section with your posts ("" when off or empty)."""
    if not v or not v.enabled:
        return ""
    ex = examples_for(platform, v, names)
    if not ex:
        return ""
    target = names.get(platform, platform)
    borrowed = any(where != target for where, _ in ex)
    lines = [f"The creator's own voice — posts they wrote{' on other platforms' if borrowed else ''}:"]
    for i, (where, t) in enumerate(ex, 1):
        lines += [f"<example {i}{f' ({where})' if borrowed else ''}>", t, f"</example {i}>"]
    lines.append(
        "Write like this creator: match their tone, sentence length, punctuation, casing, emoji and hashtag habits,"
        " and how they open and close a post."
        + (f" Adapt it to {target}'s format." if borrowed else "")
        + " Those examples are about OTHER topics: never reuse their facts, numbers, names, stories or phrases —"
        " everything you say must come from this clip. The creative angle decides the structure; the examples"
        " only decide the voice."
    )
    return "\n".join(lines) + "\n"
