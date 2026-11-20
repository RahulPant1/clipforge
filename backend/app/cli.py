"""CLI without the web UI.

  python -m app.cli "https://youtube.com/watch?v=..." --clips 5
  python -m app.cli my_video.mp4 --out data/cli_run
  python -m app.cli "<url>" --topic "salary negotiation"   # only moments about this
"""
import argparse
import json
from pathlib import Path

from .models import Stage
from .pipeline.orchestrator import run_pipeline


def main() -> None:
    ap = argparse.ArgumentParser(description="ClipForge — long video to Shorts, locally")
    ap.add_argument("source", help="YouTube URL or local video path")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--out", default="data/cli_run")
    ap.add_argument("--topic", default="", help="only moments about this (optional)")
    a = ap.parse_args()

    last = {"stage": None}

    def report(stage: Stage, progress: float, message: str) -> None:
        if stage != last["stage"]:
            print(f"\n== {stage.value} ==")
            last["stage"] = stage
        print(f"\r  {progress * 100:5.1f}%  {message:<60}", end="", flush=True)

    out = Path(a.out)
    clips = run_pipeline(a.source, out, a.clips, report, topic=a.topic)
    print()
    for c in clips:
        print(f"  ✓ {c.file}  [{c.score}/10]  {c.duration:4.1f}s  {c.title}")
    (out / "clips.json").write_text(json.dumps([c.model_dump() for c in clips], indent=2, ensure_ascii=False))
    print(f"\nDone → {out}/")


if __name__ == "__main__":
    main()
