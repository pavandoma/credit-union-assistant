"""Shared helpers for the extraction agents."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..records import Chunk, Fact
from ..text import parse_dates

NUM_CELL = re.compile(
    r"^\s*(\$?\s*\d[\d,]*(?:\.\d+)?(?:\s*%?\s*[-–—]\s*\$?\s*\d[\d,]*(?:\.\d+)?)?)\s*(%)?\s*(APR|APY)?\s*$", re.I)
EFFECTIVE = re.compile(r"effective\s+(?:as\s+of\s+|on\s+|date:?\s*)?([^\s.;,)]+(?:\s+\d{1,2},?\s+\d{4})?)", re.I)
STALE = re.compile(
    r"\b(offer (?:has |had )?ended|(?:has|have) ended|promotion (?:has )?ended|expired|"
    r"no longer (?:available|offered|valid)|offer ended|ended on|ended \d)",
    re.I,
)
EXPIRY = re.compile(
    r"(?:through|thru|until|expires?(?: on)?|ends?(?: on)?|ended(?: on)?|valid (?:through|until)|by)\s+"
    r"((?:\d{4}-\d{2}-\d{2})|(?:\d{1,2}/\d{1,2}/\d{2,4})|(?:[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}))",
    re.I,
)


@dataclass
class PageCtx:
    url: str
    title: str
    ptype: str
    authority: float
    pubdate: str | None = None


def make_chunk(ctx: PageCtx, kind: str, text: str, context: str = "", facts=None, links=None,
               group: int = -1, order: int = 0, effective=None) -> Chunk:
    expiry = None
    m = EXPIRY.search(text)
    if m:
        ds = parse_dates(m.group(1))
        expiry = ds[0] if ds else None
    return Chunk(
        url=ctx.url, title=ctx.title, page_type=ctx.ptype, authority=ctx.authority, kind=kind,
        text=text, context=context, facts=facts or [], links=links or [], effective=effective,
        expiry=expiry, stale_hint=bool(STALE.search(text)), group=group, order=order, pubdate=ctx.pubdate,
    )


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$\"'(])", text)
    return [p.strip() for p in parts if p.strip()]


def windows(text: str, limit: int = 420) -> list[str]:
    """Split a long paragraph into overlapping sentence windows."""
    if len(text) <= limit * 1.3:
        return [text]
    sents = split_sentences(text)
    out, cur = [], []
    for s in sents:
        if cur and sum(len(x) + 1 for x in cur) + len(s) > limit:
            out.append(" ".join(cur))
            cur = cur[-1:]
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out
