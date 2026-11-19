from app.config import settings
from app.models import Word
from app.pipeline.captions import build_ass, build_srt, keyword_set, ts_ass, ts_srt
from app.pipeline.highlights import clean_keywords

WORDS = [Word(start=i * 0.5, end=i * 0.5 + 0.4, word=f" {w}") for i, w in
         enumerate(["Invest", "500", "rupees", "every", "month", "and", "relax"])]


def test_timestamps():
    assert ts_srt(3661.5) == "01:01:01,500"
    assert ts_ass(61.234) == "0:01:01.23"


def test_srt_chunks_and_bridging():
    srt = build_srt(WORDS, n=3)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 3
    assert blocks[0].splitlines()[2] == "INVEST 500 RUPEES"
    # gap between chunks (<0.6 s) is bridged
    assert blocks[0].splitlines()[1].endswith(blocks[1].splitlines()[1].split(" --> ")[0])


def test_ass_one_event_per_word_with_highlight_and_keyword():
    ass = build_ass(WORDS, ["rupees"], n=3)
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == len(WORDS)
    first, second = events[0], events[1]
    assert rf"\c{settings.highlight_color}&" in first.split("INVEST")[0]  # active word yellow
    assert r"\t(0,90," in first and r"\t(0,90," not in second  # pop only when line appears
    assert rf"\c{settings.keyword_color}&\fscx100\fscy100}}RUPEES" in second
    third = events[2]  # numbers are always highlighted
    assert rf"\c{settings.keyword_color}&\fscx100\fscy100}}500" in third
    assert "PlayResY: 1920" in ass


def test_keyword_fallback_to_numbers():
    assert keyword_set([], WORDS) == {"500"}
    assert keyword_set(["Big Money"], WORDS) == {"big", "money", "500"}


def test_chunks_break_at_sentence_end():
    from app.pipeline.captions import chunk_words
    text = [" Use", " it.", " If", " you", " invest"]
    ws = [Word(start=i, end=i + 0.5, word=w) for i, w in enumerate(text)]
    got = [[w.word.strip() for w in c] for c in chunk_words(ws, 3)]
    assert got == [["Use", "it."], ["If", "you", "invest"]]


def test_clean_keywords():
    assert clean_keywords(["SIP", "sip", " ", "tax", "crash", "fund", "x"]) == ["sip", "tax", "crash", "fund"]
    assert clean_keywords(None) == []


def test_braces_are_stripped_from_text():
    ass = build_ass([Word(start=0, end=1, word=" {evil}\\N")], [])
    assert "{evil}" not in ass and "EVIL" in ass
