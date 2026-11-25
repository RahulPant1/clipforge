"""Sound design: a soft whoosh on jump cuts, a pop on keywords, background music ducked under the voice.

All local. data/sounds/whoosh.wav and pop.wav are made once with numpy (replace them with your own — any
format FFmpeg reads, same names); music is whatever you put in data/sounds/music/ (mp3/wav/m4a/ogg/flac).

render.py asks for:
  events(plan, keywords)    → [(output time, "whoosh"|"pop")]: whooshes only on real cuts (gap ≥ WHOOSH_MIN_GAP,
                              ≥ WHOOSH_SPACING apart), pops on keyword words (≥ POP_SPACING apart, ≤ POP_MAX)
  write_track(events, dur, out) → one mono WAV with every effect placed (so the graph needs one extra input,
                              however many effects) — None when there's nothing to place
  audio_graph(...)          → filter_complex for audio: voice (cuts/end-card pad already applied) + music looped,
                              trimmed, faded and ducked (sidechaincompress keyed by the voice) + the effects track
"""
import logging
import subprocess
import wave
from pathlib import Path

import numpy as np

from ..config import settings
from .jumpcut import CutPlan, norm

log = logging.getLogger(__name__)

RATE = 48000
WHOOSH_MIN_GAP = 0.35  # s of removed audio before a cut gets a whoosh (tiny breath trims stay silent)
WHOOSH_SPACING = 2.5
POP_SPACING = 2.0
POP_MAX = 6
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


def sounds_dir() -> Path:
    return settings.data_dir / "sounds"


def music_dir() -> Path:
    return sounds_dir() / "music"


def music_tracks() -> list[str]:
    d = music_dir()
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in MUSIC_EXT)


