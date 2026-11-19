"""Content Studio — Gemma writes platform-native posts for every short.

Each platform has its own creative angles, rules and JSON schema. The variety engine
(history.py) rotates angles and bans recently used openers, so a week of posts doesn't
read like one template.
"""
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..models import Clip, Post, Segment
from .history import ContentHistory
from .llm import LLM

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]

NAMES = {"x": "X (Twitter)", "instagram": "Instagram Reels", "youtube": "YouTube Shorts", "linkedin": "LinkedIn"}

# ------------------------------------------------------------------ creative angles
# key -> (label, instruction). Order matters only as a tie-break.
ANGLES: dict[str, dict[str, tuple[str, str]]] = {
    "x": {
        "hot_take": ("Hot take", "One bold, slightly contrarian opinion drawn from the clip, then 1-2 lines of proof."),
        "thread": ("Thread", "A 4-6 post thread: post 1 is a scroll-stopping hook ending with a colon or arrow, "
                             "posts 2-5 each deliver one concrete point, last post wraps up with a question."),
        "stat_hook": ("Stat hook",
                      "Lead with the most surprising number or fact said in the clip, then why it matters."),
        "question": ("Question",
                     "Open with a provocative question your audience argues about, answer it in 2-3 lines."),
        "quote_card": ("Quote", "Pull the single most quotable line from the transcript in quotes, add one sharp line "
                                "of your own commentary."),
        "one_liner": ("One-liner",
                      "A witty, memorable one-liner (under 140 characters) that captures the clip's idea."),
    },
    "instagram": {
        "story": ("Mini story", "Tell it as a tiny story: setup, tension, payoff. Make the reader feel something."),
        "listicle": ("Listicle", "3-5 quick numbered takeaways, each one line, emoji bullets."),
        "pov": ("POV", "Start with 'POV:' and put the viewer inside the situation from the clip."),
        "myth_fact": ("Myth vs fact", "Bust a common myth using what's said in the clip: ❌ Myth / ✅ Fact format."),
        "save_this": ("Save-this cheat sheet", "A practical cheat-sheet style caption people will save for later."),
        "relatable": ("Relatable", "Open with a 'we've all been there' relatable moment, then the insight."),
    },
    "youtube": {
        "curiosity": ("Curiosity gap", "Title creates a curiosity gap without lying."),
        "how_to": ("How-to", "Title promises a clear outcome: 'How to …' or 'Do this to …'."),
        "number": ("Number-led", "Title leads with a number or stat from the clip."),
        "challenge": ("Challenge a belief", "Title challenges something most viewers believe."),
        "you_focus": ("You-focused", "Title speaks directly to the viewer using 'you/your'."),
    },
    "linkedin": {
        "lesson": ("Lesson learned", "Frame it as a lesson you took away from this clip, first person, honest."),
        "framework": ("Framework", "Turn the clip into a simple 3-step framework with a memorable name."),
        "data_insight": ("Insight",
                         "Lead with the key insight or number, then explain why it matters for professionals."),
        "contrarian": ("Contrarian", "A respectful contrarian take: 'Unpopular opinion: …' backed by the clip."),
        "story": ("Story", "A short story-driven post with a clear business or career takeaway."),
        "mistakes": ("Mistakes to avoid", "List 3 mistakes people make, based on the clip, and the fix for each."),
    },
}

RULES = {
    "x": """- Each post max 280 characters. Punchy, conversational, zero corporate tone.
- Use format "{fmt}": single = exactly 1 post, thread = 4-6 posts.
- At most 2 hashtags total, only if natural. Max 2 emoji per post.""",
    "instagram": """- caption_hook: the first line, under 125 characters, must stop the scroll.
- caption_body: 2-6 short lines separated by line breaks, emoji welcome.
- cta: one line asking to save, share, comment or follow.
- hashtags: 12-18 relevant hashtags, mix of broad and niche, no spaces.
- carousel: 5-7 slides for a companion carousel. Slide 1 = cover with a big promise, middle slides = one idea
  each, last slide = call to action. Each title max 6 words, each text max 25 words.
- cover_text: 2-6 words to print on the reel cover.""",
    "youtube": """- title: under 60 characters, honest (no fake clickbait), include one keyword.
- description: 2-4 sentences on the value of the short, keywords used naturally.
- hashtags: 3-5 relevant hashtags (#Shorts is added automatically).
- tags: 8-12 search keywords people would type.
- pinned_comment: a question that invites viewers to reply.""",
    "linkedin": """- hook: one line under 150 characters that earns the "see more" click.
- body: 3-6 short paragraphs of 1-2 sentences. Professional but human. No hype words
  (game-changer, revolutionary, unlock, delve).
- takeaway: one crisp sentence.
- question: an open question to drive comments.
- hashtags: 3-5.""",
}

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
SCHEMAS = {
    "x": {"type": "object", "properties": {"posts": _STRS, "hashtags": _STRS}, "required": ["posts", "hashtags"]},
    "instagram": {"type": "object", "properties": {
        "caption_hook": _STR, "caption_body": _STR, "cta": _STR, "hashtags": _STRS, "cover_text": _STR,
        "carousel": {"type": "array", "items": {"type": "object", "properties": {"title": _STR, "text": _STR},
                                                "required": ["title", "text"]}}},
        "required": ["caption_hook", "caption_body", "cta", "hashtags", "carousel", "cover_text"]},
    "youtube": {"type": "object", "properties": {
        "title": _STR, "description": _STR, "hashtags": _STRS, "tags": _STRS, "pinned_comment": _STR},
        "required": ["title", "description", "hashtags", "tags", "pinned_comment"]},
    "linkedin": {"type": "object", "properties": {
        "hook": _STR, "body": _STR, "takeaway": _STR, "question": _STR, "hashtags": _STRS},
        "required": ["hook", "body", "takeaway", "question", "hashtags"]},
}

