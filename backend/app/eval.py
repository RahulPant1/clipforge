"""Prompt / model eval for clip selection — same transcript, several variants, one comparison report.

  python -m app.eval <job_id>                                   # current model + prompt (baseline)
  python -m app.eval <job_id> --model gemma4:e4b-it-q4_K_M --model gemma3:4b
  python -m app.eval <job_id> --prompt default --prompt my_prompt.txt --think on --think off
  python -m app.eval --print-prompt > my_prompt.txt             # start a variant from the built-in prompt

Every combination of --model × --prompt × --think runs (×--repeat). The first one is the baseline the
others are compared against. The job's approved/custom clips are "your picks": a variant scores
recall when its candidates overlap them (IoU ≥ 0.5). Those picks were chosen from the job's own
candidates, so they favour whatever model/prompt made the job — treat recall as a hint, not ground truth.

Writes data/evals/<timestamp>/report.md + results.json. Needs Ollama; don't run while a job is selecting
(the eval doesn't take the server's GPU lock — both would just run slower).
"""
import argparse
import itertools
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import settings
from .models import Segment
from .pipeline import scenes
from .pipeline.highlights import PROMPT, check_prompt, prompt_id, select_highlights
from .pipeline.llm import OllamaClient

THINK = {"default": None, "on": True, "off": False}  # "default" = don't send think (model decides)
MATCH_IOU = 0.5


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def matched(spans: list[tuple[float, float]], against: list[tuple[float, float]]) -> int:
    """How many of `spans` overlap some span in `against` with IoU ≥ MATCH_IOU."""
    return sum(any(iou(s, a) >= MATCH_IOU for a in against) for s in spans)


@dataclass
class Variant:
    model: str
    prompt_name: str
    prompt: str
    think: str  # key of THINK
    run: int = 1
    topic: str = ""
    scenes: str = "off"  # on = the job's scene index as ON SCREEN lines (scenes.json)
    seconds: float = 0.0
    error: str = ""
    trace: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        r = f" #{self.run}" if self.run > 1 else ""
        sc = " · scenes" if self.scenes == "on" else ""
        return f"{self.model} · {self.prompt_name} · think={self.think}{sc}{r}"


def summarize(v: Variant, picks: list[tuple[float, float]], baseline: list[tuple[float, float]] | None) -> dict:
    """Pure metrics from a variant's trace (tested without Ollama)."""
    items = v.trace.get("items", [])
    kinds = ("kept", "overlap", "ranked_out", "too_short", "off_topic", "invalid")
    outcomes = {k: sum(1 for i in items if i.get("outcome") == k) for k in kinds}
    usable = outcomes["kept"] + outcomes["overlap"] + outcomes["ranked_out"]
    clips = v.trace.get("clips", [])
    spans = [(c["start"], c["end"]) for c in clips]
    durs = [e - s for s, e in spans]
    wins = v.trace.get("windows", [])
    out = {
        "label": v.label, "error": v.error, "seconds": round(v.seconds, 1),
        "windows": len(wins), "windows_failed": sum(1 for w in wins if w.get("error")),
        "returned": len(items), **outcomes,
        "usable_rate": round(usable / len(items), 2) if items else 0.0,
        "candidates": len(clips),
        "median_s": round(statistics.median(durs), 1) if durs else 0.0,
        "min_s": round(min(durs), 1) if durs else 0.0, "max_s": round(max(durs), 1) if durs else 0.0,
        "mean_score": round(statistics.mean(c["score"] for c in clips), 1) if clips else 0.0,
    }
    if picks:
        out["picks_found"] = f"{matched(picks, spans)}/{len(picks)}"
    if baseline is not None:
        out["agree_baseline"] = round(matched(baseline, spans) / len(baseline), 2) if baseline else 0.0
    return out


