"""Spoken numbers in a transcript → values a stat card can count up to.

Reads what Whisper wrote, in whatever form it chose: digits ("40%", "$2.5", "2,50,000", "1.5x", "10k"),
English words ("fifty percent", "two hundred and fifty", "one point five"), common Hinglish words ("das hazaar",
"pachaas"), Devanagari digits ("५०"), with currency (₹ Rs $ € £, "rupees", "dollars") and magnitudes (thousand,
million, billion, lakh, crore). The display keeps how it was said: "2.5 lakh" stays 2.5 lakh, "fifty percent"
becomes 50%.

Skipped (not a stat): years (1900-2100 with nothing attached), ranges and fractions ("3-4", "24/7", "3 to 4"),
clock times ("5:30"), ordinals ("1st"), and small bare numbers ("one", "3 things") with no unit.
"""
import re
from dataclasses import dataclass

from ..models import Word

_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_EDGE = "\"'“”‘’()[]¿¡…"
_TRAIL = ".,!?;:"

ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    # Hinglish (Latin, as hinglish.py / Whisper write them). Left out on purpose: "do" (2) and "saath" (60 / "with")
    "ek": 1, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6, "saat": 7,
    "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "baarah": 12, "pandrah": 15, "bees": 20,
    "pachees": 25, "pachchis": 25, "tees": 30, "chalis": 40, "chaalis": 40, "pachaas": 50, "pachas": 50,
    "sattar": 70, "assi": 80, "nabbe": 90,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
        "ninety": 90}
HUNDRED = {"hundred", "sau"}
THOUSAND = {"thousand", "hazaar", "hazar", "hajar", "hajaar"}
# magnitudes kept as words/letters in the display (multiplied in for thousand only)
BIG = {"million": "M", "millions": "M", "billion": "B", "billions": "B", "trillion": "T",
       "lakh": " lakh", "lakhs": " lakh", "lac": " lakh", "crore": " crore", "crores": " crore", "karod": " crore",
       "karor": " crore"}
INLINE_BIG = {"k": "K", "m": "M", "mn": "M", "b": "B", "bn": "B", "cr": " crore", "l": " lakh"}
CURRENCY = {"₹": "₹", "$": "$", "€": "€", "£": "£"}
CURRENCY_BEFORE = {"rs": "₹", "rs.": "₹", "inr": "₹", "usd": "$"}
CURRENCY_AFTER = {"rupees": "₹", "rupee": "₹", "rupaye": "₹", "rupaiye": "₹", "rupay": "₹", "dollars": "$",
                  "dollar": "$", "bucks": "$", "euros": "€", "euro": "€"}
PERCENT = {"%", "percent", "percentage", "pc", "pratishat", "fisadi"}
TIMES = {"times", "x", "guna"}
RANGE = {"to", "-", "–", "se", "or"}  # "3 to 4", "das se bees"
SMALL = 10  # bare numbers below this (no unit) aren't stats: "3 things", "ek baar"

_DIGITS = re.compile(r"^([₹$€£]?)((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?)(%|x|k|m|mn|b|bn|cr|l)?$", re.I)
_INDIAN = re.compile(r"^\d{1,2}(,\d{2})+,\d{3}$")


@dataclass
class Num:
    start: int  # word index of the first word (in the list given to parse)
    end: int  # one past the last word
    value: float
    decimals: int = 0
    prefix: str = ""  # currency symbol
    suffix: str = ""  # % / x / K / M / " lakh" / " crore"
    grouping: str = "western"  # western (1,234,567) | indian (12,34,567)
    spoken: str = ""  # the words as transcribed

    @property
    def has_unit(self) -> bool:
        return bool(self.prefix or self.suffix)

    def text(self, value: float | None = None) -> str:
        """Display text: the final value, or `value` (a count-up frame) in the same format."""
        return f"{self.prefix}{fmt(self.value if value is None else value, self.decimals, self.grouping)}{self.suffix}"


def fmt(value: float, decimals: int = 0, grouping: str = "western") -> str:
    if decimals:
        return f"{value:.{decimals}f}"
    n = int(round(value))
    if n < 1000 or grouping == "none":
        return str(n)
    if grouping == "indian":
        s = str(n)
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        return ",".join([*parts, tail])
    return f"{n:,}"


def clean(token: str) -> str:
    """A transcript word without spaces / quotes / trailing punctuation; Devanagari digits → 0-9."""
    return token.strip().strip(_EDGE).translate(_DEVANAGARI).rstrip(_TRAIL).strip(_EDGE)


def _key(token: str) -> str:
    return clean(token).lower()


