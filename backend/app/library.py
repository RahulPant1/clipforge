"""History of every video ClipForge worked on, kept OUTSIDE the job folders so it survives deleting a job.

    library/                                   (LIBRARY_DIR, default backend/data/library)
      ClipForge history.xlsx                   sheets "Videos" + "Shorts": filters, frozen header, links
      history.json                             the record the spreadsheet is built from
      2026-11-20 Jeff Su - How I Manage My Time [3a3c4e1b19]/
        info.md                                video metadata + the shorts made from it
        Short 1 - <title>.mp4                  hard link to the job's short_1.mp4 (no extra disk space)
        Short 1 - cover.jpg / captions.srt / posts.md / carousel N.png

Hard links: a re-render rewrites short_N.mp4 in place, so the library copy is always the latest; deleting the job
leaves the library files (and their disk space) in place. A LIBRARY_DIR on another drive (/mnt/c/...) can't hard
link, so files are copied (and re-copied when they change).

Deleted jobs stay in history.json / the spreadsheet, marked "deleted". Kept current by a debounced thread fed from
every JobStore save (`Library.touch`). Rebuild by hand: `python -m app.library`.
"""
import argparse
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path

from .config import settings
from .models import Job, JobStatus

log = logging.getLogger(__name__)

XLSX = "ClipForge history.xlsx"
RECORD = "history.json"
INFO = "info.md"
DEBOUNCE = 2.0  # s of quiet after the last save before writing (edits come in bursts)
PLATFORMS = ("youtube", "instagram", "x", "linkedin")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in Windows file names


def library_dir() -> Path:
    return Path(settings.library_dir) if settings.library_dir else settings.data_dir / "library"


def windows_path(path: Path) -> str | None:
    """How Explorer reaches this folder: /mnt/c/x → C:\\x, else \\\\wsl.localhost\\<distro>\\… (None off WSL)."""
    p = str(path)
    m = re.match(r"^/mnt/([a-z])(/.*)?$", p)
    if m:
        return f"{m[1].upper()}:" + (m[2] or "/").replace("/", "\\")
    distro = os.environ.get("WSL_DISTRO_NAME")
    return f"\\\\wsl.localhost\\{distro}" + p.replace("/", "\\") if distro else None


def safe_name(text, limit: int = 80) -> str:
    """Windows-safe file/folder name part (WSL folders are browsed from Explorer)."""
    s = " ".join(_BAD.sub(" ", str(text or "")).split())
    if len(s) > limit:  # cut at a word, and drop a dangling " -" / "|" left at the end
        s = s[:limit + 1].rsplit(" ", 1)[0] if " " in s[:limit] else s[:limit]
    return s.rstrip(" .-–—|,;") or "untitled"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mmss(seconds) -> str:
    if seconds is None:
        return ""
    s = int(float(seconds))  # floor, like moment_url: "0:42" and its link jump to the same second
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def moment_url(url: str, start: float) -> str:
    """The source video at this short's moment (YouTube `t=`); other sources get the plain URL."""
    if not url or not re.search(r"youtube\.com|youtu\.be", url):
        return url
    return f"{url}{'&' if '?' in url else '?'}t={int(start)}s"


def folder_name(job: Job, meta: dict) -> str:
    day = f"{job.created_at.astimezone():%Y-%m-%d}"
    title = safe_name(meta.get("title") or Path(job.source).stem or job.source, 80)
    channel = meta.get("channel")
    return f"{day} {safe_name(channel, 40)} - {title} [{job.id}]" if channel else f"{day} {title} [{job.id}]"


def _place(src: Path, dst: Path) -> None:
    """Hard link src at dst (copy across drives); leave it if it's already the same file / an up-to-date copy."""
    if dst.exists():
        s, d = src.stat(), dst.stat()
        if (s.st_ino, s.st_dev) == (d.st_ino, d.st_dev) or (s.st_size == d.st_size and d.st_mtime >= s.st_mtime):
            return
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