COLUMNS = [("label", "variant"), ("seconds", "time s"), ("candidates", "cands"), ("returned", "returned"),
           ("usable_rate", "usable"), ("too_short", "too short"), ("off_topic", "off topic"), ("invalid", "invalid"),
           ("overlap", "overlap"),
           ("ranked_out", "ranked out"),
           ("median_s", "median s"), ("mean_score", "avg score"), ("picks_found", "your picks"),
           ("agree_baseline", "≈ baseline"), ("windows_failed", "failed chunks")]


def report_md(source: str, rows: list[dict], variants: list[Variant], n: int, keep: int) -> str:
    cols = [(k, h) for k, h in COLUMNS if any(k in r for r in rows)]
    lines = [f"# Clip selection eval — {source}", "",
             f"{datetime.now():%Y-%m-%d %H:%M} · {n} shorts, {keep} candidates asked · num_ctx "
             f"{settings.ollama_num_ctx} · clips {settings.min_clip_seconds:.0f}-{settings.max_clip_seconds:.0f} s"
             + (f" · topic “{variants[0].topic}”" if variants and variants[0].topic else ""),
             "", "| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(k, "")) for k, _ in cols) + " |")
    lines += ["", "- **usable**: share of the model's picks that passed validation (length, fields).",
              "- **your picks**: the job's approved/custom clips matched by a candidate (IoU ≥ 0.5). They were "
              "picked from the original run's candidates, so they favour that model/prompt.",
              "- **≈ baseline**: share of the first variant's candidates this one also found.", ""]
    for v in variants:
        lines.append(f"## {v.label}")
        if v.error:
            lines += ["", f"**Failed:** {v.error}", ""]
            continue
        lines.append(f"prompt `{prompt_id(v.prompt)}`\n")
        for c in sorted(v.trace.get("clips", []), key=lambda c: -c["score"]):
            mark = "✓" if c.get("approved") else " "
            rel = f" · on topic {c['relevance']}/10" if c.get("relevance") else ""
            lines.append(f"- {mark} `{_mmss(c['start'])}–{_mmss(c['end'])}` "
                         f"({c['end'] - c['start']:.0f} s) **{c['score']}/10**{rel} {c['title']} — _{c['hook']}_")
        lines.append("")
    return "\n".join(lines)


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02}"


def load_target(target: str) -> tuple[str, list[Segment], list[tuple[float, float]], int, str]:
    """job id | job dir | transcript.json → (name, segments, your picks, num_clips, topic)."""
    p = Path(target)
    if not p.exists():
        p = settings.data_dir / "jobs" / target
    job_dir = p.parent if p.is_file() else p
    transcript = p if p.is_file() else p / "transcript.json"
    if not transcript.exists():
        raise SystemExit(f"no transcript at {transcript} — pass a job id, job folder or transcript.json")
    segs = [Segment(**s) for s in json.loads(transcript.read_text())]
    picks, n, topic = [], 5, ""
    job_file = job_dir / "job.json"
    if job_file.exists():
        job = json.loads(job_file.read_text())
        picks = [(c["start"], c["end"]) for c in job.get("clips", []) if c.get("approved") or c.get("custom")]
        n = job.get("num_clips") or n
        topic = job.get("topic") or ""
    return job_dir.name, segs, picks, n, topic


def job_scenes(target: str) -> list | None:
    """The job's scene index lines for the prompt (None = no scenes.json with descriptions)."""
    p = Path(target)
    if not p.exists():
        p = settings.data_dir / "jobs" / target
    index = scenes.load(p.parent if p.is_file() else p)
    return index.for_prompt() if index and index.mode == "described" else None


