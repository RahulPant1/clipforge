"""Hindi transcript in Devanagari → Hinglish (Latin letters), word by word, so every word keeps its timing.

Whisper writes Hindi speech in Devanagari. Most people read (and type) Hindi on social media in Latin letters,
and fixing a caption is far easier when you can type the word — so a job can turn its transcript into Hinglish:
captions, .srt, clip selection, posts and the caption editor all read transcript.json, so they all switch.
The Devanagari original is kept in transcript.native.json (back_to_native restores it).

Rule-based, no model: consonants + vowel signs map to the spelling people actually use ("aap", "nahin", "mein"),
with Hindi schwa deletion (कमला → kamla, not kamala; जब → jab). English words written in Devanagari come out
phonetic ("इंप्रेस" → "impres"): fix those once with Replace all.
"""
import json
import re
from pathlib import Path

from ..models import Segment
from . import transcript_edit

NATIVE = "transcript.native.json"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "ळ": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    # nukta letters (precomposed)
    "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "d", "ढ़": "dh", "फ़": "f", "य़": "y", "ऱ": "r", "ऩ": "n",
}
NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}  # base + ़
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
          "ऍ": "e", "ऑ": "o", "ओ": "o", "औ": "au"}
MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ॅ": "e",
          "ॉ": "o", "ो": "o", "ौ": "au"}
HALANT, NUKTA_SIGN, ANUSVARA, CHANDRABINDU, VISARGA = "्", "़", "ं", "ँ", "ः"
OTHER = {"।": ".", "॥": ".", "ॐ": "om", "ऽ": "", **{chr(0x0966 + d): str(d) for d in range(10)}}
LABIAL = ("p", "ph", "b", "bh", "m", "f")


class _Tok:
    __slots__ = ("kind", "base", "vowel", "inherent", "nasal")

    def __init__(self, kind: str, base: str = "", vowel: str = "", inherent: bool = False):
        self.kind, self.base, self.vowel, self.inherent, self.nasal = kind, base, vowel, inherent, False


def _parse(word: str) -> list:
    toks: list = []
    chars, i = list(word), 0
    while i < len(chars):
        ch = chars[i]
        if ch in CONS:
            base = CONS[ch]
            if i + 1 < len(chars) and chars[i + 1] == NUKTA_SIGN:
                base, i = NUKTA.get(ch, base), i + 1
            t = _Tok("C", base, "a", inherent=True)
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt == HALANT:
                t.vowel, t.inherent, i = "", False, i + 1
            elif nxt in MATRAS:
                t.vowel, t.inherent, i = MATRAS[nxt], False, i + 1
            toks.append(t)
        elif ch in VOWELS:
            toks.append(_Tok("V", "", VOWELS[ch]))
        elif ch in (ANUSVARA, CHANDRABINDU):
            if toks and isinstance(toks[-1], _Tok):
                toks[-1].nasal = True
            else:
                toks.append("n")
        elif ch == VISARGA:
            toks.append("h")
        elif ch == NUKTA_SIGN:
            pass
        else:
            toks.append(OTHER.get(ch, ch))  # punctuation, Latin, digits: as they are
        i += 1
    return toks


def _delete_schwas(run: list[_Tok]) -> None:
    """Hindi drops most inherent 'a's: always at the end of a word (jab, kismat), and in the middle in a
    vowel–C–[a]–C–vowel context, scanning right to left (samajhna, kamla, ladki)."""
    if len(run) > 1 and run[-1].kind == "C" and run[-1].inherent:
        run[-1].vowel = ""
    for k in range(len(run) - 2, 0, -1):
        t, prev, nxt = run[k], run[k - 1], run[k + 1]
        if t.kind == "C" and t.inherent and t.vowel and prev.vowel and nxt.kind == "C" and nxt.vowel:
            t.vowel = ""


