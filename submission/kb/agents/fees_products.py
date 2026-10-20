"""Fees and products agent: fees, product lists, eligibility, account conditions, and any other
member information that is not a rate or a branch fact."""
from __future__ import annotations

import re

from ..pageparse import Block
from ..records import Fact
from .common import PageCtx, make_chunk, windows
from .rates import table_chunks

FEE_FACT = re.compile(
    r"(?P<ent>[A-Za-z][A-Za-z0-9 \-/&']{2,70}?)\s+(?:fee|charge|cost)s?\s*(?:is|of|:|=|-|–)?\s*"
    r"(?P<val>\$\s?\d[\d,]*(?:\.\d{1,2})?)",
    re.I,
)
FEE_FACT2 = re.compile(r"(?P<val>\$\s?\d[\d,]*(?:\.\d{1,2})?)\s+(?:fee|charge)\s+(?:for|to)\s+(?P<ent>[^.;,]{3,60})", re.I)
# Organisation-wide totals only: "It has over 600 employees and 22 branch office locations", "Dupaco's 23
# full-service branches", "serves residents in 118 counties". A count at the start of a sentence ("294 employees
# used ...") describes a group, not the total, so a lead word is required.
STAT = re.compile(r"\b(?:has|have|had|with|employs|employing|supported by|team of|operates|operating|serves|serving|"
                  r"and|in|its|our|dupaco['’]s)\s+(?:over|more than|nearly|about|approximately|almost|roughly|"
                  r"some)?\s*(\d[\d,]*)\+?\s+(?:[\w-]+\s+){0,2}?"
                  r"(branch office locations|branch locations|branches|employees|counties|members)\b", re.I)
STAT_BOUNDS = {"employees": (100, 100_000), "branch locations": (5, 300), "counties": (5, 1_000),
               "members": (1_000, 10_000_000)}
BUSINESS = re.compile(r"\bbusiness\b", re.I)


def fee_facts(text: str, ctx: PageCtx) -> list[Fact]:
    facts = []
    low = text.lower()
    # cheap keyword checks first: the full patterns are slow and most paragraphs contain neither
    has_stat = any(w in low for w in ("branch", "employees", "counties", "members"))
    has_fee = "$" in text and any(w in low for w in ("fee", "charge", "cost"))
    if not has_stat and not has_fee:
        return facts
    national = re.search(r"\bu\.\s?s\.|united states|nationwide|nationally|across the (?:country|nation)", text, re.I)
    for m in ([] if national or not has_stat else STAT.finditer(text)):     # a national statistic is not ours
        noun = m.group(2).lower()
        noun = "branch locations" if noun.startswith("branch") else noun
        num = m.group(1).rstrip(",")
        lo, hi = STAT_BOUNDS[noun]
        if not lo <= int(num.replace(",", "")) <= hi or re.fullmatch(r"(?:19|20)\d\d", num) \
                or ctx.ptype == "document":
            continue                                    # outside a plausible total, a year, or a report table
        year = re.search(r"(?:in|during|for)\s+(20\d\d)\b", text)
        facts.append(Fact(entity="Dupaco", attribute=noun, value=num, url=ctx.url,
                          conditions={"year": year.group(1)} if year else {},
                          quote=text[max(0, m.start() - 20): m.end() + 10], effective=ctx.pubdate, kind="text",
                          page_type=ctx.ptype))
    if not has_fee:
        return facts
    for m in FEE_FACT.finditer(text):
        ent = re.sub(r"^(?:the|our|a|an|and|with|is|there is|there's)\s+", "", m.group("ent").strip(" -:,"), flags=re.I)
        if len(ent) < 3:
            continue
        facts.append(Fact(entity=ent, attribute="fee", value=re.sub(r"\s", "", m.group("val")), unit="$",
                          url=ctx.url, quote=text[max(0, m.start()): m.end() + 30], kind="text",
                          page_type=ctx.ptype))
    for m in FEE_FACT2.finditer(text):
        facts.append(Fact(entity=m.group("ent").strip(), attribute="fee", value=re.sub(r"\s", "", m.group("val")),
                          unit="$", url=ctx.url, quote=text[max(0, m.start()): m.end() + 20], kind="text",
                          page_type=ctx.ptype))
    return facts


class FeesProductsAgent:
    name = "fees_products"

    def table(self, b, blocks, idx, ctx, group):
        return table_chunks(b, blocks, idx, ctx, group, rate_mode=False)

    def text(self, b: Block, ctx: PageCtx, windows_: list[str]):
        out = []
        for w in windows_:
            facts = fee_facts(w, ctx)
            if facts and BUSINESS.search(w + " " + ctx.url):
                for f in facts:
                    if f.attribute == "fee":            # business fees differ from personal fees; totals do not
                        f.conditions["segment"] = "business"
            out.append(make_chunk(ctx, b.kind, w, " > ".join(b.captions), facts=facts, links=b.links,
                                  order=b.order * 1000))
        return out
