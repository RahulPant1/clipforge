"""Pydantic schemas shared by the API, job store and pipeline."""
import math
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class CropKey(BaseModel):
    """A hand-set crop position (Shorts step → Adjust crop): from `t` on, the crop is centred on `x`."""
    t: float = Field(ge=0)  # SOURCE seconds (survives trims, like clip.removed)
    x: float = Field(ge=0, le=1)  # crop centre, 0..1 of the source width
    pan: bool = False  # glide here from the previous key (camera pan) instead of cutting at t


class RenderOptions(BaseModel):
    """Per-clip overrides; None = use the global setting."""
    jumpcut: bool | None = None
    reframe: str | None = None  # auto | face | split | speaker | center | fit | manual (crop_keys)
    crop_keys: list[CropKey] | None = Field(None, max_length=200)  # reframe="manual": where the crop sits, over time
    caption_style: str | None = None  # karaoke | simple | none
    caption_preset: str | None = None  # classic | punch | clean | boxed | neon | custom (presets.py)
    caption_position: str | None = None  # bottom | middle
    zoom: bool | None = None  # punch-in on alternate jump-cut segments
    hook: bool | None = None  # show clip.hook as a title card for the first seconds
    follow: float | None = None  # follow THIS person: face centre x (0..1 of source width) picked in the UI
    watermark: bool | None = None  # brand kit logo in the corner (None = on when the kit has a logo)
    end_card: bool | None = None  # brand kit end card after the last word (None = the kit's setting)
    sfx: bool | None = None  # whoosh on cuts + pop on keywords (None = SFX_DEFAULT)
    music: str | None = None  # track in data/sounds/music ("" = none, None = MUSIC_DEFAULT)
    emoji: bool | None = None  # colour emoji above caption lines + the hook's emoji (None = CAPTION_EMOJI)
    aspects: list[Literal["4:5", "1:1"]] | None = None  # feed versions next to the 9:16 (None = EXTRA_ASPECTS)
    dub: bool | None = None  # Replace voice: the clip's dub track (dub.py) instead of the original speech


