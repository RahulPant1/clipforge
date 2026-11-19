"""Pydantic schemas shared by the API, job store and pipeline."""
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    review = "review"  # waiting for you to approve clips (review mode)
    done = "done"
    failed = "failed"


class JobMode(str, Enum):
    review = "review"  # pause after clip selection so you can edit/approve
    auto = "auto"  # run everything end to end


class Stage(str, Enum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    selecting = "selecting"
    writing = "writing"  # Content Studio: per-platform posts
    rendering = "rendering"
    done = "done"


class Word(BaseModel):
    start: float
    end: float
    word: str


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = []


class Post(BaseModel):
    """Ready-to-paste content for one platform."""
    platform: str  # x | instagram | youtube | linkedin
    angle: str  # creative angle key, e.g. "hot_take"
    angle_label: str = ""
    text: str  # final copy, ready to paste (X threads joined with ---)
    parts: list[str] = []  # X: individual posts; Instagram: carousel slide texts
    title: str | None = None  # YouTube title
    hashtags: list[str] = []
    carousel: list[str] = []  # Instagram carousel PNG filenames in the job folder
    extra: dict[str, str | list[str]] = {}  # yt tags, pinned comment, ig cover text …
    status: str = "draft"  # draft | approved | posted
    edited: bool = False  # you changed the text by hand


class RenderOptions(BaseModel):
    """Per-clip overrides; None = use the global setting."""
    jumpcut: bool | None = None
    reframe: str | None = None  # auto | face | split | center
    caption_style: str | None = None  # karaoke | simple
    zoom: bool | None = None  # punch-in on alternate jump-cut segments
    hook: bool | None = None  # show clip.hook as a title card for the first seconds


class Clip(BaseModel):
    index: int = 0
    start: float
    end: float
    title: str
    hook: str
    score: int = Field(ge=1, le=10)
    keywords: list[str] = []  # Gemma-picked words highlighted in captions
    file: str | None = None  # filename inside the job folder once rendered
    cut_seconds: float = 0.0  # silence/filler removed by jump cuts
    out_duration: float | None = None  # final length after cuts
    reframe: str = "center"  # face | split | center | none — how the 9:16 crop was chosen
    content: dict[str, Post] = {}  # platform -> generated post
    approved: bool = True  # chosen for rendering (review step)
    custom: bool = False  # added by you from the transcript
    options: RenderOptions = RenderOptions()
    stale: bool = False  # edited after rendering -> needs re-render
    version: int = 0  # bumps on every render (cache-busting in the UI)
    render_error: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class JobCreate(BaseModel):
    url: str
    num_clips: int = Field(default=5, ge=1, le=20)
    mode: JobMode = JobMode.review


class ClipEdit(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    hook: str | None = None
    keywords: list[str] | None = None
    approved: bool | None = None
    options: RenderOptions | None = None


class ClipAdd(BaseModel):
    start: float
    end: float
    title: str = "My clip"
    hook: str = ""


class PostEdit(BaseModel):
    text: str | None = None
    title: str | None = None
    parts: list[str] | None = None
    status: str | None = None  # draft | approved | posted


class Job(BaseModel):
    id: str
    source: str  # URL or uploaded filename
    num_clips: int = 5
    mode: JobMode = JobMode.review
    status: JobStatus = JobStatus.queued
    stage: Stage = Stage.queued
    progress: float = 0.0  # 0..1 within the current stage
    message: str = ""
    error: str | None = None
    clips: list[Clip] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
