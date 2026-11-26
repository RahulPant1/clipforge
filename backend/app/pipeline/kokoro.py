"""Local preset narration voices for Script → Short."""
import json
import subprocess
from pathlib import Path

from ..config import settings

_PREFIX = "kokoro:"
_PRESETS = {
    "af_heart": ("Heart · US English", "en-us"),
    "af_bella": ("Bella · US English", "en-us"),
    "am_michael": ("Michael · US English", "en-us"),
    "bf_emma": ("Emma · UK English", "en-gb"),
    "bm_george": ("George · UK English", "en-gb"),
}
_WORKER = Path(__file__).resolve().parents[2] / "tts" / "kokoro_worker.py"


def available() -> bool:
    directory = settings.kokoro_dir.expanduser()
    return all((directory / file).is_file() for file in (
        "venv/bin/python", "kokoro-v1.0.onnx", "voices-v1.0.bin",
    ))


def voices() -> list[dict]:
    return [{"id": _PREFIX + key, "name": name} for key, (name, _) in _PRESETS.items()] if available() else []


def is_preset(voice: str) -> bool:
    return voice.startswith(_PREFIX)


def fingerprint(voice: str) -> tuple[int, int]:
    if voice.removeprefix(_PREFIX) not in _PRESETS:
        raise ValueError("Choose an available Kokoro voice")
    if not available():
        raise ValueError("Kokoro is unavailable. Install the local model first.")
    directory = settings.kokoro_dir.expanduser()
    return tuple((directory / file).stat().st_mtime_ns for file in ("kokoro-v1.0.onnx", "voices-v1.0.bin"))


def speak(voice: str, lines: list[str], work: Path, names: list[str]) -> None:
    fingerprint(voice)
    key = voice.removeprefix(_PREFIX)
    directory = settings.kokoro_dir.expanduser().resolve()
    payload = {"voice": key, "language": _PRESETS[key][1],
               "items": [{"text": line, "path": str((work / name).resolve())}
                         for line, name in zip(lines, names, strict=True)]}
    try:
        result = subprocess.run([str(directory / "venv/bin/python"), str(_WORKER), str(directory)],
                                input=json.dumps(payload), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("The local Kokoro worker did not finish") from error
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError("Kokoro could not generate narration" + (f": {detail[-1][:220]}" if detail else ""))
