"""Shared record formats. Evidence (quote, url) is kept apart from interpretation (entity, attribute)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# Source authority by page type. Higher wins when two sources disagree about the same fact.
AUTHORITY = {
    "rates": 1.00,      # official rate sheets
    "product": 0.90,    # product pages
    "contact": 0.90,    # contact / locator page
    "home": 0.80,
    "info": 0.70,       # other site pages (member services, about, careers ...)
    "promo": 0.50,      # landing pages and promotions: can be expired
    "news": 0.45,       # newsroom releases: describe events on a date
    "document": 0.45,   # PDFs
    "calculator": 0.30,
    "blog": 0.35,       # old blog posts
}


def page_type(url: str) -> str:
    u = url.lower().split("#")[0].rstrip("/")
    path = re.sub(r"^https?://[^/]+", "", u)
    host = re.sub(r"^https?://", "", u).split("/")[0]
    if host.startswith("info.") or host.startswith("join."):
        return "info"
    if path in ("/rates", "/business-rates") or path.endswith("/rates") or path.startswith("/rates/"):
        return "rates"
    if path == "" or path == "/home":
        return "home"
    if path.startswith("/contact") or path.startswith("/connect/contact"):
        return "contact"
    if path.startswith("/newsroom"):
        return "news"
    if path.startswith("/learn/blog") or re.match(r"^/\d{4}/\d{2}/", path) or "/resources/" in path \
            or path.startswith("/business-resources") or path.startswith("/learn"):
        return "blog"
    if path.startswith("/calculators") or "calculator" in path:
        return "calculator"
    if path.startswith("/hubfs") or path.endswith(".pdf"):
        return "document"
    if path.endswith("-lp") or path in ("/switch-and-earn", "/earn-more") or "special" in path or "promo" in path:
        return "promo"
    if path.startswith("/personal") or path.startswith("/business") or path in ("/checking",):
        return "product"
    return "info"


@dataclass
class Fact:
    """One extracted statement with its conditions and evidence."""
    entity: str                       # product or entity, as written
    attribute: str                    # apy | apr | dividend rate | rate | fee | min to open | address | hours ...
    value: str                        # as written, e.g. "4.10%"
    unit: str = ""                    # "%", "$", ""
    conditions: dict = field(default_factory=dict)   # tier, term, balance, vehicle year ...
    url: str = ""
    quote: str = ""                   # source text that supports the value
    effective: Optional[str] = None   # ISO date if stated
    expiry: Optional[str] = None      # ISO date if stated
    status: str = "unchecked"         # unchecked | verified | unsupported
    kind: str = "table"               # table | text
    page_type: str = "info"

    def key(self) -> tuple:
        """Identity of a fact: its attribute plus the words that name the product and its conditions.
        The split between 'entity' and 'condition' differs from page to page, so the words are pooled."""
        k = getattr(self, "_key", None)
        if k is None:
            from .text import key_tokens
            words = key_tokens(self.entity + " " + " ".join(self.conditions.values()))
            words = tuple(w for w in words if w not in GENERIC_WORDS and not (len(w) == 1 and w.isdigit()))
            k = (self.attribute.lower(), words)
            object.__setattr__(self, "_key", k)
        return k


GENERIC_WORDS = frozenset("home loan account rate".split())


@dataclass
class Chunk:
    """One retrievable passage: a table row, a paragraph, a list item or a location."""
    cid: int = -1
    pk: int = -1                      # page key (url + content hash) the chunk belongs to
    url: str = ""
    title: str = ""
    page_type: str = "info"
    authority: float = 0.5
    kind: str = "para"                # row | para | list | branch
    text: str = ""                    # member-facing text
    context: str = ""                 # section captions, used for search only
    facts: list = field(default_factory=list)
    links: list = field(default_factory=list)       # [(anchor, url)]
    effective: Optional[str] = None
    expiry: Optional[str] = None
    stale_hint: bool = False          # text says offer ended / no longer available
    pubdate: Optional[str] = None     # publication date of a news or blog page, if the page shows one
    group: int = -1                   # table id, so rows of one table stay together
    order: int = 0                    # position in the page