def run_variant(v: Variant, segs: list[Segment], n: int, keep: int, scene_lines: list | None = None) -> None:
    llm = OllamaClient(model=v.model)
    t0 = time.monotonic()
    try:
        select_highlights(segs, n, llm, keep=keep, prompt=v.prompt, think=THINK[v.think], trace=v.trace,
                          topic=v.topic, scenes=scene_lines if v.scenes == "on" else None)
    except Exception as e:  # one broken variant (model not pulled, think unsupported…) mustn't stop the eval
        v.error = str(e)[:300]
    v.seconds = time.monotonic() - t0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Compare clip-selection prompts/models on one transcript")
    ap.add_argument("target", nargs="?", help="job id, job folder or transcript.json")
    ap.add_argument("--model", action="append", help=f"Ollama model (repeatable; default {settings.gemma_model})")
    ap.add_argument("--prompt", action="append", help="'default' or a prompt file (repeatable)")
    ap.add_argument("--think", action="append", choices=list(THINK), help="repeatable; default = SELECT_THINK")
    ap.add_argument("--clips", type=int, help="shorts wanted (default: the job's)")
    ap.add_argument("--candidates", type=int, help="candidates to keep (default 2× clips, like review mode)")
    ap.add_argument("--topic", help="steer selection to a topic (default: the job's; '' for none)")
    ap.add_argument("--scenes", action="append", choices=["on", "off"],
                    help="repeatable: with/without the job's scene index (scenes.json); default off")
    ap.add_argument("--repeat", type=int, default=1, help="runs per variant, to see run-to-run noise")
    ap.add_argument("--out", type=Path, help="report folder (default data/evals/<timestamp>)")
    ap.add_argument("--print-prompt", action="store_true", help="print the built-in prompt and exit")
    a = ap.parse_args(argv)

    if a.print_prompt:
        print(PROMPT)
        return
    if not a.target:
        ap.error("target is required")

    name, segs, picks, job_n, job_topic = load_target(a.target)
    topic = job_topic if a.topic is None else a.topic
    n = a.clips or job_n
    keep = a.candidates or 2 * n
    prompts = []
    for spec in a.prompt or ["default"]:
        text = PROMPT if spec == "default" else Path(spec).read_text(encoding="utf-8")
        try:
            check_prompt(text)
        except ValueError as e:
            raise SystemExit(f"{spec}: {e}") from None
        prompts.append((spec if spec == "default" else Path(spec).stem, text))
    default_think = next(k for k, val in THINK.items() if val == settings.select_think)
    scene_lines = job_scenes(a.target)
    if "on" in (a.scenes or []) and scene_lines is None:
        raise SystemExit("--scenes on: this job has no described scenes.json (SCENE_INDEX=on, then Retry/new job)")
    variants = [Variant(model=m, prompt_name=pn, prompt=pt, think=t, run=r, topic=topic, scenes=sc)
                for m, (pn, pt), t, sc, r in itertools.product(a.model or [settings.gemma_model], prompts,
                                                               a.think or [default_think], a.scenes or ["off"],
                                                               range(1, a.repeat + 1))]

    out = a.out or settings.data_dir / "evals" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"{name}: {len(segs)} segments, {len(picks)} of your picks, {len(variants)} variant(s) → {out}")

    rows: list[dict] = []
    baseline: list[tuple[float, float]] | None = None
    last_model = None
    for i, v in enumerate(variants, 1):
        if last_model and last_model != v.model:
            OllamaClient(model=last_model).unload()  # 8 GB VRAM: never hold two models
        last_model = v.model
        print(f"[{i}/{len(variants)}] {v.label}", flush=True)
        run_variant(v, segs, n, keep, scene_lines)
        spans = [(c["start"], c["end"]) for c in v.trace.get("clips", [])]
        row = summarize(v, picks, baseline)
        if baseline is None and not v.error:
            baseline = spans
        rows.append(row)
        print(f"    failed: {v.error}" if v.error else f"    {row['candidates']} candidates in {row['seconds']} s")
    if last_model:
        OllamaClient(model=last_model).unload()

    (out / "report.md").write_text(report_md(name, rows, variants, n, keep), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(
        {"source": name, "clips": n, "candidates": keep, "topic": topic, "picks": picks, "rows": rows,
         "variants": [{"label": v.label, "model": v.model, "prompt": v.prompt_name, "prompt_id": prompt_id(v.prompt),
                       "think": v.think, "run": v.run, "seconds": round(v.seconds, 1), "error": v.error,
                       "trace": v.trace} for v in variants]},
        indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nReport: {out / 'report.md'}")


if __name__ == "__main__":
    sys.exit(main())