def _vowel(t: _Tok, last: bool, mono: bool) -> str:
    v = t.vowel
    if not mono and not last and v in ("aa", "ee", "oo"):  # tumhara, zyada, sarkar (doubled only in baat, aap)
        return {"aa": "a", "ee": "i", "oo": "u"}[v]
    if last:  # word-final spellings people use: kya, ladki, majnu, mein, haan, hoon, nahin
        if v == "aa" and not t.nasal:
            return "a"
        if v == "ee":
            return "i"
        if v == "oo" and not t.nasal:
            return "u"
        if v == "e" and t.nasal and t.kind == "C":
            return "ei"
    return v


def _render(run: list[_Tok]) -> str:
    out = []
    mono = sum(bool(t.vowel) for t in run) == 1  # one-syllable words keep long vowels: baat, aap, pyaar, theek
    for k, t in enumerate(run):
        nxt = run[k + 1] if k + 1 < len(run) else None
        base = t.base
        if t.kind == "C" and not t.vowel:
            if base == "ph" and nxt is not None:
                base = "f"  # फ्र / फ्ल: English loans (fresh, flight); native ph-clusters are rare
            if base == "ch" and nxt is not None and nxt.base == "chh":
                base = "c"  # अच्छा → accha
            if base == "chh" and nxt is None:
                base = "ch"  # कुछ → kuch
        if base == "chh" and k and run[k - 1].base == "ch" and not run[k - 1].vowel:
            base = "ch"
        s = base + _vowel(t, nxt is None, mono)
        if t.nasal:
            s += "m" if nxt is not None and nxt.base in LABIAL else "n"
        out.append(s)
    return "".join(out)


def to_latin(text: str) -> str:
    """Transliterate every Devanagari run in `text`; anything else (spaces, punctuation, English) is kept."""
    if not DEVANAGARI.search(text or ""):
        return text
    out, run = [], []
    for t in _parse(text):
        if isinstance(t, _Tok):
            run.append(t)
            continue
        if run:
            _delete_schwas(run)
            out.append(_render(run))
            run = []
        out.append(t)
    if run:
        _delete_schwas(run)
        out.append(_render(run))
    return "".join(out)


def is_devanagari(segs: list[Segment]) -> bool:
    text = "".join(s.text for s in segs)
    return len(DEVANAGARI.findall(text)) > 0.3 * max(len(re.sub(r"\W", "", text)), 1)


def romanize(job_dir: Path) -> list[tuple[float, float]]:
    """transcript.json → Hinglish (keeps the Devanagari original once). Returns the changed spans ([] = nothing to
    do: not Devanagari, or already Latin)."""
    with transcript_edit._lock:
        segs = transcript_edit.load(job_dir)
        if not segs or not any(DEVANAGARI.search(s.text) for s in segs):
            return []
        native = job_dir / NATIVE
        if not native.exists():
            native.write_text(json.dumps([s.model_dump() for s in segs], ensure_ascii=False))
        spans = []
        for s in segs:
            for w in s.words:
                latin = to_latin(w.word)
                if latin != w.word:
                    w.word = latin
                    spans.append((w.start, w.end))
            transcript_edit.rebuild_text(s)
            if not s.words:
                s.text = to_latin(s.text)
        transcript_edit.save(job_dir, segs)
        return [(min(a for a, _ in spans), max(b for _, b in spans))] if spans else [(segs[0].start, segs[-1].end)]


def back_to_native(job_dir: Path) -> list[tuple[float, float]]:
    """Restore Whisper's Devanagari transcript (fixes made in Hinglish are lost — they were made in Latin)."""
    with transcript_edit._lock:
        native = job_dir / NATIVE
        if not native.exists():
            return []
        segs = [Segment(**s) for s in json.loads(native.read_text())]
        transcript_edit.save(job_dir, segs)
        native.unlink()
        return [(segs[0].start, segs[-1].end)] if segs else []


def state(job_dir: Path) -> str:
    """"hinglish" (converted, original kept) | "devanagari" (convertible) | "" (not a Devanagari transcript)."""
    if (job_dir / NATIVE).exists():
        return "hinglish"
    segs = transcript_edit.load(job_dir)
    return "devanagari" if segs and is_devanagari(segs) else ""