class Library:
    def __init__(self, store, root: Path | None = None, fetch_meta: bool = True):
        self.store, self._root, self.fetch_meta = store, root, fetch_meta
        self._pending: set[str] = set()
        self._dirty = False  # spreadsheet still to (re)write, e.g. it was open in Excel last time
        self._lock, self._flush_lock = threading.Lock(), threading.Lock()
        self._wake, self._last = threading.Event(), 0.0

    @property
    def root(self) -> Path:
        return self._root or library_dir()

    # ---------------------------------------------------------------- feeding

    def touch(self, job_id: str) -> None:
        """Called after every JobStore save/delete (cheap: the work happens later, on the thread)."""
        with self._lock:
            self._pending.add(job_id)
            self._last = time.monotonic()
        self._wake.set()

    def start(self) -> None:
        for job in self.store.list():  # catch up on everything (jobs from before the library existed too)
            self.touch(job.id)
        threading.Thread(target=self._loop, daemon=True, name="library").start()

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            while time.monotonic() - self._last < DEBOUNCE:
                time.sleep(0.5)
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # the history must never take the app down
                log.exception("library: update failed")

    # ---------------------------------------------------------------- syncing

    def flush(self) -> bool:
        """Bring every touched job's folder + record up to date, then rewrite the spreadsheet if anything changed.
        Returns False if the spreadsheet couldn't be written (open in Excel) — it's retried on the next flush."""
        with self._flush_lock:
            with self._lock:
                ids, self._pending = self._pending, set()
            records = _read_json(self.root / RECORD)
            changed = False
            for job_id in sorted(ids):
                job = self.store.get(job_id)
                if job is None:
                    rec = records.get(job_id)
                    if rec and not rec.get("deleted_at"):
                        rec["deleted_at"], rec["status"] = datetime.now(UTC).isoformat(), "deleted"
                        self._write_info(rec)
                        changed = True
                elif job.status not in (JobStatus.queued, JobStatus.running):  # busy: synced when it finishes
                    try:
                        records[job_id] = self._sync(job, records.get(job_id))
                        changed = True
                    except Exception:
                        log.exception("library: couldn't record job %s", job_id)
            if not (changed or self._dirty or not (self.root / XLSX).exists()):
                return True
            self.root.mkdir(parents=True, exist_ok=True)
            if changed:
                tmp = self.root / f".{RECORD}.tmp"
                tmp.write_text(json.dumps(records, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
                os.replace(tmp, self.root / RECORD)
            self._dirty = not write_xlsx(list(records.values()), self.root / XLSX)
            return not self._dirty

    def _meta(self, job: Job, job_dir: Path) -> dict:
        from .pipeline.download import META_VERSION, fetch_meta

        meta = _read_json(job_dir / "meta.json")
        if self.fetch_meta and job.source.startswith("http") and meta.get("meta_version", 1) < META_VERSION:
            try:  # job from before full metadata: fetch it once (no download)
                fetched = fetch_meta(job.source)
                meta = {**meta, **{k: v for k, v in fetched.items() if v not in ("", None, [])}}
            except Exception as e:
                log.warning("library: no metadata for %s (%s)", job.source, e)
                meta = {**meta, "meta_version": META_VERSION, "meta_error": str(e)[:200]}  # don't retry forever
            (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        return meta

    def _sync(self, job: Job, prev: dict | None) -> dict:
        job_dir = self.store.dir(job.id)
        meta = self._meta(job, job_dir)
        heard = _read_json(job_dir / "transcript_info.json")
        name = folder_name(job, meta)
        folder = self.root / name
        if prev and prev.get("folder") and prev["folder"] != name and (self.root / prev["folder"]).is_dir() \
                and not folder.exists():
            os.rename(self.root / prev["folder"], folder)  # title/channel became known: keep the same folder
        folder.mkdir(parents=True, exist_ok=True)

        files: dict[str, Path] = {}  # library name -> job file
        shorts = []
        for c in job.clips:
            if not c.file or not (job_dir / c.file).exists():
                continue
            base = f"Short {c.index}"
            video = f"{base} - {safe_name(c.title, 60)}.mp4"
            files[video] = job_dir / c.file
            for src, dst in [(c.cover, f"{base} - cover.jpg"), (f"short_{c.index}.srt", f"{base} - captions.srt"),
                             (f"short_{c.index}.posts.md", f"{base} - posts.md")]:
                if src and (job_dir / src).exists():
                    files[dst] = job_dir / src
            ig = c.content.get("instagram")
            for k, png in enumerate(ig.carousel if ig else [], 1):
                if (job_dir / png).exists():
                    files[f"{base} - carousel {k}.png"] = job_dir / png
            yt = c.content.get("youtube")
            shorts.append({
                "index": c.index, "title": c.title, "hook": c.hook, "start": round(c.start, 2),
                "end": round(c.end, 2), "length": c.out_duration or round(c.end - c.start, 1), "score": c.score,
                "relevance": c.relevance, "keywords": c.keywords, "reframe": c.reframe,
                "status": {p: c.content[p].status for p in PLATFORMS if p in c.content},
                "youtube_title": (yt.title or "") if yt else "", "file": video,
            })
        for dst, src in files.items():
            _place(src, folder / dst)
        for old in set((prev or {}).get("files", [])) - set(files):  # renamed title / deleted short: only ours
            (folder / old).unlink(missing_ok=True)

        duration = meta.get("duration") or heard.get("duration")
        if not duration and (job_dir / "source.mp4").exists():
            from .pipeline.nospeech import probe_duration

            duration = round(probe_duration(job_dir / "source.mp4"), 1) or None
        rec = {
            "id": job.id, "processed": job.created_at.isoformat(), "updated": job.updated_at.isoformat(),
            "status": job.status.value, "error": job.error, "topic": job.topic, "source": job.source,
            "mode": job.mode.value, "candidates": len(job.clips), "duration": duration,
            "language": heard.get("language") or meta.get("language") or "",
            "no_speech": (job_dir / "signals.json").exists(), "meta": meta, "folder": name,
            "files": sorted(files), "shorts": shorts, "deleted_at": None,
        }
        self._write_info(rec)
        return rec

    def _write_info(self, rec: dict) -> None:
        folder = self.root / rec["folder"]
        if folder.is_dir():
            (folder / INFO).write_text(info_md(rec), encoding="utf-8")


# ------------------------------------------------------------------ outputs

def info_md(rec: dict) -> str:
    m = rec.get("meta", {})
    url = m.get("url") or rec.get("source", "")
    lines = [f"# {m.get('title') or rec.get('source')}", ""]
    rows = [("Channel", m.get("channel")), ("Channel page", m.get("channel_url")), ("Video", url),
            ("Uploaded", m.get("upload_date")), ("Length", mmss(rec.get("duration"))),
            ("Views", f"{m['views']:,}" if m.get("views") else ""),
            ("Likes", f"{m['likes']:,}" if m.get("likes") else ""),
            ("Language", rec.get("language")), ("Category", ", ".join(m.get("categories") or [])),
            ("Tags", ", ".join(m.get("tags") or [])),
            ("Processed", rec["processed"][:16].replace("T", " ") + " UTC"), ("Topic", rec.get("topic")),
            ("Status", rec.get("status") + (f" — {rec['error']}" if rec.get("error") else "")),
            ("Deleted from ClipForge", (rec.get("deleted_at") or "")[:10]), ("ClipForge job", rec["id"])]
    lines += [f"- **{k}:** {v}" for k, v in rows if v]
    lines += ["", f"## Shorts ({len(rec['shorts'])} made from {rec.get('candidates', 0)} candidates)", ""]
    if rec["shorts"]:
        lines += ["| # | Title | From | Length | Score | YouTube | Instagram | X | LinkedIn | File |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for s in rec["shorts"]:
            st = s.get("status", {})
            at = f"[{mmss(s['start'])}–{mmss(s['end'])}]({moment_url(url, s['start'])})"
            lines.append(f"| {s['index']} | {s['title']} | {at} | {s['length']:.0f}s | {s['score']} | "
                         + " | ".join(st.get(p, "—") for p in PLATFORMS) + f" | {s['file']} |")
    if m.get("description"):
        lines += ["", "## Video description", "", m["description"]]
    return "\n".join(lines) + "\n"


def _day(iso: str | None):
    try:
        return datetime.fromisoformat(iso).astimezone().date() if iso else None
    except ValueError:
        return None


def _upload(s: str | None):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


VIDEO_COLS = [("Processed", 12), ("Channel", 22), ("Video", 52), ("Uploaded", 12), ("Length", 9), ("Views", 12),
              ("Language", 9), ("Category", 18), ("Topic", 18), ("Status", 10), ("Candidates", 11), ("Shorts", 8),
              ("Posted", 8), ("Folder", 44), ("Job ID", 12), ("URL", 44), ("Tags", 50)]
SHORT_COLS = [("Processed", 12), ("Channel", 22), ("Video", 40), ("Short", 7), ("Title", 44), ("Hook", 34),
              ("From", 9), ("To", 9), ("Length (s)", 10), ("Score", 7), ("Keywords", 30), ("YouTube", 10),
              ("Instagram", 10), ("X", 9), ("LinkedIn", 10), ("YouTube title", 40), ("File", 44), ("Job ID", 12)]


def write_xlsx(records: list[dict], path: Path) -> bool:
    """Both sheets, newest first. False if the file is locked (open in Excel on Windows) — nothing is lost."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    videos = wb.active
    videos.title = "Videos"
    shorts = wb.create_sheet("Shorts")
    link = Font(color="0563C1", underline="single")
    for ws, cols in ((videos, VIDEO_COLS), (shorts, SHORT_COLS)):
        ws.append([c for c, _ in cols])
        for i, (_, width) in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
            cell = ws.cell(row=1, column=i)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F3B52")
            cell.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"

    def put(ws, row: list, links: dict[int, str], dates=(), grey=False):
        ws.append(row)
        r = ws.max_row
        for col, target in links.items():
            if target:
                ws.cell(row=r, column=col).hyperlink = target
                ws.cell(row=r, column=col).font = link
        for col in dates:
            ws.cell(row=r, column=col).number_format = "yyyy-mm-dd"
        if grey:
            for c in ws[r]:
                c.font = Font(color="999999", strike=c.column in (3, 5) and ws is shorts)

    for rec in sorted(records, key=lambda r: r.get("processed", ""), reverse=True):
        m = rec.get("meta", {})
        url = m.get("url") or (rec["source"] if rec.get("source", "").startswith("http") else "")
        title = m.get("title") or rec.get("source", "")
        posted = sum(v == "posted" for s in rec["shorts"] for v in s.get("status", {}).values())
        gone = bool(rec.get("deleted_at"))
        folder = f"{rec['folder']}/"
        put(videos, [_day(rec.get("processed")), m.get("channel", ""), title, _upload(m.get("upload_date")),
                     mmss(rec.get("duration")), m.get("views"), rec.get("language", ""),
                     ", ".join(m.get("categories") or []), rec.get("topic", ""), rec.get("status", ""),
                     rec.get("candidates", 0), len(rec["shorts"]), posted, rec["folder"], rec["id"], url,
                     ", ".join(m.get("tags") or [])],
            {3: url, 14: folder, 16: url}, dates=(1, 4), grey=gone)
        for s in rec["shorts"]:
            st = s.get("status", {})
            put(shorts, [_day(rec.get("processed")), m.get("channel", ""), title, s["index"], s["title"], s["hook"],
                         mmss(s["start"]), mmss(s["end"]), s["length"], s["score"], ", ".join(s.get("keywords") or []),
                         *(st.get(p, "") for p in PLATFORMS), s.get("youtube_title", ""), s["file"], rec["id"]],
                {3: url, 7: moment_url(url, s["start"]), 17: folder + s["file"]}, dates=(1,), grey=gone)
    for ws in (videos, shorts):
        ws.auto_filter.ref = ws.dimensions
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        wb.save(tmp)
        os.replace(tmp, path)
        return True
    except PermissionError:
        log.warning("library: %s is open (Excel?) — will update it when it's closed", path.name)
        tmp.unlink(missing_ok=True)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild the ClipForge history (library folders + spreadsheet).")
    ap.add_argument("--no-fetch", action="store_true", help="don't look up missing YouTube metadata")
    args = ap.parse_args()
    from .jobs import default_store

    lib = Library(default_store(), fetch_meta=not args.no_fetch)
    for job in lib.store.list():
        lib.touch(job.id)
    ok = lib.flush()
    print(f"{lib.root / XLSX}{'' if ok else ' (locked — close it in Excel and run again)'}")


if __name__ == "__main__":
    main()
