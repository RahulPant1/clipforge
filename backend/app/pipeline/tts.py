"""Your voice (Qwen3-TTS voice cloning, no training): recordings of you → a voice profile → any text spoken in it.

Qwen3-TTS lives in its own env (TTS_PYTHON, default ~/miniconda3/envs/clipforge-tts) and only runs when asked:
tts/qwen_worker.py is started for one build / speak and exits, so the GPU is free again. Callers hold GPU_LOCK and
unload Gemma first (8 GB VRAM).

data/voices/<id>/
  voice.json              name, recordings (file, text, seconds, quality, style, use), built info
  recordings/rec_N.wav    24 kHz mono, loudness-normalised, silence trimmed; long takes split at sentence ends
  prompt.pt               the built voice: averaged speaker embedding of every used recording + the best clip's
                          audio codes and words (its delivery) — made by `build`
  samples/*.wav           what you asked it to say

How several recordings help (no fine-tuning): every clip gives a speaker embedding; their average is steadier than
any single clip. The best-scored clip (clear, 5-15 s, Whisper-confident) is the delivery reference. A clip whose
embedding is far from the rest (noise, another voice) is flagged and left out of the average.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import numpy as np

from ..config import settings

log = logging.getLogger(__name__)

WORKER = Path(__file__).resolve().parents[2] / "tts" / "qwen_worker.py"
SR = 24000
MIN_SECONDS, MAX_SECONDS = 3.0, 20.0  # one reference clip; longer takes are split
SPLIT_TARGET = 12.0  # s per piece when a long take is split at sentence ends
BEST_RANGE = (5.0, 15.0)  # the delivery reference works best in this range
MIN_SIMILARITY = 0.75  # cosine to the voice's average embedding: below = probably not (only) you
MAX_RECORDINGS = 40
MAX_UPLOAD = 200 * 2**20  # bytes per uploaded file
STYLES = ("", "calm", "energetic", "hinglish", "storytelling", "serious")
_ID = re.compile(r"^[a-z0-9_-]{1,40}$")


# ------------------------------------------------------------------ environment

def python() -> Path:
    if settings.tts_python:
        return Path(settings.tts_python).expanduser()
    return Path.home() / "miniconda3" / "envs" / "clipforge-tts" / "bin" / "python"


def status() -> dict:
    """Installed? (cheap: no model load). The model downloads on first use (~4 GB for 1.7B)."""
    py = python()
    if not py.exists():
        return {"installed": False, "model": settings.tts_model,
                "reason": f"no TTS env at {py} — see README (Your voice)"}
    res = run_worker({"cmd": "check"}, timeout=120)
    return {"installed": bool(res.get("ok") and res.get("cuda_ok")), "model": settings.tts_model,
            "reason": res.get("error") or ("" if res.get("cuda_ok") else "the TTS env can't use the GPU"),
            **{k: res.get(k) for k in ("torch", "cuda", "gpu")}}


def run_worker(job: dict, timeout: float | None = None) -> dict:
    """One worker run (its own process: the model's VRAM is gone when it exits)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "job.json"
        f.write_text(json.dumps(job), encoding="utf-8")
        env = {**os.environ, "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "PYTHONUNBUFFERED": "1"}
        env.pop("LD_LIBRARY_PATH", None)  # ClipForge's CUDA 12 wheels for Whisper must not leak into the TTS env
        try:
            p = subprocess.run([str(python()), str(WORKER), str(f)], capture_output=True, text=True, env=env,
                               timeout=timeout or settings.tts_timeout)
        except FileNotFoundError:
            return {"ok": False, "error": f"TTS env not found at {python()}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"TTS worker took longer than {timeout or settings.tts_timeout} s"}
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        tail = p.stderr.strip().splitlines()[-3:]
        return {"ok": False, "error": f"TTS worker exited with {p.returncode}: {' | '.join(tail)}"[:500]}
    if p.stderr:
        log.debug("tts worker: %s", p.stderr[-2000:])
    return json.loads(lines[-1])


# ------------------------------------------------------------------ profiles

def root() -> Path:
    return settings.data_dir / "voices"


def voice_dir(vid: str) -> Path:
    if not _ID.match(vid or ""):
        raise ValueError("bad voice id")
    return root() / vid


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "voice"
    return s if not (root() / s).exists() else f"{s}-{uuid.uuid4().hex[:4]}"


def load(vid: str) -> dict:
    f = voice_dir(vid) / "voice.json"
    if not f.is_file():
        raise KeyError(vid)
    return json.loads(f.read_text(encoding="utf-8"))


def save(v: dict) -> dict:
    d = voice_dir(v["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "voice.json.tmp"
    tmp.write_text(json.dumps(v, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "voice.json")
    return v


def all_voices() -> list[dict]:
    if not root().is_dir():
        return []
    out = []
    for f in sorted(root().glob("*/voice.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def create(name: str) -> dict:
    name = " ".join(name.split())[:60] or "My voice"
    return save({"id": slug(name), "name": name, "created": time.time(), "language": "Auto", "recordings": [],
                 "built": None})


def delete(vid: str) -> None:
    shutil.rmtree(voice_dir(vid), ignore_errors=True)


# ------------------------------------------------------------------ recordings

def to_wav(src: Path, dst: Path) -> None:
    """Any audio/video → 24 kHz mono, loudness -20 LUFS, leading/trailing silence trimmed."""
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15"
    af = f"{trim},areverse,{trim},areverse,loudnorm=I=-20:TP=-2:LRA=11,aresample={SR}"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-af", af, "-ar", str(SR),
                        "-sample_fmt", "s16", str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise ValueError(f"can't read that audio: {r.stderr.strip()[-200:]}")


def read_pcm(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768


def quality(pcm: np.ndarray) -> dict:
    """Seconds, signal-to-noise (loud vs quiet 50 ms frames) and clipping — what makes a clean reference."""
    secs = len(pcm) / SR
    hop = int(SR * 0.05)
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop) if len(pcm) >= hop else pcm.reshape(1, -1)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    snr = float(np.percentile(rms, 90) - np.percentile(rms, 10)) if len(rms) > 4 else 0.0
    clip = float((np.abs(pcm) > 0.99).mean()) if len(pcm) else 0.0
    return {"seconds": round(secs, 2), "snr_db": round(snr, 1), "clipping": round(clip, 5)}


def score(rec: dict) -> float:
    """0-100: clean (SNR), confident words, a usable length, no clipping."""
    s = min(rec.get("snr_db", 0) / 40, 1) * 50
    s += max(min((rec.get("logprob", -1.0) + 1.0) / 0.8, 1), 0) * 30  # Whisper avg logprob -1 → 0, -0.2 → 1
    lo, hi = BEST_RANGE
    secs = rec.get("seconds", 0)
    s += 20 if lo <= secs <= hi else 10 if MIN_SECONDS <= secs <= MAX_SECONDS else 0
    return round(s - (30 if rec.get("clipping", 0) > 0.001 else 0), 1)


def transcribe_many(paths: list[Path]) -> list[dict]:
    """Whisper once for all the new recordings: words (with times, for splitting) + confidence."""
    if not paths:
        return []
    if settings.whisper_device == "cuda":
        from .transcribe import preload_cuda_wheels

        preload_cuda_wheels()
    import gc

    from faster_whisper import WhisperModel

    model = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                         compute_type=settings.whisper_compute_type)
    out = []
    try:
        for p in paths:
            segs, info = model.transcribe(str(p), word_timestamps=True, vad_filter=False,
                                          language=settings.whisper_language)
            segs = list(segs)
            words = [{"start": w.start, "end": w.end, "word": w.word} for s in segs for w in (s.words or [])]
            lp = float(np.mean([s.avg_logprob for s in segs])) if segs else -2.0
            out.append({"text": " ".join(s.text.strip() for s in segs).strip(), "words": words, "logprob": lp,
                        "language": getattr(info, "language", None)})
    finally:
        del model
        gc.collect()
    return out


def split_points(words: list[dict], total: float) -> list[tuple[float, float]]:
    """A long take → pieces of ~SPLIT_TARGET s cut after a sentence end (else any word end), never mid-word."""
    if total <= MAX_SECONDS or not words:
        return [(0.0, total)]
    pieces, start = [], 0.0
    ends = [(w["end"], w["word"].strip()[-1:] in ".?!…") for w in words]
    while total - start > MAX_SECONDS:
        window = [(t, sent) for t, sent in ends if start + MIN_SECONDS * 2 <= t <= start + MAX_SECONDS]
        if not window:
            break
        sentence = [t for t, sent in window if sent]
        cut = min(sentence or [t for t, _ in window], key=lambda t: abs(t - start - SPLIT_TARGET))
        pieces.append((start, cut))
        start = cut
    pieces.append((start, total))
    return [(a, b) for a, b in pieces if b - a >= MIN_SECONDS]


def add_recordings(vid: str, files: list[tuple[str, bytes]], style: str = "") -> dict:
    """Upload(s) → cleaned wavs (long ones split) → transcribed + scored. Needs the GPU (Whisper): hold GPU_LOCK."""
    v = load(vid)
    d = voice_dir(vid) / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    style = style if style in STYLES else ""
    with tempfile.TemporaryDirectory() as tmp:
        cleaned = []
        for i, (name, data) in enumerate(files):
            src = Path(tmp) / f"in{i}{Path(name).suffix[:8] or '.bin'}"
            src.write_bytes(data)
            dst = Path(tmp) / f"clean{i}.wav"
            to_wav(src, dst)
            cleaned.append(dst)
        heard = transcribe_many(cleaned)
        n = max((int(re.sub(r"\D", "", r["file"]) or 0) for r in v["recordings"]), default=0)
        for dst, h in zip(cleaned, heard, strict=True):
            pcm = read_pcm(dst)
            total = len(pcm) / SR
            for a, b in split_points(h["words"], total):
                if len(v["recordings"]) >= MAX_RECORDINGS:
                    break
                n += 1
                f = f"rec_{n}.wav"
                piece = pcm[int(a * SR):int(b * SR)]
                _write_wav(d / f, piece)
                text = " ".join(w["word"].strip() for w in h["words"] if a - 0.05 <= w["start"] < b - 0.05)
                rec = {"file": f, "text": text or h["text"], "style": style, "use": True, "logprob": h["logprob"],
                       "language": h["language"], **quality(piece)}
                rec["score"] = score(rec)
                v["recordings"].append(rec)
    v["built"] = None  # recordings changed: build again
    return save(v)


def _write_wav(path: Path, pcm: np.ndarray) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                        "-sample_fmt", "s16", str(path)], input=pcm.astype("<f4").tobytes(), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"couldn't write {path.name}")


def edit_recording(vid: str, file: str, changes: dict) -> dict:
    v = load(vid)
    rec = next((r for r in v["recordings"] if r["file"] == file), None)
    if rec is None:
        raise KeyError(file)
    if "text" in changes:
        rec["text"] = " ".join(str(changes["text"]).split())[:600]
    if "style" in changes and changes["style"] in STYLES:
        rec["style"] = changes["style"]
    if "use" in changes:
        rec["use"] = bool(changes["use"])
    v["built"] = None
    return save(v)


def remove_recording(vid: str, file: str) -> dict:
    v = load(vid)
    v["recordings"] = [r for r in v["recordings"] if r["file"] != file]
    (voice_dir(vid) / "recordings" / file).unlink(missing_ok=True)
    v["built"] = None
    return save(v)


# ------------------------------------------------------------------ build / speak

def pick_best(recs: list[dict], style: str = "") -> int:
    """Index (into recs) of the delivery reference: the best score, preferring the asked style."""
    def key(i: int):
        r = recs[i]
        return (bool(style) and r.get("style") == style, r.get("score", 0))
    return max(range(len(recs)), key=key)


def build(vid: str, style: str = "") -> dict:
    """The voice from every used recording (worker; GPU — hold GPU_LOCK). Flags clips that don't sound like you."""
    v = load(vid)
    recs = [r for r in v["recordings"] if r.get("use") and r.get("text")]
    if not recs:
        raise ValueError("add at least one recording (with its words) first")
    d = voice_dir(vid)
    best = pick_best(recs, style)
    t = time.time()
    res = run_worker({"cmd": "build", "model": settings.tts_model, "attn": settings.tts_attn,
                      "recordings": [{"audio": str(d / "recordings" / r["file"]), "text": r["text"]} for r in recs],
                      "best": best, "min_similarity": MIN_SIMILARITY if len(recs) >= 3 else 0.0,
                      "out": str(d / "prompt.pt")})
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "build failed")
    for r, sim in zip(recs, res["similarity"], strict=True):
        r["similarity"] = sim
        r["outlier"] = len(recs) >= 3 and sim < MIN_SIMILARITY
    v["built"] = {"at": time.time(), "model": settings.tts_model, "best": recs[best]["file"], "style": style,
                  "used": len(res["used"]), "seconds": round(time.time() - t, 1),
                  "peak_vram_mb": res.get("peak_vram_mb")}
    return save(v)


def speak(vid: str, texts: list[str], language: str | None = None, out_dir: Path | None = None,
          names: list[str] | None = None) -> list[dict]:
    """`texts` in the voice → wav files (worker; GPU — hold GPU_LOCK). Builds first if needed."""
    v = load(vid)
    d = voice_dir(vid)
    if not v.get("built") or not (d / "prompt.pt").exists():
        v = build(vid)
    out_dir = out_dir or d / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = names or [f"say_{int(time.time() * 1000)}_{i}.wav" for i in range(len(texts))]
    lang = language or v.get("language") or settings.tts_language
    res = run_worker({"cmd": "speak", "model": settings.tts_model, "attn": settings.tts_attn,
                      "prompt": str(d / "prompt.pt"),
                      "items": [{"text": t, "language": lang, "out": str(out_dir / n)} for t, n in zip(texts, names,
                                                                                                    strict=True)],
                      "gen": {"max_new_tokens": settings.tts_max_new_tokens}})
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "speak failed")
    return [{"file": Path(i["out"]).name, "seconds": i["seconds"], "took": i["took"]} for i in res["items"]]
