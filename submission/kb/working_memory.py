"""Working memory for chat follow-ups (used by the local UI, not by the scorer).

A member often asks a short follow-up: "What about 72 months?" or "And the jumbo one?". This module joins
the follow-up to the previous question. If the follow-up names a new term, vehicle year, amount, date or rate
type, the old value of that condition is removed first, so the two never conflict. The UI shows the joined
question ("Read as: ...") so the member can see how the follow-up was understood."""
from __future__ import annotations

import re

from .answer import EXTRA_STOP, FILLER, GENERIC_Q, clean_question
from .text import STOP, tokens

FOLLOW_UP = re.compile(r"^\s*(?:and|also|ok(?:ay)?,?\s*and|then|what about|how about|and what about|what if|"
                       r"same (?:for|but)|and for|for the|for a|for an|and the|and a)\b", re.I)
PRONOUN = re.compile(r"\b(?:it|its|it's|that|this one|that one|those|them|they|the same)\b", re.I)

# condition types: when the follow-up names one, the previous question's value of it is dropped
CONDITIONS = {                               # order matters: a full date goes before a bare year
    "date": re.compile(r"\b(?:as of|in|during|on|for)?\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
                       r"[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", re.I),
    "term": re.compile(r"\b\d{1,3}\s*[- ]?\s*(?:months?|mos?|years?|yrs?)\b|\b(?:one|two|three|four|five|six|seven|"
                       r"eight|nine|ten|twelve|eighteen|twenty|thirty)[- ](?:months?|years?)\b", re.I),
    "year": re.compile(r"\b(?:19|20)\d\d\b(?:\s*(?:model[- ]year|vehicle|car))?", re.I),
    "amount": re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s*[km]?\b|\b\d+(?:\.\d+)?\s*k\b", re.I),
    "measure": re.compile(r"\b(?:apr|apy|dividend rate|interest rate)\b", re.I),
}


# attribute words (stemmed): a follow-up that names a new one asks about the same subject ("What are its hours?")
ATTRIBUTES = frozenset("address location hour open close phone fax fee cost charge rate apr apy dividend minimum "
                       "maximum limit term requirement eligibility eligible penalty routing".split())
POINTER = re.compile(r"\b(?:its|it's|it|that one|this one|that|this|they|them|those|the same)\b", re.I)


def _subject_words(q: str) -> list:
    return [t for t in tokens(q) if len(t) > 1 and not t[0].isdigit()
            and t not in STOP and t not in EXTRA_STOP and t not in GENERIC_Q and t not in FILLER]


def _subject_phrase(prev: str) -> str:
    """The words of the previous question that name its subject: "the address of the Galena Branch" -> "Galena Branch"."""
    keep = set(_subject_words(prev)) - ATTRIBUTES - {"earn", "pay", "paid", "offer", "apply", "see", "find"}
    out = []
    for w in prev.split():
        toks = set(tokens(w))
        if toks and not toks & ATTRIBUTES and (toks & keep or any(t[0].isdigit() for t in toks)):
            out.append(w.strip("?.,!;:'\""))
    return " ".join(out)


def is_follow_up(question: str) -> bool:
    q = clean_question(question)
    if not q:
        return False
    if FOLLOW_UP.match(q):
        return True
    words = _subject_words(q)
    if not words:
        return True                         # "72 months?", "and in May 2026?"
    return bool(PRONOUN.search(q)) and len(q.split()) <= 7


def resolve(question: str, previous: str | None) -> tuple[str, bool]:
    """Returns (question to answer, True if the previous question was used)."""
    q = clean_question(question)
    prev = clean_question(previous or "")
    if not q or not prev or not is_follow_up(q):
        return q, False
    new_attrs = (set(tokens(q)) & ATTRIBUTES) - set(tokens(prev))
    subject = _subject_phrase(prev)
    if new_attrs and subject:
        # a new attribute of the same subject: the pointer word becomes the subject ("its hours" -> "the X hours")
        if POINTER.search(q):
            joined = POINTER.sub(" ", POINTER.sub(f"the {subject}", q, count=1))   # subject has no pointer words
        else:
            tail = FOLLOW_UP.sub("", q).strip(" ,?.")
            tail = re.sub(r"\b(?:the|a|an|one|ones)\b", " ", tail, flags=re.I)
            joined = f"{tail} for the {subject}"
        return re.sub(r"\s+", " ", joined).strip()[:1000], True
    base = prev
    for name, pattern in CONDITIONS.items():
        if pattern.search(q):
            base = pattern.sub(" ", base)
    tail = FOLLOW_UP.sub("", q).strip(" ,?.") or q
    # words that only point back ("the jumbo one", "its rate") carry no meaning of their own
    tail = re.sub(r"\b(?:the|a|an|one|ones|it|its|it's|that|this|those|them|they)\b", " ", tail, flags=re.I)
    base = re.sub(r"\b(?:as of|in|during|on|for)\s*(?=[?.]?\s*$)", " ", base.rstrip(" ?."), flags=re.I)
    joined = re.sub(r"\s+", " ", f"{base.rstrip(' ?.')} {tail}").strip()
    joined = re.sub(r"\b([Aa]) (?=[aeiouAEIOU])", r"\1n ", joined)   # "a 60-month auto loan" -> "an auto loan"
    return joined[:1000], True
