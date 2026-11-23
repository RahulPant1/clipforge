"""Your voice (Qwen3-TTS voice cloning, no training): recordings of you → a voice profile → any text spoken in it.

Qwen3-TTS lives in its own env (TTS_PYTHON, default ~/miniconda3/envs/clipforge-tts) and only runs when asked:
tts/qwen_worker.py is started for one build / speak and exits, so the GPU is free again. Callers hold GPU_LOCK and
unload Gemma first (8 GB VRAM).

data/voices/<id>/
  voice.json              name, description, consent, created/updated, recordings (file, text, seconds, quality,
                          style, use), builds (one per version), dirty (recordings changed since the last build)
  recordings/rec_N.wav    24 kHz mono, -20 LUFS, silence trimmed; long takes split at sentence ends
  prompts/vN.pt           version N of the voice: averaged speaker embedding of the recordings used then + the best
                          one's audio codes and words (its delivery). A reference-based voice prompt — the model
                          itself is NOT trained or changed (see README: cloning vs fine-tuning)
  samples/vN.wav          the test sentence spoken right after building version N (same model load)
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
OUTLIER_GAP = 0.015  # a take this far below the profile's median similarity (to the other takes) is left out.
# Measured: takes of one voice 0.990-0.994, a different voice's take 0.970, two different voices 0.945 — the
# embeddings are close in absolute terms, so a fixed cut-off (0.75 before) never fired. Synthetic voices only:
# check it on real recordings (real takes vary more with mic and mood).
MAX_RECORDINGS = 40
MAX_UPLOAD = 200 * 2**20  # bytes per uploaded file
MAX_VERSIONS = 20  # older versions' prompts are deleted (their entries stay, marked gone)
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
    return migrate(json.loads(f.read_text(encoding="utf-8")))


def migrate(v: dict) -> dict:
    """Voices from before versions/descriptions: their one build becomes v1 (prompt.pt kept where it is)."""
    v.setdefault("description", "")
    v.setdefault("updated", v.get("created"))
    v.setdefault("dirty", v.get("built") is None and bool(v.get("recordings")))
    if "builds" not in v:
        legacy = v.get("built")
        old = root() / v["id"] / "prompt.pt" if _ID.match(v.get("id") or "") else None
        if legacy:
            v["builds"] = [{**legacy, "version": 1, "prompt": "prompt.pt", "sample": None}]
        elif old is not None and old.is_file():  # built, then a take was edited (old code cleared `built`): keep it
            v["builds"] = [{"version": 1, "at": old.stat().st_mtime, "model": None, "prompt": "prompt.pt",
                            "sample": None, "used": None, "best": None}]
            v["dirty"] = True
        else:
            v["builds"] = []
        v["built"] = v["builds"][-1] if v["builds"] else None
    return v


def save(v: dict) -> dict:
    v["updated"] = time.time()
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
            out.append(migrate(json.loads(f.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    return out


def create(name: str, description: str = "", consent: bool = False) -> dict:
    """consent: the person whose voice this is agreed to it being cloned (your own voice, or with permission)."""
    if not consent:
        raise ValueError("confirm this is your voice or you have permission to clone it")
    name = " ".join(name.split())[:60] or "My voice"
    now = time.time()
    return save({"id": slug(name), "name": name, "description": " ".join(description.split())[:300],
                 "consent_at": now, "created": now, "language": "Auto", "recordings": [], "builds": [],
                 "built": None, "dirty": False})


LANGUAGES = ("Auto", "English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese",
             "Spanish", "Italian")


def update_profile(vid: str, changes: dict) -> dict:
    """Rename, describe, or set the default language."""
    v = load(vid)
    if "name" in changes:
        v["name"] = " ".join(str(changes["name"]).split())[:60] or v["name"]
    if "description" in changes:
        v["description"] = " ".join(str(changes["description"]).split())[:300]
    if "language" in changes:
        if changes["language"] not in LANGUAGES:
            raise ValueError(f"language must be one of {', '.join(LANGUAGES)}")
        v["language"] = changes["language"]
    return save(v)


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
    v["dirty"] = True  # recordings changed since the last build: the next build makes a new version
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
    v["dirty"] = True
    return save(v)


def remove_recording(vid: str, file: str) -> dict:
    v = load(vid)
    v["recordings"] = [r for r in v["recordings"] if r["file"] != file]
    (voice_dir(vid) / "recordings" / file).unlink(missing_ok=True)
    v["dirty"] = True
    return save(v)


# ------------------------------------------------------------------ build / speak

def pick_best(recs: list[dict], style: str = "") -> int:
    """Index (into recs) of the delivery reference: the best score, preferring the asked style."""
    def key(i: int):
        r = recs[i]
        return (bool(style) and r.get("style") == style, r.get("score", 0))
    return max(range(len(recs)), key=key)


def build(vid: str, style: str = "", sample_text: str | None = None) -> dict:
    """A new version of the voice from every used recording (worker; GPU — hold GPU_LOCK) + its test sentence in
    the same model load. Flags recordings that don't sound like the rest. Earlier versions stay (compare them)."""
    v = load(vid)
    recs = [r for r in v["recordings"] if r.get("use") and r.get("text")]
    if not recs:
        raise ValueError("add at least one recording (with its words) first")
    d = voice_dir(vid)
    n = max((b["version"] for b in v["builds"]), default=0) + 1
    (d / "prompts").mkdir(parents=True, exist_ok=True)
    (d / "samples").mkdir(parents=True, exist_ok=True)
    best = pick_best(recs, style)
    text = (sample_text or settings.tts_sample_text).strip()[:300]
    lang = v.get("language") or settings.tts_language
    t = time.time()
    res = run_worker({"cmd": "build", "model": settings.tts_model, "attn": settings.tts_attn,
                      "recordings": [{"audio": str(d / "recordings" / r["file"]), "text": r["text"]} for r in recs],
                      "best": best, "outlier_gap": OUTLIER_GAP,
                      "out": str(d / "prompts" / f"v{n}.pt"),
                      "sample": {"text": text, "language": lang, "out": str(d / "samples" / f"v{n}.wav")},
                      "gen": {"max_new_tokens": settings.tts_max_new_tokens}})
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "build failed")
    used = set(res["used"])
    for i, (r, sim) in enumerate(zip(recs, res["similarity"], strict=True)):
        r["similarity"] = sim
        r["outlier"] = i not in used
    sample = res.get("sample") or {}
    entry = {"version": n, "at": time.time(), "model": settings.tts_model, "kind": "reference voice prompt",
             "prompt": f"prompts/v{n}.pt", "best": recs[best]["file"], "style": style,
             "used": len(res["used"]), "files": [recs[i]["file"] for i in res["used"]],
             "seconds": round(time.time() - t, 1), "peak_vram_mb": res.get("peak_vram_mb"),
             "sample": f"v{n}.wav" if sample else None, "sample_text": text,
             "sample_seconds": sample.get("seconds")}
    v["builds"].append(entry)
    for old in v["builds"][:-MAX_VERSIONS]:  # keep the newest MAX_VERSIONS prompts on disk
        if old.get("prompt") and old["prompt"] != "prompt.pt":
            (d / old["prompt"]).unlink(missing_ok=True)
        old["prompt"] = None
    v["built"], v["dirty"] = entry, False
    return save(v)


