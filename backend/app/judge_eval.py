"""Measure the clip judge (pipeline/judge.py) on real jobs.

    python -m app.judge_eval <job> [<job> ...] [--max-clips 6] [--out folder]

For every candidate clip of each job:
  clean      — the clip as Gemma picked it: how often the judge flags its opening / ending (on real picks some of
               those flags are right, so this is an upper bound on false alarms, not a pure error rate)
  late_start — the clip with its opening sentences dropped (start moved to a later sentence start, ≥ 25 % in):
               does the judge flag the opening, and does its fix land within FIX_TOL s of the real start?
  early_end  — the clip cut at an earlier sentence end (≤ 70 % in): flagged ending? fix within FIX_TOL s?
Dropping sentences doesn't always remove something the clip needs, so 100 % recall isn't the target; a large gap
between the damaged and clean flag rates is. Your own trims (job clips that differ from candidates.json) are
reported too when there are any. Needs Ollama; don't run it while a job is selecting clips.
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .config import settings
from .models import Clip, Segment
from .pipeline import judge
from .pipeline.highlights import sentence_bounds
from .pipeline.llm import OllamaClient

FIX_TOL = 3.0


def damaged(clip: Clip, segs: list[Segment]) -> dict[str, Clip]:
    starts, ends = sentence_bounds(segs)
    d = clip.end - clip.start
    out = {}
    later = [t for t in starts if clip.start + 0.25 * d <= t <= clip.end - settings.min_clip_seconds]
    if later:
        out["late_start"] = clip.model_copy(update={"start": later[0], "review": None})
    earlier = [t for t in ends if clip.start + settings.min_clip_seconds <= t <= clip.start + 0.7 * d]
    if earlier:
        out["early_end"] = clip.model_copy(update={"end": earlier[-1], "review": None})
    return out


def flags(r) -> set[str]:
    return {c.key for c in r.checks if not c.ok and c.key in ("opening", "ending", "list")}


def run(targets: list[str], max_clips: int, out: Path) -> dict:
    llm = OllamaClient()
    rows: list[dict] = []
    try:
        for target in targets:
            jd = Path(target) if Path(target).exists() else settings.data_dir / "jobs" / target
            job = json.loads((jd / "job.json").read_text())
            segs = [Segment(**s) for s in json.loads((jd / "transcript.json").read_text())]
            if not any(s.words for s in segs):
                continue
            clips = [Clip(**c) for c in job["clips"] if not c.get("custom")][:max_clips]
            for c in clips:
                variants = {"clean": c, **damaged(c, segs)}
                for kind, v in variants.items():
                    t = time.monotonic()
                    r = judge.review(segs, v, llm)
                    fx = r.fix
                    rows.append({
                        "job": jd.name, "clip": c.index, "kind": kind, "verdict": r.verdict, "flags": sorted(flags(r)),
                        "orig": [c.start, c.end], "judged": [v.start, v.end],
                        "fix": [fx.start, fx.end] if fx else None,
                        "fix_ok": bool(fx) and (abs(fx.start - c.start) <= FIX_TOL if kind == "late_start"
                                                else abs(fx.end - c.end) <= FIX_TOL if kind == "early_end" else False),
                        "seconds": round(time.monotonic() - t, 1), "model": r.model,
                        "notes": [f"{x.key}: {x.note}" for x in r.checks if not x.ok],
                    })
                    print(f"{jd.name} #{c.index} {kind:10} → {r.verdict:13} {sorted(flags(r))}", flush=True)
    finally:
        llm.unload()
    summary = summarize(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False))
    (out / "report.md").write_text(report_md(summary, rows), encoding="utf-8")
    return summary


def summarize(rows: list[dict]) -> dict:
    """Pure: per kind, how often the expected side was flagged, fixes within FIX_TOL s, verdict counts."""
    side = {"late_start": "opening", "early_end": "ending"}
    out: dict = {}
    for kind in ("clean", "late_start", "early_end"):
        rs = [r for r in rows if r["kind"] == kind]
        if not rs:
            continue
        verdicts: dict[str, int] = {}
        for r in rs:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        entry = {"n": len(rs), "verdicts": verdicts,
                 "flag_opening": round(sum("opening" in r["flags"] for r in rs) / len(rs), 2),
                 "flag_ending": round(sum(bool({"ending", "list"} & set(r["flags"])) for r in rs) / len(rs), 2),
                 "median_s": sorted(r["seconds"] for r in rs)[len(rs) // 2]}
        if kind in side:
            hit = [r for r in rs if side[kind] in r["flags"] or (kind == "early_end" and "list" in r["flags"])]
            entry["caught"] = round(len(hit) / len(rs), 2)
            entry["fix_within_tol"] = round(sum(r["fix_ok"] for r in rs) / len(rs), 2)
        out[kind] = entry
    return out


def report_md(summary: dict, rows: list[dict]) -> str:
    lines = [f"# Clip judge eval — {datetime.now():%Y-%m-%d %H:%M}", "",
             "| clips | n | flagged opening | flagged ending | caught (right side) | fix within 3 s | verdicts |",
             "|---|---|---|---|---|---|---|"]
    for kind, s in summary.items():
        v = ", ".join(f"{k} {n}" for k, n in sorted(s["verdicts"].items()))
        lines.append(f"| {kind} | {s['n']} | {s['flag_opening']:.0%} | {s['flag_ending']:.0%} | "
                     f"{s.get('caught', 0):.0%} | {s.get('fix_within_tol', 0):.0%} | {v} |")
    lines += ["", "## Per clip", ""]
    for r in rows:
        lines.append(f"- {r['job']} #{r['clip']} **{r['kind']}** {r['judged'][0]:.1f}-{r['judged'][1]:.1f} → "
                     f"**{r['verdict']}**" + (f", fix {r['fix'][0]:.1f}-{r['fix'][1]:.1f}" if r["fix"] else ""))
        lines += [f"  - {n}" for n in r["notes"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--max-clips", type=int, default=6)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    out = a.out or settings.data_dir / "evals" / f"judge-{datetime.now():%Y%m%d-%H%M%S}"
    summary = run(a.jobs, a.max_clips, out)
    print(json.dumps(summary, indent=1))
    print(f"\nReport: {out / 'report.md'}")


if __name__ == "__main__":
    main()
