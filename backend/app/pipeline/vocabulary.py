"""Names and terms Whisper should know (your name, brands, jargon), for every new transcript.

Stored in data/vocabulary.json and passed to faster-whisper as `hotwords` — a prompt hint that makes the model
prefer these spellings. Words you fix with "replace all" can be added here so the next job gets them right.
"""
import json
import logging

from ..config import settings

log = logging.getLogger(__name__)

MAX_TERMS = 100
MAX_LEN = 40


def _path():
    return settings.data_dir / "vocabulary.json"


def clean(terms) -> list[str]:
    out, seen = [], set()
    for t in terms or []:
        t = " ".join(str(t).split())[:MAX_LEN]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_TERMS]


def load() -> list[str]:
    p = _path()
    if not p.exists():
        return []
    try:
        return clean(json.loads(p.read_text(encoding="utf-8")).get("terms", []))
    except (OSError, ValueError, AttributeError) as e:  # a broken file must not stop transcription
        log.warning("ignoring %s: %s", p, e)
        return []


def save(terms) -> list[str]:
    terms = clean(terms)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"terms": terms}, ensure_ascii=False, indent=2), encoding="utf-8")
    return terms


def add(term: str) -> list[str]:
    return save([*load(), term])


def hotwords() -> str | None:
    """Comma-separated terms for faster-whisper, or None when there are none."""
    terms = load()
    return ", ".join(terms) if terms else None