def _words_value(keys: list[str], i: int) -> tuple[float, int, int] | None:
    """English/Hinglish number words from keys[i] → (value, decimals, words used)."""
    total, cur, j, used = 0, 0, i, False
    while j < len(keys):
        k = keys[j]
        a, _, b = k.partition("-")  # "twenty-five"
        if b and a in TENS and b in ONES and ONES[b] < 10:
            cur += TENS[a] + ONES[b]
        elif k in ONES:
            cur += ONES[k]
        elif k in TENS:
            cur += TENS[k]
        elif k in HUNDRED and (cur or used):
            cur = (cur or 1) * 100
        elif k in THOUSAND and (cur or total or used):
            total += (cur or 1) * 1000
            cur = 0
        elif k == "and" and used and j + 1 < len(keys) and (keys[j + 1] in ONES or keys[j + 1] in TENS):
            pass
        else:
            break
        used = True
        j += 1
    if not used:
        return None
    value, decimals = total + cur, 0
    if j + 1 < len(keys) and keys[j] == "point" and keys[j + 1] in ONES and ONES[keys[j + 1]] < 10:
        frac, j = "", j + 1
        while j < len(keys) and keys[j] in ONES and ONES[keys[j]] < 10:
            frac += str(ONES[keys[j]])
            j += 1
        value, decimals = float(f"{value}.{frac}"), len(frac)
    return float(value), decimals, j - i


def _is_number(key: str, raw: str) -> bool:
    return bool(_DIGITS.match(raw)) or key in ONES or key in TENS


def parse(words: list[Word], i: int) -> Num | None:
    """The number starting at words[i] (it may span several words), or None."""
    keys = [_key(w.word) for w in words]
    raw = [clean(w.word) for w in words]
    if i >= len(keys) or not keys[i]:
        return None
    if i >= 2 and keys[i - 1] in RANGE and _is_number(keys[i - 2], raw[i - 2]):
        return None  # the second half of a range
    j, prefix, suffix, grouping, digits = i, "", "", "western", False
    if keys[j] in CURRENCY_BEFORE and j + 1 < len(keys):
        prefix = CURRENCY_BEFORE[keys[j]]
        j += 1
    if re.search(r"\d[-/:]\d|\d(st|nd|rd|th)$", keys[j]):
        return None
    m = _DIGITS.match(raw[j])
    if m:
        sym, num, tail = m.group(1), m.group(2), (m.group(3) or "").lower()
        prefix = CURRENCY.get(sym, prefix)
        if _INDIAN.match(num):
            grouping = "indian"
        value = float(num.replace(",", ""))
        decimals = len(num.split(".")[1]) if "." in num else 0
        if tail == "%":
            suffix = "%"
        elif tail == "x":
            suffix = "x"
        elif tail:
            suffix = INLINE_BIG[tail]
        j += 1
        digits = True
    else:
        got = _words_value(keys, j)
        if not got:
            return None
        value, decimals, n = got
        j += n
    # magnitude / unit words after the number
    if j < len(keys) and keys[j] in THOUSAND and not suffix:
        value *= 1000
        j += 1
    if j < len(keys) and keys[j] in BIG and not suffix:
        suffix = BIG[keys[j]]
        j += 1
    if j < len(keys) and keys[j] in CURRENCY_AFTER and not prefix:
        prefix = CURRENCY_AFTER[keys[j]]
        j += 1
    if j < len(keys) and not suffix:
        if keys[j] in PERCENT:
            suffix = "%"
            j += 1
        elif keys[j] == "per" and j + 1 < len(keys) and keys[j + 1] == "cent":
            suffix, j = "%", j + 2
        elif keys[j] in TIMES and keys[j] != "x" or keys[j] == "x" and digits:
            suffix = "x"
            j += 1
    # ranges: "3 to 4", "10 - 20", "teen se chaar"
    if j + 1 < len(keys) and keys[j] in RANGE and _is_number(keys[j + 1], raw[j + 1]):
        return None
    num = Num(start=i, end=j, value=value, decimals=decimals, prefix=prefix, suffix=suffix, grouping=grouping,
              spoken=" ".join(w.word.strip() for w in words[i:j]))
    if not num.has_unit:
        if digits and not decimals and 1900 <= value <= 2100 and "," not in raw[i]:
            return None  # a year
        if value < SMALL:
            return None
    return num


def glue(words: list[Word]) -> tuple[list[Word], list[int]]:
    """Whisper splits some numbers into pieces with no space before them (" 99", ".99", "%"): pieces that continue
    a number are joined to it. Returns the joined words and, for each, the index of its first original word."""
    out: list[Word] = []
    first: list[int] = []
    for i, w in enumerate(words):
        prev = out[-1].word.rstrip() if out else ""
        if prev[-1:].isdigit() and not w.word[:1].isspace() and w.word[:1] in ".,%0123456789":
            out[-1] = out[-1].model_copy(update={"word": out[-1].word + w.word, "end": w.end})
        else:
            out.append(w)
            first.append(i)
    return out, first


def find(words: list[Word]) -> list[Num]:
    """Every number in `words`, left to right, without overlaps. start/end index `words` (not the glued pieces)."""
    joined, first = glue(words)
    out, i = [], 0
    while i < len(joined):
        n = parse(joined, i)
        if n:
            i = n.end  # next joined word
            a, b = first[n.start], first[n.end] if n.end < len(first) else len(words)
            n.start, n.end = a, b
            n.spoken = "".join(w.word for w in words[a:b]).strip()
            out.append(n)
        else:
            i += 1
    return out
