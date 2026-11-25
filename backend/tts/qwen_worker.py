"""Qwen3-TTS worker — runs in its own env (clipforge-tts, Python 3.12), started by app/pipeline/tts.py only when a
voice is built or speaks, then exits so the GPU is free again. Never imports ClipForge (different env).

    <tts python> qwen_worker.py job.json      → prints one JSON line (the result) on stdout; logs go to stderr

Jobs:
  {"cmd": "build", "model": ..., "recordings": [{"audio": wav, "text": transcript}, ...], "best": i, "out": prompt.pt,
   "sample": {"text", "language", "out"}}   (sample optional: a test line in the new voice, same model load)
      One clone prompt per recording → their speaker embeddings averaged (your voice, steadier than any one clip)
      → saved with the best clip's audio codes + transcript (its delivery: pace, tone). Returns each clip's cosine
      similarity to the average (a low one = noise / another voice in that recording).
  {"cmd": "speak", "model": ..., "prompt": prompt.pt, "items": [{"text", "language", "out", "prompt"?}], "gen": {...}}
      (an item's own "prompt" = another voice version — A/B comparisons in one model load)
      Every line in the voice; one model load for all of them. Writes 24 kHz mono wavs.
  {"cmd": "check"}  → torch / CUDA / package versions (no model load)
"""
import json
import sys
import time
from pathlib import Path


def log(*a) -> None:
    print(*a, file=sys.stderr, flush=True)


def load(model_id: str, attn: str):
    import torch
    from qwen_tts import Qwen3TTSModel

    t = time.time()
    model = Qwen3TTSModel.from_pretrained(model_id, device_map="cuda:0", dtype=torch.bfloat16, attn_implementation=attn)
    log(f"model loaded in {time.time() - t:.1f}s")
    return model


def build(job: dict) -> dict:
    import torch
    from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem

    model = load(job["model"], job.get("attn", "sdpa"))
    recs = job["recordings"]
    items = model.create_voice_clone_prompt(ref_audio=[r["audio"] for r in recs], ref_text=[r["text"] for r in recs],
                                            x_vector_only_mode=False)
    embs = torch.stack([it.ref_spk_embedding.float() for it in items])
    unit = torch.nn.functional.normalize(embs, dim=-1)
    n = len(items)
    # each take against the average of the OTHER takes (with itself in the mean an odd one hides); speaker
    # embeddings sit close together (two different voices measured 0.945), so the rule is relative: a take more
    # than outlier_gap below the median of the profile is someone/something else (a foreign take: 0.970 vs 0.99)
    sims = []
    for i in range(n):
        others = unit[[k for k in range(n) if k != i]] if n > 1 else unit
        sims.append(float(unit[i] @ torch.nn.functional.normalize(others.mean(0), dim=0)))
    med = sorted(sims)[n // 2]
    gap = job.get("outlier_gap")
    keep = [i for i, s in enumerate(sims) if gap is None or n < 3 or s >= med - gap] or list(range(n))
    avg = embs[keep].mean(0)  # mean of the raw vectors keeps their typical norm
    best = items[job.get("best", 0)]
    voice = VoiceClonePromptItem(ref_code=best.ref_code, ref_spk_embedding=avg.to(best.ref_spk_embedding.dtype),
                                 x_vector_only_mode=False, icl_mode=True, ref_text=best.ref_text)
    torch.save({"version": 1, "model": job["model"], "item": voice.__dict__, "used": keep}, job["out"])
    sample = say(model, voice, job["sample"], job.get("gen", {})) if job.get("sample") else None
    return {"similarity": [round(s, 4) for s in sims], "median": round(med, 4), "used": keep, "sample": sample,
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20)}


def say(model, item, it: dict, gen: dict) -> dict:
    import soundfile as sf

    t = time.time()
    wavs, sr = model.generate_voice_clone(text=it["text"], language=it.get("language") or "Auto",
                                          voice_clone_prompt=[item], **gen)
    sf.write(it["out"], wavs[0], sr)
    res = {"out": it["out"], "seconds": round(len(wavs[0]) / sr, 2), "took": round(time.time() - t, 2), "sr": sr}
    log(f"{it['out']}: {res['seconds']} s of speech in {res['took']} s")
    return res


def speak(job: dict) -> dict:
    import torch
    from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem

    prompts: dict[str, object] = {}

    def voice(path: str):
        if path not in prompts:
            saved = torch.load(path, map_location="cpu", weights_only=False)
            prompts[path] = VoiceClonePromptItem(**saved["item"])
        return prompts[path]

    model = load(job["model"], job.get("attn", "sdpa"))
    out = []
    for it in job["items"]:  # one at a time: 8 GB
        try:
            result = say(model, voice(it.get("prompt") or job["prompt"]), it, job.get("gen", {}))
        except Exception as e:
            if not job.get("continue_on_error"):
                raise
            result = {"out": it["out"], "error": f"{type(e).__name__}: {e}"[:300]}
        out.append(result)
        if job.get("progress"):
            with Path(job["progress"]).open("ab") as progress:
                progress.write(b".")
    return {"items": out, "sample_rate": next((item["sr"] for item in reversed(out) if "sr" in item), None),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20)}


def check(job: dict) -> dict:
    import qwen_tts  # noqa: F401  (import check only)
    import torch

    ok = torch.cuda.is_available()
    return {"torch": torch.__version__, "cuda": torch.version.cuda, "cuda_ok": ok,
            "gpu": torch.cuda.get_device_name(0) if ok else None}


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        res = {"ok": True, **{"build": build, "speak": speak, "check": check}[job["cmd"]](job)}
    except Exception as e:  # the caller shows this; the traceback is on stderr
        import traceback

        traceback.print_exc()
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
    print(json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
