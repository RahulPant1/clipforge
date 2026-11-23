"""Qwen3-TTS worker — runs in its own env (clipforge-tts, Python 3.12), started by app/pipeline/tts.py only when a
voice is built or speaks, then exits so the GPU is free again. Never imports ClipForge (different env).

    <tts python> qwen_worker.py job.json      → prints one JSON line (the result) on stdout; logs go to stderr

Jobs:
  {"cmd": "build", "model": ..., "recordings": [{"audio": wav, "text": transcript}, ...], "best": i, "out": prompt.pt}
      One clone prompt per recording → their speaker embeddings averaged (your voice, steadier than any one clip)
      → saved with the best clip's audio codes + transcript (its delivery: pace, tone). Returns each clip's cosine
      similarity to the average (a low one = noise / another voice in that recording).
  {"cmd": "speak", "model": ..., "prompt": prompt.pt, "items": [{"text", "language", "out"}], "gen": {...}}
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
    mean_dir = torch.nn.functional.normalize(unit.mean(0), dim=0)
    sims = (unit @ mean_dir).tolist()
    keep = [i for i, s in enumerate(sims) if s >= job.get("min_similarity", 0.0)] or list(range(len(items)))
    avg = embs[keep].mean(0)  # mean of the raw vectors keeps their typical norm
    best = items[job.get("best", 0)]
    voice = VoiceClonePromptItem(ref_code=best.ref_code, ref_spk_embedding=avg.to(best.ref_spk_embedding.dtype),
                                 x_vector_only_mode=False, icl_mode=True, ref_text=best.ref_text)
    torch.save({"version": 1, "model": job["model"], "item": voice.__dict__, "used": keep}, job["out"])
    return {"similarity": [round(s, 4) for s in sims], "used": keep,
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20)}


def speak(job: dict) -> dict:
    import soundfile as sf
    import torch
    from qwen_tts.inference.qwen3_tts_model import VoiceClonePromptItem

    saved = torch.load(job["prompt"], map_location="cpu", weights_only=False)
    model = load(job.get("model") or saved["model"], job.get("attn", "sdpa"))
    item = VoiceClonePromptItem(**saved["item"])
    out = []
    for it in job["items"]:  # one at a time: long batches cost VRAM on 8 GB
        t = time.time()
        wavs, sr = model.generate_voice_clone(text=it["text"], language=it.get("language") or "Auto",
                                              voice_clone_prompt=[item], **job.get("gen", {}))
        sf.write(it["out"], wavs[0], sr)
        out.append({"out": it["out"], "seconds": round(len(wavs[0]) / sr, 2), "took": round(time.time() - t, 2)})
        log(f"{it['out']}: {out[-1]['seconds']} s of speech in {out[-1]['took']} s")
    return {"items": out, "sample_rate": sr, "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20)}


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
