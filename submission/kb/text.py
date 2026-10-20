"""Text helpers: tokens, number words, dates. Pure standard library."""
from __future__ import annotations

import re
from functools import lru_cache
from datetime import date

_WORDNUM = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "eighteen": "18", "twenty": "20", "thirty": "30", "forty": "40", "sixty": "60",
}
_TOK = re.compile(r"\d+(?:\.\d+)?|[a-z]+")
_COMMA_NUM = re.compile(r"(?<=\d),(?=\d{3}\b)")

STOP = frozenset(
    "a an the of to in on for and or is are was were be been do does did can could would should "
    "what whats which who whom how many much when where why i me my we our us you your it its "
    "this that these those there here with at by from as about any all tell give get have has had "
    "please like would want need know dupaco right now currently current today also if then than "
    "so up out into over under not no yes s t doe".split()
)


def norm_num(tok: str) -> str:
    if "." in tok:
        tok = tok.rstrip("0").rstrip(".")
    return tok or "0"


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and not w.endswith("us"):
        return w[:-1]
    return w


@lru_cache(maxsize=1 << 16)
def _word(t: str) -> str:
    return norm_num(t) if t[0].isdigit() else (_WORDNUM.get(t) or stem(t))


@lru_cache(maxsize=1 << 15)
def _tokens_cached(s: str) -> tuple:
    return tuple(_word(t) for t in _TOK.findall(_COMMA_NUM.sub("", s.lower())))


def tokens(s: str) -> list[str]:
    """Lower-cased, stemmed word and number tokens. Paragraphs repeat across snapshots, so results are cached."""
    s = s or ""
    if len(s) <= 4000:
        return list(_tokens_cached(s))
    return [_word(t) for t in _TOK.findall(_COMMA_NUM.sub("", s.lower()))]


def content_tokens(s: str) -> list[str]:
    return [t for t in tokens(s) if t not in STOP]


def key_tokens(s: str) -> tuple[str, ...]:
    """Order-free, stopword-free signature used to match entities across pages."""
    return tuple(sorted(set(content_tokens(s))))


_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split())}
_MON3 = {k[:3]: v for k, v in _MONTHS.items()}
_MON3["sept"] = 9

_RE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_RE_LONG = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_RE_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse_dates(s: str) -> list[str]:
    """All full dates found in s, as ISO strings, in order of appearance."""
    found: list[tuple[int, str]] = []
    for m in _RE_ISO.finditer(s):
        d = _mk(*m.groups())
        if d:
            found.append((m.start(), d))
    for m in _RE_US.finditer(s):
        mo, dd, y = m.groups()
        y = int(y) + 2000 if len(y) == 2 else int(y)
        d = _mk(y, mo, dd)
        if d:
            found.append((m.start(), d))
    for m in _RE_LONG.finditer(s):
        mon = m.group(1).lower()
        mo = _MONTHS.get(mon) or _MON3.get(mon[:3] if mon != "sept" else "sept")
        if mo:
            d = _mk(m.group(3), mo, m.group(2))
            if d:
                found.append((m.start(), d))
    found.sort()
    return [d for _, d in found]


def month_end(question: str) -> str | None:
    """'as of May 2026' / 'in March 2026' -> last day of that month (ISO)."""
    for m in _RE_MONTH_YEAR.finditer(question):
        mon = m.group(1).lower()
        mo = _MONTHS.get(mon) or (_MON3.get(mon[:3]) if len(mon) <= 4 or mon in _MONTHS else None)
        if not mo or mon in ("may",) and False:
            continue
        y = int(m.group(2))
        if not 2000 <= y <= 2100:
            continue
        nxt = date(y + (mo == 12), mo % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1).isoformat()
    return None
