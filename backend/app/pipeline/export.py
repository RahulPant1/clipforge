"""Export pack: one zip with every short, caption file, post copy, carousel and a titles.csv."""
import csv
import io
import zipfile
from pathlib import Path

from ..models import Job

FOLDERS = {".mp4": "shorts", ".srt": "captions", ".jpg": "thumbnails", ".png": "carousels", ".md": "posts"}


def _first_line(text: str) -> str:
    return (text or "").strip().split("\n", 1)[0]


def titles_csv(job: Job) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["short", "file", "seconds", "score", "title", "hook", "keywords",
                "youtube_title", "x_post", "instagram_first_line", "linkedin_hook"])
    for c in job.clips:
        p = c.content
        w.writerow([c.index, c.file, c.out_duration or round(c.end - c.start, 1), c.score, c.title, c.hook,
                    " ".join(c.keywords),
                    p["youtube"].title if "youtube" in p else "",
                    p["x"].parts[0] if "x" in p and p["x"].parts else "",
                    _first_line(p["instagram"].text) if "instagram" in p else "",
                    _first_line(p["linkedin"].text) if "linkedin" in p else ""])
    return buf.getvalue()


def build_pack(job: Job, job_dir: Path) -> Path:
    out = job_dir / f"clipforge_{job.id}.zip"
    names: list[str] = []
    for c in job.clips:
        stem = f"short_{c.index}"
        names += [f"{stem}.mp4", f"{stem}.srt", f"{stem}.jpg", f"{stem}.posts.md"]
        names += [f for post in c.content.values() for f in post.carousel]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            f = job_dir / n
            if f.is_file():
                folder = FOLDERS.get(f.suffix, "")
                # mp4s are already compressed — store them as-is (much faster)
                z.write(f, f"{folder}/{n}", zipfile.ZIP_STORED if f.suffix == ".mp4" else zipfile.ZIP_DEFLATED)
        z.writestr("titles.csv", "\ufeff" + titles_csv(job))  # BOM so Excel reads UTF-8/emoji
        z.writestr("README.txt", "ClipForge export\n\nshorts/      vertical videos\ncaptions/    .srt to upload "
                   "with each short\nposts/       ready-to-paste copy for YouTube, Instagram, X, LinkedIn\n"
                   "carousels/   Instagram carousel slides (post in order)\ntitles.csv   overview of everything\n")
    return out
