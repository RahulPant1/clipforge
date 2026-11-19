"""Variety engine memory: which creative angles and opening lines were used recently.

Persisted across jobs (data/content_history.json) so posts keep feeling new week after week:
- angles rotate least-recently-used first (every angle is used before any repeats)
- recent openers are fed back to Gemma as "don't start like this"
"""
import json
import random
import threading
from pathlib import Path

from ..config import settings


class ContentHistory:
    def __init__(self, path: Path, keep_openers: int = 15):
        self.path = path
        self.keep = keep_openers
        self._lock = threading.Lock()
        self.data: dict = {"seq": 0, "angles": {}, "openers": {}}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text()))
            except (json.JSONDecodeError, OSError):
                pass

    def pick_angle(self, platform: str, angles: list[str], exclude: set[str] | None = None) -> str:
        used = self.data["angles"].get(platform, {})
        cands = [a for a in angles if a not in (exclude or set())] or angles
        return min(cands, key=lambda a: (used.get(a, 0), random.random()))

    def recent_openers(self, platform: str, n: int = 8) -> list[str]:
        return self.data["openers"].get(platform, [])[-n:]

    def record(self, platform: str, angle: str, opener: str) -> None:
        with self._lock:
            self.data["seq"] += 1
            self.data["angles"].setdefault(platform, {})[angle] = self.data["seq"]
            ops = self.data["openers"].setdefault(platform, [])
            if opener:
                ops.append(opener[:80])
                del ops[:-self.keep]
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)


_cache: dict[Path, ContentHistory] = {}


def get_history() -> ContentHistory:
    path = settings.data_dir / "content_history.json"
    if path not in _cache:
        _cache[path] = ContentHistory(path)
    return _cache[path]