PROMPT = """You are a top {platform_name} copywriter. Write ONE post.
Language: {language}. Voice: {voice}.

Source: a short clip from the video "{video_title}"{by_channel}.
Clip title: {title}
Clip hook: {hook}
Key words: {keywords}
What is said in the clip:
\"\"\"{text}\"\"\"

Creative angle for THIS post: {angle_label} — {angle_desc}
{avoid_block}
Hard rules:
- Never invent facts, numbers, names or quotes that are not in the transcript.
- Write natively for {platform_name}; it must not read like a generic caption.
{rules}
{cta_block}"""


@dataclass
class ContentContext:
    clip: Clip
    text: str  # what's said in the clip
    video_title: str = ""
    channel: str = ""
    url: str = ""


def clip_text(segs: list[Segment], clip: Clip, limit: int = 2500) -> str:
    return " ".join(s.text for s in segs if s.end > clip.start and s.start < clip.end)[:limit]


def load_meta(job_dir: Path) -> dict:
    p = job_dir / "meta.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def build_prompt(platform: str, angle: str, ctx: ContentContext, avoid: list[str]) -> str:
    label, desc = ANGLES[platform][angle]
    fmt = "thread" if angle == "thread" else "single"
    avoid_block = ("Do NOT start the post like any of these recent posts (be fresh):\n"
                   + "\n".join(f"- {a}" for a in avoid) + "\n") if avoid else ""
    cta = f"If there is a call to action, you may mention {settings.brand_handle}." if settings.brand_handle else ""
    c = ctx.clip
    return PROMPT.format(
        platform_name=NAMES[platform], language=settings.content_language, voice=settings.content_voice,
        video_title=ctx.video_title or c.title, by_channel=f" by {ctx.channel}" if ctx.channel else "",
        title=c.title, hook=c.hook, keywords=", ".join(c.keywords) or "-", text=ctx.text,
        angle_label=label, angle_desc=desc, avoid_block=avoid_block,
        rules=RULES[platform].format(fmt=fmt), cta_block=cta,
    )


# ------------------------------------------------------------------ assembly + platform limits

def norm_tags(tags, limit: int) -> list[str]:
    out, seen = [], set()
    for t in tags or []:
        t = re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append("#" + t)
    return out[:limit]