def prompt_for(v: dict, version: int | None = None) -> Path:
    """The prompt file of a version (None = the newest)."""
    builds = [b for b in v.get("builds", []) if b.get("prompt")]
    b = builds[-1] if version is None and builds else next((x for x in builds if x["version"] == version), None)
    if b is None or not (voice_dir(v["id"]) / b["prompt"]).is_file():
        raise KeyError(f"version {version}" if version else "no build yet")
    return voice_dir(v["id"]) / b["prompt"]


def speak(vid: str, texts: list[str], language: str | None = None, out_dir: Path | None = None,
          names: list[str] | None = None, version: int | None = None) -> list[dict]:
    """`texts` in the voice (a version, default the newest) → wav files (worker; GPU — hold GPU_LOCK).
    Builds first only when there is no version at all."""
    v = load(vid)
    if not any(b.get("prompt") for b in v["builds"]):
        v = build(vid)
    prompt = prompt_for(v, version)
    d = voice_dir(vid)
    out_dir = out_dir or d / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = names or [f"say_{int(time.time() * 1000)}_{i}.wav" for i in range(len(texts))]
    lang = language or v.get("language") or settings.tts_language
    res = run_worker({"cmd": "speak", "model": settings.tts_model, "attn": settings.tts_attn, "prompt": str(prompt),
                      "items": [{"text": t, "language": lang, "out": str(out_dir / n)} for t, n in zip(texts, names,
                                                                                                    strict=True)],
                      "gen": {"max_new_tokens": settings.tts_max_new_tokens}})
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "speak failed")
    return [{"file": Path(i["out"]).name, "seconds": i["seconds"], "took": i["took"]} for i in res["items"]]


def compare(vid: str, text: str, versions: list[int], language: str | None = None) -> list[dict]:
    """The same sentence in two (or more) versions, one model load → [{version, file, seconds}]."""
    v = load(vid)
    prompts = [prompt_for(v, n) for n in versions]  # KeyError for a missing version
    d = voice_dir(vid) / "samples"
    d.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    lang = language or v.get("language") or settings.tts_language
    items = [{"text": text, "language": lang, "prompt": str(p), "out": str(d / f"cmp_{stamp}_v{n}.wav")}
             for n, p in zip(versions, prompts, strict=True)]
    res = run_worker({"cmd": "speak", "model": settings.tts_model, "attn": settings.tts_attn,
                      "prompt": str(prompts[0]), "items": items,
                      "gen": {"max_new_tokens": settings.tts_max_new_tokens}})
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "compare failed")
    return [{"version": n, "file": Path(i["out"]).name, "seconds": i["seconds"]}
            for n, i in zip(versions, res["items"], strict=True)]