def music_path(name: str | None) -> Path | None:
    """A track from the music folder by file name (no paths), or None."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = music_dir() / name
    return p if p.is_file() and p.suffix.lower() in MUSIC_EXT else None


# ------------------------------------------------------------------ starter sounds (numpy, no downloads)

def _whoosh() -> np.ndarray:
    """0.45 s of noise swept through a moving low-pass, swelling then fading — an air swish, not a hiss."""
    n = int(RATE * 0.45)
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n)
    t = np.linspace(0, 1, n)
    cutoff = 300 + 3500 * np.sin(np.pi * t) ** 2  # sweep up then down (Hz)
    a = np.exp(-2 * np.pi * cutoff / RATE)
    out, y = np.empty(n), 0.0
    for i in range(n):  # one-pole low-pass with a moving cutoff
        y = (1 - a[i]) * noise[i] + a[i] * y
        out[i] = y
    env = np.sin(np.pi * t) ** 1.5
    out *= env
    return 0.5 * out / (np.abs(out).max() or 1)


def _pop() -> np.ndarray:
    """60 ms falling sine blip (900 → 350 Hz) with a fast decay — a soft UI pop."""
    n = int(RATE * 0.06)
    t = np.arange(n) / RATE
    freq = 900 * (350 / 900) ** (t / t[-1])
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    out = np.sin(phase) * np.exp(-t * 55) * np.minimum(1, t * 2000)
    return 0.6 * out / np.abs(out).max()


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def ensure_starters() -> None:
    for name, make in (("whoosh", _whoosh), ("pop", _pop)):
        if not _sound_file(name):
            _write_wav(sounds_dir() / f"{name}.wav", make())


def _sound_file(name: str) -> Path | None:
    for ext in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
        p = sounds_dir() / f"{name}{ext}"
        if p.is_file():
            return p
    return None


def load_sound(name: str) -> np.ndarray:
    """Your file (any format FFmpeg reads) → mono float32 at RATE."""
    ensure_starters()
    p = _sound_file(name)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4")[: RATE * 3].astype(np.float64)  # cap at 3 s: these are accents


# ------------------------------------------------------------------ where the effects go

def events(plan: CutPlan, keywords: list[str]) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    t, last = 0.0, -99.0  # output time of each cut = running length of the keeps before it
    for i in range(len(plan.keeps) - 1):
        t += plan.keeps[i][1] - plan.keeps[i][0]
        gap = plan.keeps[i + 1][0] - plan.keeps[i][1]
        if gap >= WHOOSH_MIN_GAP and t - last >= WHOOSH_SPACING:
            out.append((round(t, 3), "whoosh"))
            last = t
    ks = {norm(p) for k in keywords for p in k.split() if len(norm(p)) > 2}
    last, pops = -99.0, 0
    for w in plan.words:
        if pops >= POP_MAX:
            break
        if norm(w.word) in ks and w.start - last >= POP_SPACING and w.start > 0.3:
            out.append((round(w.start, 3), "pop"))
            last, pops = w.start, pops + 1
    return sorted(out)


def write_track(evts: list[tuple[float, str]], duration: float, out: Path) -> Path | None:
    """Every effect on one mono track as long as the short. Whooshes peak right on the cut."""
    if not evts or duration <= 0:
        return None
    track = np.zeros(int(RATE * duration) + 1)
    gains = {"whoosh": 10 ** (settings.sfx_whoosh_db / 20), "pop": 10 ** (settings.sfx_pop_db / 20)}
    cache: dict[str, np.ndarray] = {}
    for t, kind in evts:
        s = cache.setdefault(kind, load_sound(kind))
        start = int(RATE * (t - (len(s) / RATE / 2 if kind == "whoosh" else 0)))  # whoosh centred on the cut
        a, b = max(start, 0), min(start + len(s), len(track))
        if b > a:
            track[a:b] += gains[kind] * s[a - start: b - start]
    _write_wav(out, track)
    return out


def _tempo(tempo: float) -> list[str]:
    filters = []
    while tempo < 0.5:
        filters.append("atempo=0.5")
        tempo *= 2
    while tempo > 2:
        filters.append("atempo=2")
        tempo /= 2
    if abs(tempo - 1) > 0.0001:
        filters.append(f"atempo={tempo:.8f}")
    return filters


def audio_graph(af: list[str], music_input: int | None, sfx_input: int | None, duration: float,
                voice_src: str = "0:a", ambience_input: int | None = None, stretch: float = 1.0,
                ambience_timing: list[tuple[float, float]] | None = None, ambience_level: float = 0.18) -> str:
    """filter_complex for audio → [aout]. af = the voice chain (cuts, crossfades, end-card pad) as for -af.
    voice_src: the speech stream — the source's audio, or a Replace-voice dub track input ("3:a")."""
    voice = ",".join(af) if af else "anull"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=stereo"  # sidechain needs equal formats
    parts = [f"[{voice_src}]{voice},{fmt}[voice]"]
    mix = ["[vo]"]
    if music_input is not None:
        fade = min(1.5, duration / 4)
        parts.append("[voice]asplit=2[vo][key]")
        parts.append(f"[{music_input}:a]{fmt},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
                     f"loudnorm=I={settings.music_lufs}:TP=-3:LRA=11,{fmt},afade=t=in:d=0.6,"
                     f"afade=t=out:st={max(duration - fade, 0):.3f}:d={fade:.3f}[mus]")
        parts.append("[mus][key]sidechaincompress=threshold=0.03:ratio=3:attack=30:release=450:makeup=1[duck]")
        mix.append("[duck]")
    else:
        parts.append("[voice]anull[vo]")
    if sfx_input is not None:
        parts.append(f"[{sfx_input}:a]{fmt}[fx]")
        mix.append("[fx]")
    if ambience_input is not None:
        source = f"[{ambience_input}:a]"
        gain = f"volume={ambience_level:.4f}"
        if ambience_timing:
            spans = list(zip(ambience_timing, ambience_timing[1:], strict=False))
            inputs = "".join(f"[room_in{i}]" for i in range(len(spans)))
            parts.append(f"{source}{fmt},{gain},asplit={len(spans)}{inputs}")
            for i, ((start, start_out), (end, end_out)) in enumerate(spans):
                length = end_out - start_out
                filters = [f"atrim=start={start:.6f}:end={end:.6f}", "asetpts=PTS-STARTPTS",
                           *_tempo((end - start) / length), "apad", f"atrim=duration={length:.6f}"]
                parts.append(f"[room_in{i}]{','.join(filters)}[room_part{i}]")
            outputs = "".join(f"[room_part{i}]" for i in range(len(spans)))
            parts.append(f"{outputs}concat=n={len(spans)}:v=0:a=1[room]")
        else:
            filters = [fmt, gain, *_tempo(1 / max(stretch, 1.0))]
            parts.append(f"{source}{','.join(filters)}[room]")
        mix.append("[room]")
    if len(mix) == 1:
        parts.append("[vo]anull[aout]")
    else:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[aout]")
    return ";".join(parts)

