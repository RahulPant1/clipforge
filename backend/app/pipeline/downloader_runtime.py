"""Staged yt-dlp upgrades and rollback without mutating the running Python environment."""
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import urlsplit

from ..config import settings
from ..files import atomic_output

_lock = Lock()
_ID = re.compile(r"[0-9a-f]{16}")
COOLDOWN = 600


def _root() -> Path:
    return settings.data_dir / "downloader"


def is_youtube(url: str) -> bool:
    return urlsplit(url).hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _state() -> dict:
    path = _root() / "active.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _save(state: dict) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with atomic_output(root / "active.json") as staged:
        staged.write_text(json.dumps(state), encoding="utf-8")


def active() -> Path | None:
    current = _state().get("active")
    if isinstance(current, str) and _ID.fullmatch(current):
        path = _root() / current
        if path.is_dir():
            return path
    return None


def _installed_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def status() -> dict:
    state = _state()
    installed = _installed_version("yt-dlp")
    installed_solver = _installed_version("yt-dlp-ejs")
    return {"version": state.get("version") or installed, "solver": state.get("solver") or installed_solver,
            "can_rollback": "previous" in state, "retry_after": retry_after()}


def retry_after() -> float:
    path = _root() / "cooldown"
    try:
        return float(path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return 0


def cooldown() -> float:
    until = max(retry_after(), time.time() + COOLDOWN)
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    with atomic_output(root / "cooldown") as staged:
        staged.write_text(str(until), encoding="ascii")
    return until


def run(directory: Path, arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(directory.resolve()), "PYTHONNOUSERSITE": "1"}
    return subprocess.run([sys.executable, "-P", "-m", "yt_dlp", "--ignore-config", *arguments],
                          env=env, cwd=directory, capture_output=True, text=True, timeout=timeout)


def cookies() -> list[str]:
    return ["--cookies", str(settings.ytdlp_cookies.resolve())] if settings.ytdlp_cookies else []


def update(url: str) -> dict:
    if not is_youtube(url):
        raise ValueError("Choose a failed YouTube job to verify the update")
    if retry_after() > time.time():
        raise ValueError("Wait for the YouTube cooldown before testing an update")
    if not _lock.acquire(blocking=False):
        raise ValueError("A downloader update is already running")
    try:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".update-", dir=root) as folder:
            staged = Path(folder)
            package = staged / "package"
            install = subprocess.run([
                sys.executable, "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--pre",
                "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--target", str(package.resolve()),
                "yt-dlp[default]>=2026.11.24.232653.dev0",
            ], capture_output=True, text=True, timeout=120)
            if install.returncode:
                raise ValueError("Could not fetch an updated downloader from PyPI")
            checked = run(package, [
                "--test", "--no-playlist", "--no-cache-dir", "--socket-timeout", "15", "--retries", "0",
                "--print", "after_move:%(id)s", "-f", format_selector(),
                "-o", str((staged / "check.%(ext)s").resolve()), *cookies(), "--", url,
            ], timeout=90)
            if checked.returncode or not any(file.stat().st_size for file in staged.glob("check.*")):
                if "http error 429" in checked.stderr.lower():
                    cooldown()
                raise ValueError("The updated downloader could not verify this URL. The active version was kept.")
            versions = {}
            for field, dist in (("version", "yt-dlp"), ("solver", "yt-dlp-ejs")):
                versions[field] = next(item.version for item in importlib.metadata.distributions(path=[package])
                                       if item.metadata["Name"].replace("_", "-").casefold() == dist)
            ident = uuid.uuid4().hex[:16]
            package.replace(root / ident)
            previous = _state()
            _save({"active": ident, **versions, "previous": {key: previous[key]
                    for key in ("active", "version", "solver") if key in previous}})
        return status()
    except (OSError, subprocess.TimeoutExpired, StopIteration) as error:
        raise ValueError("Downloader update could not complete. The active version was kept.") from error
    finally:
        _lock.release()


def rollback() -> dict:
    if not _lock.acquire(blocking=False):
        raise ValueError("A downloader update is already running")
    try:
        state = _state()
        if "previous" not in state:
            raise ValueError("No previous downloader is available")
        _save(state["previous"])
        return status()
    finally:
        _lock.release()


def format_selector() -> str:
    height = settings.max_height
    return f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/b[height<={height}][ext=mp4]/b"
