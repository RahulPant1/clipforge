"""Compare recorded clip scores with an offline audio/visual rerank on fixed windows."""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from .pipeline import nospeech


def overlap(left, right):
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def match(left, right):
    return overlap(left, right) / (max(left[1], right[1]) - min(left[0], right[0])) >= 0.5


def shortlist(candidates, scores):
    kept = []
    duplicates = 0
    for index in sorted(range(len(scores)), key=lambda index: -scores[index]):
        span = candidates[index]
        if any(overlap(span, other) for other in kept):
            duplicates += 1
            continue
        kept.append(span)
        if len(kept) == 5:
            break
    return kept, duplicates


def baseline_score(item):
    if "signal" not in item:
        return item["score"] + (item.get("relevance") or 0)
    signal = item["signal"]
    visual = item.get("vision", {})
    score = visual.get("score", round(5 + 1.5 * signal))
    score = min(score, nospeech.TEXT_SCORE if visual.get("mostly_text") else 10)
    return max(1, score) + (visual.get("relevance") or 0) + signal / 100


def metrics(candidates, scores, references):
    kept, duplicates = shortlist(candidates, scores)
    errors = []
    for reference in references:
        for candidate in kept:
            if match(reference, candidate):
                errors.append((abs(reference[0] - candidate[0]) + abs(reference[1] - candidate[1])) / 2)
                break
    return {"picked": len(kept), "matched": len(errors), "duplicate_rejections": duplicates,
            "mean_boundary_error_s": round(sum(errors) / len(errors), 2) if errors else None}


def evaluate(fixtures, jobs, cache_dir):
    signals = {}
    for fixture in fixtures:
        job = fixture["job"]
        folder = jobs / job
        scope = fixture["range"]
        refs = fixture["references"]
        items = json.loads((folder / "candidates.json").read_text())["items"]
        eligible = [item for item in items if "start" in item and "end" in item
                    and ("score" in item or "signal" in item)
                    and item.get("outcome") not in {"off_topic", "invalid", "too_short"}
                    and scope[0] <= item["start"] < item["end"] <= scope[1]]
        spans = [(item["start"], item["end"]) for item in eligible]
        scores = [baseline_score(item) for item in eligible]
        started = time.perf_counter()
        baseline = metrics(spans, scores, refs)
        baseline_ms = round((time.perf_counter() - started) * 1000, 3)
        if job not in signals:
            started = time.perf_counter()
            cache = folder / "signals.json"
            if not cache.is_file():
                cache = cache_dir / f"{job}.json"
            curve = nospeech.excitement(nospeech.analyse(folder / "source.mp4", cache))
            signals[job] = curve, round(time.perf_counter() - started, 3)
        curve, analysis_seconds = signals[job]
        started = time.perf_counter()
        strength = []
        for start, end in spans:
            samples = curve[math.floor(start / nospeech.HOP):math.ceil(end / nospeech.HOP)]
            strength.append(float(np.quantile(samples, 0.8)) if len(samples) else 0)
        spread = max(strength, default=0) - min(strength, default=0)
        combined = [score + 2 * (signal - min(strength)) / spread if spread else score
                    for score, signal in zip(scores, strength, strict=True)]
        reranked = metrics(spans, combined, refs)
        yield {"fixture": fixture["name"], "profile": fixture["profile"], "candidates": len(spans),
               "reference_count": len(refs), "baseline": baseline, "reranked": reranked,
               "baseline_ms": baseline_ms, "rerank_ms": round((time.perf_counter() - started) * 1000, 3),
               "shared_source_analysis_s": analysis_seconds}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list with job, name, profile, range and references")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    fixtures = json.loads(args.manifest.read_text())
    print(json.dumps(list(evaluate(fixtures, args.jobs, args.cache)), indent=2))


if __name__ == "__main__":
    main()
