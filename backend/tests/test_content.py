import csv
import io
import zipfile

import pytest

from app.config import settings
from app.models import Clip, Job, JobStatus
from app.pipeline.content import (
    ANGLES,
    ContentContext,
    assemble_x,
    assemble_youtube,
    build_prompt,
    generate_all,
    generate_post,
    norm_tags,
    split_post,
    write_posts_md,
)
from app.pipeline.export import build_pack
from app.pipeline.history import ContentHistory

from .conftest import SchemaFakeLLM


def _ctx():
    clip = Clip(index=1, start=0, end=30, title="The 8th wonder", hook="Most people miss this", score=9,
                keywords=["compound", "double"])
    return ContentContext(clip=clip, text="Compound interest is the 8th wonder of the world.",
                          video_title="Money Basics", channel="FinChannel", url="https://youtu.be/x")


# ---------------------------------------------------------------- platform limits

def test_split_post_respects_280_and_keeps_words():
    long = " ".join(["Compound interest rewards patience."] * 20)
    parts = split_post(long)
    assert len(parts) > 1 and all(len(p) <= 280 for p in parts)
    assert " ".join(parts).split() == long.split()


def test_norm_tags():
    assert norm_tags(["#Money", "money", "personal finance", "", "#AI!"], 5) == ["#Money", "#personalfinance", "#AI"]


def test_x_thread_vs_single():
    raw = {"posts": ["Hook:", "Point one.", "Ask?"], "hashtags": ["a", "b", "c"]}
    thread = assemble_x(raw, "thread", _ctx())
    assert len(thread.parts) == 3 and thread.parts[0].endswith("🧵")
    assert thread.parts[-1].endswith("#a #b") and "#c" not in thread.text  # max 2 hashtags
    single = assemble_x(raw, "hot_take", _ctx())
    assert len(single.parts) == 1 and "🧵" not in single.text


def test_x_does_not_duplicate_hashtags_the_model_already_wrote():
    # Seen with gemma4: the last thread post already ends with the hashtags it also returns in "hashtags"
    raw = {"posts": ["Hook:", "So, what matters more? #CareerTips #interviewtips"],
           "hashtags": ["CareerTips", "InterviewTips"]}
    post = assemble_x(raw, "thread", _ctx())
    assert post.parts[-1] == "So, what matters more? #CareerTips #interviewtips"
    assert post.text.lower().count("#careertips") == 1 and post.text.lower().count("#interviewtips") == 1

    raw["posts"][-1] = "So, what matters more? #CareerTips"  # one present, one missing → append only the missing
    assert assemble_x(raw, "thread", _ctx()).parts[-1] == "So, what matters more? #CareerTips #InterviewTips"

    raw["posts"][-1] = "So, what matters more? #CareerTipsDaily"  # a longer tag is not a match
    assert assemble_x(raw, "thread", _ctx()).parts[-1].endswith("#CareerTipsDaily #CareerTips #InterviewTips")


def test_youtube_adds_shorts_and_limits_title():
    post = assemble_youtube({"title": "word " * 40, "description": "d", "hashtags": ["money"], "tags": ["t"],
                             "pinned_comment": "q?"}, "number", _ctx())
    assert post.hashtags[0] == "#Shorts" and len(post.title) <= 100
    assert post.extra["pinned_comment"] == "q?"


def test_credit_line_only_when_enabled(monkeypatch):
    raw = {"title": "t", "description": "d", "hashtags": [], "tags": [], "pinned_comment": ""}
    assert "Clip from" not in assemble_youtube(raw, "number", _ctx()).text
    monkeypatch.setattr(settings, "credit_source", True)
    assert "🎥 Clip from: Money Basics — FinChannel https://youtu.be/x" in assemble_youtube(raw, "number", _ctx()).text


# ---------------------------------------------------------------- variety engine

def test_history_rotates_all_angles_before_repeating(tmp_path):
    h = ContentHistory(tmp_path / "h.json")
    angles = list(ANGLES["x"])
    used = []
    for _ in range(len(angles)):
        a = h.pick_angle("x", angles)
        h.record("x", a, f"opener {a}")
        used.append(a)
    assert sorted(used) == sorted(angles)  # every angle once
    h2 = ContentHistory(tmp_path / "h.json")  # persisted
    assert h2.pick_angle("x", angles) == used[0]  # least recently used comes back first
    assert h2.recent_openers("x")[-1] == f"opener {used[-1]}"


def test_exclude_angle(tmp_path):
    h = ContentHistory(tmp_path / "h.json")
    assert h.pick_angle("x", ["a", "b"], exclude={"a"}) == "b"


def test_prompt_has_angle_avoid_list_and_guardrails():
    p = build_prompt("linkedin", "framework", _ctx(), ["I almost ignored this clip"])
    assert "Framework" in p and "- I almost ignored this clip" in p
    assert "Never invent facts" in p and "Money Basics" in p and "FinChannel" in p


# ---------------------------------------------------------------- generation

def test_generate_all_platforms_with_carousel(tmp_path):
    llm = SchemaFakeLLM()
    h = ContentHistory(tmp_path / "h.json")
    posts = generate_all(_ctx(), llm, h, tmp_path)
    assert set(posts) == {"x", "instagram", "youtube", "linkedin"}
    ig = posts["instagram"]
    assert ig.text.startswith("POV:") and ig.hashtags == ["#money", "#investing"]
    assert len(ig.carousel) == 3 and all((tmp_path / f).exists() for f in ig.carousel)
    assert posts["youtube"].title.startswith("The 8th wonder")
    assert all(p.angle_label for p in posts.values())


def test_new_posts_avoid_previous_openers(tmp_path):
    llm, h = SchemaFakeLLM(), ContentHistory(tmp_path / "h.json")
    generate_post("linkedin", _ctx(), llm, h, tmp_path)
    generate_post("linkedin", _ctx(), llm, h, tmp_path)
    assert "I almost ignored this 30-second clip." in llm.prompts[-1]


def test_one_platform_failing_keeps_others(tmp_path):
    posts = generate_all(_ctx(), SchemaFakeLLM(fail_on="takeaway"), ContentHistory(tmp_path / "h.json"), tmp_path)
    assert "linkedin" not in posts and {"x", "instagram", "youtube"} <= set(posts)


def test_posts_md_and_export_pack(tmp_path):
    ctx = _ctx()
    ctx.clip.content = generate_all(ctx, SchemaFakeLLM(), ContentHistory(tmp_path / "h.json"), tmp_path)
    ctx.clip.file = "short_1.mp4"
    (tmp_path / "short_1.mp4").write_bytes(b"v")
    (tmp_path / "short_1.srt").write_text("1")
    md = write_posts_md(ctx.clip, tmp_path).read_text()
    assert "## YouTube Shorts" in md and "## X (Twitter)" in md and "**Pinned comment:**" in md

    job = Job(id="abc", source="x", status=JobStatus.done, clips=[ctx.clip])
    with zipfile.ZipFile(build_pack(job, tmp_path)) as z:
        names = set(z.namelist())
        assert {"shorts/short_1.mp4", "captions/short_1.srt", "posts/short_1.posts.md", "titles.csv"} <= names
        assert any(n.startswith("carousels/") for n in names)
        rows = list(csv.DictReader(io.StringIO(z.read("titles.csv").decode("utf-8-sig"))))
        assert rows[0]["youtube_title"].startswith("The 8th wonder")


@pytest.mark.parametrize("platform", list(ANGLES))
def test_every_angle_builds_a_prompt(platform):
    for angle in ANGLES[platform]:
        assert ANGLES[platform][angle][0] in build_prompt(platform, angle, _ctx(), [])
