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
    posted_at: datetime | None = None  # set when marked posted (cleared if it goes back)
    url: str = ""  # link to the live post, pasted after posting (History links to it)


class RenderOptions(BaseModel):
    """Per-clip overrides; None = use the global setting."""
    jumpcut: bool | None = None
    reframe: str | None = None  # auto | face | split | speaker | center | fit
    caption_style: str | None = None  # karaoke | simple | none
    caption_preset: str | None = None  # classic | punch | clean | boxed | neon | custom (presets.py)
    caption_position: str | None = None  # bottom | middle
    zoom: bool | None = None  # punch-in on alternate jump-cut segments
    hook: bool | None = None  # show clip.hook as a title card for the first seconds
    follow: float | None = None  # follow THIS person: face centre x (0..1 of source width) picked in the UI


class HookVariant(BaseModel):
    style: str  # hooks.STYLES key (question, bold_claim, …) or "other"
    title: str
    hook: str


class Clip(BaseModel):
    index: int = 0
    start: float
    end: float
    title: str
    hook: str
    score: int = Field(ge=1, le=10)
    relevance: int | None = None  # 1-10 how on-topic (only when the job has a topic)
    keywords: list[str] = []  # Gemma-picked words highlighted in captions
    file: str | None = None  # filename inside the job folder once rendered
    cut_seconds: float = 0.0  # silence/filler removed by jump cuts
    out_duration: float | None = None  # final length after cuts
    reframe: str = "center"  # face | split | speaker | center | fit | none — how the crop was chosen
    fit_seconds: float = 0.0  # source seconds shown whole over a blurred copy (shots with no face)
    content: dict[str, Post] = {}  # platform -> generated post
    approved: bool = True  # chosen for rendering (review step)
    custom: bool = False  # added by you from the transcript
    removed: list[tuple[float, float]] = []  # words you struck in the transcript (SOURCE seconds) → cut out
    variants: list[HookVariant] = []  # alternative title + hook pairs to choose from (Pick step)
    cover: str | None = None  # short_N.cover.jpg once made (reel cover / thumbnail)
    cover_text: str = ""  # text printed on the cover ("" = Instagram cover_text, else the hook)
    cover_t: float | None = None  # SOURCE second of the cover frame (None = best frame, picked automatically)
    cover_version: int = 0  # bumps on every cover render (cache-busting)
    cover_style: str | None = None  # cutout | frame; None = settings.cover_style
    cover_cutout: bool = False  # the cover really is a cutout (a "cutout" cover with no person falls back to frame)
    quote: str = ""  # quote card text: a line said in the clip ("" = picked automatically)
    quote_custom: bool = False  # you wrote the quote: kept as is (a picked one is re-picked if a trim drops it)
    quote_cards: list[str] = []  # short_N.quote.png (1:1), short_N.quote45.png (4:5) once made
    quote_version: int = 0  # bumps on every quote card render (cache-busting)
    visual: str = ""  # no-speech clips: what the vision model saw on screen (feeds the posts instead of speech)
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
    topic: str = Field(default="", max_length=200)  # optional: only moments about this ("salary negotiation")
    hinglish: bool | None = None  # Hindi speech → Latin letters; None = settings.hinglish_default


class ScriptChoice(BaseModel):
    hinglish: bool  # True: transcript in Latin letters; False: back to Whisper's Devanagari


class Retry(BaseModel):
    topic: str | None = Field(default=None, max_length=200)  # change the topic ("" = none); None = keep it


class ClipEdit(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    hook: str | None = None
    keywords: list[str] | None = None
    approved: bool | None = None
    removed: list[tuple[float, float]] | None = None  # struck word ranges, replaces the list ([] restores all)
    options: RenderOptions | None = None


class CoverEdit(BaseModel):
    text: str | None = Field(default=None, max_length=80)  # None = keep
    style: str | None = None  # cutout | frame; None = keep
    t: float | None = None  # source second of the frame; None = keep
    auto: bool = False  # forget the chosen frame and pick the best one again


class QuoteEdit(BaseModel):
    text: str | None = Field(default=None, max_length=300)  # None = keep, "" = pick a line automatically again


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
    url: str | None = Field(default=None, max_length=500)  # link to the live post ("" clears it)


class Job(BaseModel):
    id: str
    source: str  # URL or uploaded filename
    num_clips: int = 5
    mode: JobMode = JobMode.review
    topic: str = ""  # steers clip selection (highlights.FOCUS); "" = best moments on any subject
    hinglish: bool = False  # Hindi (Devanagari) transcript converted to Latin letters (pipeline/hinglish.py)
    status: JobStatus = JobStatus.queued
    stage: Stage = Stage.queued
    progress: float = 0.0  # 0..1 within the current stage
    message: str = ""
    error: str | None = None
    clips: list[Clip] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BrandVoice(BaseModel):
    """Your own writing, used as few-shot examples so posts sound like you (data/brand_voice.json, all jobs)."""
    enabled: bool = True
    notes: str = ""  # free-form voice notes ("lowercase, no emojis, Hinglish ok"); replaces CONTENT_VOICE
    examples: dict[str, list[str]] = {}  # platform -> your best posts on that platform


class WordEdit(BaseModel):
    seg: int
    i: int
    word: str


class ReplaceWords(BaseModel):
    find: str
    replace: str
    dry_run: bool = False
    remember: bool = False  # also add the replacement to the vocabulary for future transcripts


class Vocabulary(BaseModel):
    terms: list[str] = []