def split_post(text: str, limit: int = 280) -> list[str]:
    """Split an over-long X post on sentence, then word boundaries."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        for piece in ([sent] if len(sent) <= limit else _wrap_words(sent, limit)):
            if cur and len(cur) + 1 + len(piece) > limit:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    return out


def _wrap_words(text: str, limit: int) -> list[str]:
    out, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > limit:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return out + ([cur] if cur else [])


def _s(raw: dict, key: str) -> str:
    v = raw.get(key, "")
    return str(v).strip() if v is not None else ""


def _credit(ctx: ContentContext) -> str:
    if not settings.credit_source or not (ctx.video_title or ctx.url):
        return ""
    who = f" — {ctx.channel}" if ctx.channel else ""
    link = f" {ctx.url}" if ctx.url else ""
    return f"🎥 Clip from: {ctx.video_title}{who}{link}"


def assemble_x(raw: dict, angle: str, ctx: ContentContext) -> Post:
    posts = [p for item in raw.get("posts") or [] for p in split_post(str(item))]
    if angle != "thread":
        posts = posts[:1]
    tags = norm_tags(raw.get("hashtags"), 2)
    # Models often write the hashtags into the last post themselves — only append the missing ones
    missing = [t for t in tags if posts and not re.search(rf"{re.escape(t)}\b", posts[-1], re.IGNORECASE)]
    if missing and len(posts[-1]) + 1 + len(" ".join(missing)) <= 280:
        posts[-1] = f"{posts[-1]} {' '.join(missing)}"
    if len(posts) > 1 and "🧵" not in posts[0] and len(posts[0]) <= 277:
        posts[0] += " 🧵"
    return Post(platform="x", angle=angle, text="\n\n---\n\n".join(posts), parts=posts, hashtags=tags)


def assemble_instagram(raw: dict, angle: str, ctx: ContentContext) -> Post:
    tags = norm_tags(raw.get("hashtags"), 20)
    credit = _credit(ctx)
    lines = [_s(raw, "caption_hook"), _s(raw, "caption_body"), _s(raw, "cta"), credit]
    text = "\n\n".join(x for x in lines if x) + ("\n.\n.\n.\n" + " ".join(tags) if tags else "")
    slides = [{"title": _s(s, "title"), "text": _s(s, "text")}
              for s in (raw.get("carousel") or []) if isinstance(s, dict) and (s.get("title") or s.get("text"))][:8]
    return Post(platform="instagram", angle=angle, text=text, hashtags=tags,
                parts=[f"{s['title']} — {s['text']}" for s in slides],
                extra={"cover_text": _s(raw, "cover_text"), "slides": json.dumps(slides, ensure_ascii=False)})


def assemble_youtube(raw: dict, angle: str, ctx: ContentContext) -> Post:
    title = _s(raw, "title") or ctx.clip.title
    if len(title) > 100:
        title = title[:100].rsplit(" ", 1)[0]
    tags = norm_tags(["Shorts", *(raw.get("hashtags") or [])], 5)
    desc = "\n\n".join(x for x in [_s(raw, "description"), _credit(ctx), " ".join(tags)] if x)
    search_tags = [str(t).strip() for t in raw.get("tags") or [] if str(t).strip()][:15]
    return Post(platform="youtube", angle=angle, title=title, text=desc, hashtags=tags,
                extra={"tags": search_tags, "pinned_comment": _s(raw, "pinned_comment")})


def assemble_linkedin(raw: dict, angle: str, ctx: ContentContext) -> Post:
    tags = norm_tags(raw.get("hashtags"), 5)
    parts = [_s(raw, "hook"), _s(raw, "body"), _s(raw, "takeaway"), _s(raw, "question"), _credit(ctx), " ".join(tags)]
    return Post(platform="linkedin", angle=angle, text="\n\n".join(p for p in parts if p)[:3000], hashtags=tags)


ASSEMBLERS = {"x": assemble_x, "instagram": assemble_instagram,
              "youtube": assemble_youtube, "linkedin": assemble_linkedin}


def opener(post: Post) -> str:
    first = (post.title if post.platform == "youtube" else post.text).strip().split("\n", 1)[0]
    return first[:80]


# ------------------------------------------------------------------ generation

def generate_post(platform: str, ctx: ContentContext, llm: LLM, history: ContentHistory,
                  job_dir: Path, exclude_angle: str | None = None) -> Post:
    angle = history.pick_angle(platform, list(ANGLES[platform]), {exclude_angle} if exclude_angle else None)
    prompt = build_prompt(platform, angle, ctx, history.recent_openers(platform))
    raw = llm.chat_json(prompt, SCHEMAS[platform], temperature=settings.content_temperature,
                        think=settings.content_think)
    post = ASSEMBLERS[platform](raw, angle, ctx)
    post.angle_label = ANGLES[platform][angle][0]
    if not post.text.strip() and not post.title:
        raise ValueError(f"{platform}: model returned empty content")

    if platform == "instagram" and settings.carousel_enabled:
        from .carousel import render_carousel

        slides = json.loads(post.extra.get("slides", "[]"))
        if slides:
            post.carousel = render_carousel(slides, job_dir, f"short_{ctx.clip.index}_ig")
    history.record(platform, angle, opener(post))
    return post


def generate_all(ctx: ContentContext, llm: LLM, history: ContentHistory, job_dir: Path,
                 on_progress: ProgressFn | None = None) -> dict[str, Post]:
    out: dict[str, Post] = {}
    plats = [p for p in settings.content_platforms if p in ANGLES]
    for i, p in enumerate(plats):
        if on_progress:
            on_progress(i / len(plats), f"Writing {NAMES[p]} post")
        try:
            out[p] = generate_post(p, ctx, llm, history, job_dir)
        except Exception as e:  # one platform failing must not kill the job
            log.warning("content for %s failed: %s", p, e)
    return out


def write_posts_md(clip: Clip, job_dir: Path) -> Path:
    lines = [f"# Short {clip.index}: {clip.title}", ""]
    for p in ("youtube", "instagram", "x", "linkedin"):
        post = clip.content.get(p)
        if not post:
            continue
        lines += [f"## {NAMES[p]} — angle: {post.angle_label or post.angle}", ""]
        if post.title:
            lines += [f"**Title:** {post.title}", ""]
        if p == "x" and len(post.parts) > 1:
            lines += [f"**{i}/{len(post.parts)}** {t}\n" for i, t in enumerate(post.parts, 1)]
        else:
            lines += [post.text, ""]
        if post.extra.get("pinned_comment"):
            lines += [f"**Pinned comment:** {post.extra['pinned_comment']}", ""]
        if post.extra.get("tags"):
            lines += [f"**Tags:** {', '.join(post.extra['tags'])}", ""]
        if post.extra.get("cover_text"):
            lines += [f"**Reel cover text:** {post.extra['cover_text']}", ""]
        if post.carousel:
            lines += ["**Carousel:** " + ", ".join(post.carousel), ""]
    path = job_dir / f"short_{clip.index}.posts.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