class Insert(BaseModel):
    """A motion-graphics card burned into the short (inserts.py). stat_counter: a number counting up to `value`
    with a label, shown when the number is spoken. The number comes from the transcript (numbers.py), never a model."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8], pattern=r"^[0-9a-z]{1,16}$")
    template: Literal["stat_counter"] = "stat_counter"
    t: float = Field(ge=0)  # SOURCE second the number starts being said (custom: where you put it)
    spoken: str = Field("", max_length=80)  # the words as transcribed ("fifty percent")
    value: float
    decimals: int = Field(0, ge=0, le=3)
    prefix: str = Field("", max_length=4)  # ₹ $ € £
    suffix: str = Field("", max_length=12)  # % · x · K · M · " lakh" · " crore"
    grouping: Literal["western", "indian", "none"] = "western"
    label: str = Field("", max_length=40)
    position: Literal["auto", "upper", "lower"] = "auto"
    approved: bool = False  # only approved inserts are rendered
    custom: bool = False  # you added it (not tied to a spoken number)

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v) or abs(v) >= 1e15:
            raise ValueError("value must be a finite number")
        return v


class HookVariant(BaseModel):
    style: str  # hooks.STYLES key (question, bold_claim, …) or "other"
    title: str
    hook: str


class ReviewCheck(BaseModel):
    """One thing the clip judge checked (judge.py). source: rule = measured from the transcript, gemma = model."""
    key: str  # opening | ending | list | hook | faithful | value | clear
    ok: bool
    note: str = Field("", max_length=400)
    quote: str = Field("", max_length=200)  # words from the transcript that show it (checked to be there)
    source: Literal["rule", "gemma"] = "gemma"


class ClipFix(BaseModel):
    start: float
    end: float
    reason: str = Field("", max_length=300)


class ClipReview(BaseModel):
    """Clip judge verdict on the span start-end (a trim since makes it outdated; the UI offers to check again)."""
    verdict: Literal["ready", "trim", "needs_context", "reject"]
    checks: list[ReviewCheck] = []
    fix: ClipFix | None = None  # boundaries that fix the opening/ending issues (sentence-snapped, ≤ max clip)
    start: float
    end: float
    model: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    need_seconds: float | None = None  # the whole idea's length when it doesn't fit max_clip_seconds


class Clip(BaseModel):
    index: int = 0
    start: float
    end: float
    title: str
    hook: str
    brand_rev: int = -1  # BrandKit.rev it was rendered with (-1 = no kit in use)
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
    review: ClipReview | None = None  # clip judge (judge.py): verdict, checks, suggested fix
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
    inserts: list[Insert] = Field([], max_length=8)  # stat cards etc. (only approved ones are burned in)
    inserts_checked: bool = False  # cards already suggested (produce doesn't ask Gemma again)
    visual: str = ""  # no-speech clips: what the vision model saw on screen (feeds the posts instead of speech)
    options: RenderOptions = RenderOptions()
    stale: bool = False  # edited after rendering -> needs re-render
    formats: list[str] = []  # feed versions rendered with it: "4:5" → short_N.45.mp4, "1:1" → short_N.11.mp4
    version: int = 0  # bumps on every render (cache-busting in the UI)
    render_error: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


Purpose = Literal["create", "learn"]  # create = shorts + posts; learn = study notes (learn.py)


class JobCreate(BaseModel):
    url: str
    purpose: Purpose = "create"
    num_clips: int = Field(default=5, ge=1, le=20)
    mode: JobMode = JobMode.review
    topic: str = Field(default="", max_length=200)  # optional: only moments about this ("salary negotiation")
    hinglish: bool | None = None  # Hindi speech → Latin letters; None = settings.hinglish_default
    title: str = Field(default="", max_length=300)  # known up front (Find videos) → shown before the download
    channel: str = Field(default="", max_length=200)


class VideoInfo(BaseModel):
    title: str = Field(default="", max_length=300)
    channel: str = Field(default="", max_length=200)


class BatchCreate(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=50)  # one job each, same settings, queued in this order
    info: dict[str, VideoInfo] = {}  # url -> title/channel already known from the list (optional)
    num_clips: int = Field(default=5, ge=1, le=20)
    mode: JobMode = JobMode.review
    topic: str = Field(default="", max_length=200)
    hinglish: bool | None = None


class BatchLinks(BaseModel):
    text: str = Field(min_length=1, max_length=20000)  # pasted links: videos, playlists, channels


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
    inserts: list[Insert] | None = Field(None, max_length=8)  # replaces the list
    options: RenderOptions | None = None


class CoverEdit(BaseModel):
    text: str | None = Field(default=None, max_length=80)  # None = keep
    style: str | None = None  # cutout | frame; None = keep
    t: float | None = None  # source second of the frame; None = keep
    auto: bool = False  # forget the chosen frame and pick the best one again


class QuoteEdit(BaseModel):
    text: str | None = Field(default=None, max_length=300)  # None = keep, "" = pick a line automatically again


class SmartTopic(BaseModel):
    topic: str = Field(min_length=1, max_length=120)


class SmartJudge(BaseModel):
    id: str = Field(pattern=r"^[\w-]{11}$")  # YouTube video id
    topic: str = Field(min_length=1, max_length=120)


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
    title: str = ""  # the video's title (from the batch/Find list, else meta.json once downloaded); "" = not known yet
    channel: str = ""
    num_clips: int = 5
    mode: JobMode = JobMode.review
    topic: str = ""  # steers clip selection (highlights.FOCUS); "" = best moments on any subject
    hinglish: bool = False  # Hindi (Devanagari) transcript converted to Latin letters (pipeline/hinglish.py)
    purpose: Purpose = "create"
    learn: bool = False  # study notes made (learn.json) — any job can have them
    status: JobStatus = JobStatus.queued
    stage: Stage = Stage.queued
    progress: float = 0.0  # 0..1 within the current stage
    message: str = ""
    error: str | None = None
    clips: list[Clip] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BrandKit(BaseModel):
    """Your look on every short (data/brand_kit.json, all jobs): logo watermark, end card, brand colour."""
    enabled: bool = True
    logo: bool = False  # data/brand/logo.png uploaded (POST /api/brand-kit/logo)
    position: str = Field(default="top-left", pattern=r"^top-(left|right)$")  # bottom corners: platform buttons
    size: int = Field(default=14, ge=6, le=35)  # logo width, % of the 1080 px frame
    opacity: float = Field(default=0.85, ge=0.2, le=1.0)
    end_card: bool = True
    end_seconds: float = Field(default=2.0, ge=1.0, le=4.0)
    end_text: str = Field(default="Follow for more", max_length=60)
    handle: str = Field(default="", max_length=40)  # "" = BRAND_HANDLE from .env
    accent: str = Field(default="", pattern=r"^(#[0-9a-fA-F]{6})?$")  # "" = CAROUSEL_ACCENT from .env
    accent_captions: bool = False  # karaoke active word in the brand colour
    rev: int = 0  # bumped when anything burned into a short changes (shorts made with an older rev → "re-render")


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


class VoiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    consent: bool = False  # it's your voice, or its owner agreed to it being cloned


class VoiceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    description: str | None = Field(None, max_length=300)
    language: str | None = None


class RecordingEdit(BaseModel):
    text: str | None = Field(None, max_length=600)  # what is said in it (fix Whisper's transcript)
    style: str | None = None
    use: bool | None = None  # include in the voice


class VoiceBuild(BaseModel):
    style: str = ""  # prefer a recording tagged with this style as the delivery reference
    sample_text: str = Field("", max_length=300)  # the test sentence for this version ("" = TTS_SAMPLE_TEXT)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    language: str = Field("", max_length=20)  # "" = the voice's / TTS_LANGUAGE
    version: int | None = None  # None = the newest


class CompareRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    versions: list[int] = Field(min_length=2, max_length=3)
    language: str = Field("", max_length=20)


class Chapter(BaseModel):
    t: float  # SOURCE seconds — a real transcript time
    title: str = Field(max_length=100)


class Concept(BaseModel):
    name: str = Field(max_length=80)
    explanation: str = Field(max_length=600)
    t: float  # where it's explained


class StudyNote(BaseModel):
    t: float
    title: str = Field(max_length=120)
    note: str = Field(max_length=600)


class QuizQuestion(BaseModel):
    q: str = Field(max_length=300)
    choices: list[str] = Field(min_length=2, max_length=5)
    answer: int = Field(ge=0)  # index into choices
    explain: str = Field("", max_length=400)
    t: float  # where the video answers it


class LearnNotes(BaseModel):
    """Learning mode (learn.py): what a video teaches, every point tied to where it's said."""
    title: str = ""
    summary: str = ""
    chapters: list[Chapter] = []
    concepts: list[Concept] = []
    notes: list[StudyNote] = []
    quiz: list[QuizQuestion] = []
    hands_on: list[str] = []  # exercises to try it yourself
    next_steps: list[str] = []  # what to learn next
    language: str | None = None
    model: str | None = None
    made_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DubLine(BaseModel):
    """One sentence of a clip's dialogue: the original (SOURCE times) and what the chosen voice says instead."""
    i: int
    start: float  # SOURCE seconds — where the original sentence starts (the new one starts here too)
    end: float
    original: str = Field(max_length=2000)
    text: str = Field(max_length=2000)  # what to say ("" = silence in its place)
    file: str | None = None  # dub_N_i_<hash>.wav, the generated speech for `said`
    said: str | None = None  # the text `file` was made from (≠ text → regenerate)
    voice: str | None = None  # voice/version it was made with ("id@v3")
    seconds: float | None = None  # length of the generated speech
    words: list[Word] = []  # its words, timed relative to the generated file (Whisper), for captions
    fit: Literal["ok", "stretched", "too_long"] | None = None
    rate: float | None = None  # speed-up applied to fit the slot (1 = none)
    at: float | None = None  # clip-relative start where the layout put it (≠ original when borrowing time)
    over: float | None = None  # seconds cut off a too_long line (how much to shorten it)


class DubScript(BaseModel):
    """Replace voice for one clip (dub_N.json): which voice speaks, and each sentence's new text."""
    clip: int
    voice: str = ""
    version: int | None = None  # None = the voice's newest version
    lines: list[DubLine] = []
    track: str | None = None  # dub_N.wav once assembled (clip-relative, clip length)
    track_version: int = 0  # bumps on every assembly (cache-busting)
    span: tuple[float, float] | None = None  # the clip's start/end the track was laid out for


class DubEdit(BaseModel):
    voice: str | None = Field(None, max_length=40)
    version: int | None = None
    texts: dict[int, str] = {}  # line i → new text ("" = silence there)
    reset: bool = False  # start again from the clip's current sentences


class DubGenerate(BaseModel):
    lines: list[int] | None = None  # None = every line that needs it; else regenerate just these


class ProduceRequest(BaseModel):
    confirm: bool = False  # yes, create approved clips the judge flagged (needs context / reject)
