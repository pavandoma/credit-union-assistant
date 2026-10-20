"""Rates agent: deposit, certificate and loan rates. Keeps APR, APY and dividend rate apart,
and keeps tiers, terms, loan amounts, vehicle years and minimum deposits as conditions."""
from __future__ import annotations

import re

from ..pageparse import Block
from ..records import Fact
from ..text import parse_dates
from .common import EFFECTIVE, NUM_CELL, PageCtx, make_chunk

RATE_HEADER = re.compile(r"apy|apr|yield|rate|dividend", re.I)
COND_CELL = re.compile(r"^[\$<>(]|^\d[\d,]*(?:\.\d+)?\s*(?:[-–—+]|or\b|to\b|$)|^(?:up to|over|under|less|more)\b", re.I)
COND_HEAD = re.compile(r"tier|balance|term|amount|month|year|period|range|loan size|vehicle|model", re.I)
ENTITY_HEAD = re.compile(r"program|product|account|name|certificate|plan|card|ira|service|item|description|fee|type", re.I)
PLACEHOLDER = re.compile(r"^[—–\-]+$|^n/?a$|^none$", re.I)
MONEY_HEADER = re.compile(r"min|payment|fee|amount|balance|cost|price", re.I)
RATE_SENT = re.compile(r"\d+(?:\.\d+)?\s*%", re.I)
RATE_WORD = re.compile(r"\b(apy|apr|dividend rate|interest rate|rate|yield)\b", re.I)
TEXT_FACT = re.compile(
    r"(?P<ent>[A-Za-z0-9][A-Za-z0-9 \-/&']{2,70}?)\s+(?P<attr>APY|APR|dividend rate|interest rate|rate)\s*"
    r"(?:is|of|at|:|=|-|–)?\s*(?P<val>\d{1,3}(?:\.\d+)?)\s*%",
    re.I,
)


def attr_for_header(h: str, cell: str = "") -> str:
    hl = h.lower()
    cl = cell.upper()
    if "APR" in cl:
        return "APR"
    if "APY" in cl:
        return "APY"
    if "apy" in hl or "percentage yield" in hl:
        return "APY"
    if "apr" in hl or "percentage rate" in hl:
        return "APR"
    if "dividend" in hl:
        return "dividend rate"
    if "min" in hl and "open" in hl:
        return "minimum to open"
    if "min" in hl and "earn" in hl:
        return "minimum to earn"
    if "payment" in hl:
        return "monthly payment"
    if "rate" in hl:
        return "interest rate"
    return hl.strip() or "value"


def is_rate_table(b: Block) -> bool:
    if any(RATE_HEADER.search(h) for h in b.header):
        return True
    hits = sum(1 for r in b.rows for c in r if re.search(r"\d\s*%", c))
    return hits >= max(2, len(b.rows))


def effective_after(blocks: list[Block], idx: int) -> str | None:
    for b in blocks[idx + 1: idx + 4]:
        if b.kind == "table":
            break
        m = EFFECTIVE.search(b.text)
        if m:
            ds = parse_dates(m.group(1))
            if ds:
                return ds[0]
    return None


def fmt_val(cell: str) -> tuple[str, str]:
    m = NUM_CELL.match(cell)
    if not m:
        return cell, ""
    num = re.sub(r"\s+", "", m.group(1))
    if re.search(r"\d[-–—]\$?\d", num):          # a range such as 12.50%-19.50%
        parts = re.split(r"[-–—]", num)
        pct = "%" if m.group(2) else ""
        parts = [p if p.endswith("%") or not pct else p + pct for p in parts]
        return "–".join(parts), pct or ("$" if num.startswith("$") else "")
    unit = "%" if m.group(2) else ("$" if num.startswith("$") else "")
    return (num + ("%" if m.group(2) else "")), unit


OPEN_LINK = re.compile(r"^(?:open|apply for|start|join|learn more about)(?: my| a| an| the)? (.{3,60}?)(?: account| application)?$",
                       re.I)


def link_entity(blocks: list[Block], idx: int) -> str | None:
    """A table without a product column is usually followed by a link such as 'Open my 1st Rate Checking Account'."""
    for b2 in blocks[idx + 1: idx + 6]:
        if b2.kind == "table":
            break
        for anchor, _url in b2.links:
            m = OPEN_LINK.match(anchor.strip())
            if m:
                return m.group(1).strip()
    return None


def table_chunks(b: Block, blocks: list[Block], idx: int, ctx: PageCtx, group: int, rate_mode: bool):
    """Turn a table into one chunk per row, each carrying structured facts."""
    caps = [c for c in b.captions if c]
    caption = " > ".join(dict.fromkeys(caps))      # used for search
    shown_cap = caps[-1] if caps else ""
    eff = effective_after(blocks, idx) if rate_mode else None
    out = []
    hdr = [h.strip() for h in b.header]
    for r_i, row in enumerate(b.rows):
        cells = row + [""] * (len(hdr) - len(row))
        label_cells, value_cells = [], []
        for h, c in zip(hdr, cells):
            c = c.strip()
            if not c:
                continue
            numeric = bool(NUM_CELL.match(c))
            if COND_HEAD.search(h) and not RATE_HEADER.search(h):     # tier, balance, term: a condition
                label_cells.append((h, c))
                continue
            ratecol = bool(RATE_HEADER.search(h)) or (MONEY_HEADER.search(h) and numeric)
            if numeric and (ratecol or "%" in c):
                value_cells.append((h, c))
            else:
                label_cells.append((h, c))
        value_cells = [(h, c) for h, c in value_cells if not PLACEHOLDER.match(c)]
        if not label_cells or not value_cells:
            # keep row as a plain passage
            txt = "; ".join(f"{h}: {c}" for h, c in zip(hdr, cells) if c.strip())
            if txt:
                out.append(make_chunk(ctx, "row", f"{shown_cap}: {txt}" if shown_cap else txt, caption, group=group,
                                      order=b.order * 1000 + r_i, effective=eff))
            continue
        entity, conds = None, {}
        for h, c in label_cells:
            if PLACEHOLDER.match(c):
                continue
            if entity is None and ENTITY_HEAD.search(h) and not COND_HEAD.search(h):
                entity = c
            elif COND_HEAD.search(h) or COND_CELL.match(c):
                conds[h] = c
            elif entity is None:
                entity = c
            else:
                conds[h] = c
        if entity is None:
            last_cap = [c for c in b.captions if c]
            entity = link_entity(blocks, idx) or (last_cap[-1] if last_cap else (ctx.title or "item"))
        facts, parts = [], []
        attrs = [attr_for_header(h, c) for h, c in value_cells]
        for (h, c), attr in zip(value_cells, attrs):
            val, unit = fmt_val(c)
            if attrs.count(attr) > 1:       # two columns with the same meaning: keep the column name
                attr = re.sub(r"\s+", " ", h.lower()).strip() or attr
            facts.append(Fact(entity=entity, attribute=attr, value=val, unit=unit, conditions=dict(conds),
                              url=ctx.url, quote=f"{entity} | " + " | ".join(f"{k}: {v}" for k, v in conds.items())
                              + f" | {h}: {c}", effective=eff, kind="table", page_type=ctx.ptype))
            parts.append(f"{attr} {val}")
        cond_txt = ", ".join(f"{v}" for v in conds.values())
        dup = shown_cap and (shown_cap.lower() in entity.lower() or entity.lower() in shown_cap.lower())
        text = f"{shown_cap + ': ' if shown_cap and not dup else ''}{entity}"
        if cond_txt:
            text += f" ({cond_txt})"
        text += ": " + "; ".join(parts)
        if eff:
            text += f" [effective {eff}]"
        ch = make_chunk(ctx, "row", text, caption, facts=facts, group=group, order=b.order * 1000 + r_i,
                        effective=eff)
        out.append(ch)
    return out


def text_rate_facts(text: str, ctx: PageCtx) -> list[Fact]:
    """Facts from lines such as '6-month certificate APY: 3.50%'."""
    facts = []
    for m in TEXT_FACT.finditer(text):
        ent = m.group("ent").strip(" -:,")
        ent = re.sub(r"^(?:the|our|a|an|and|with|at|is|was|now|currently)\s+", "", ent, flags=re.I)
        if len(ent) < 3:
            continue
        attr = m.group("attr").upper() if m.group("attr").lower() in ("apy", "apr") else m.group("attr").lower()
        if attr == "rate":
            attr = "interest rate"
        facts.append(Fact(entity=ent, attribute=attr, value=m.group("val") + "%", unit="%", url=ctx.url,
                          quote=text[max(0, m.start()): m.end() + 40], kind="text", page_type=ctx.ptype))
    return facts


class RatesAgent:
    name = "rates"

    def claims_text(self, b: Block) -> bool:
        return bool(RATE_SENT.search(b.text)) and bool(RATE_WORD.search(b.text))

    def table(self, b, blocks, idx, ctx, group):
        return table_chunks(b, blocks, idx, ctx, group, rate_mode=True)

    def text(self, b: Block, ctx: PageCtx, windows_: list[str]):
        out = []
        for w in windows_:
            facts = text_rate_facts(w, ctx)
            ch = make_chunk(ctx, b.kind, w, " > ".join(b.captions), facts=facts, links=b.links, order=b.order * 1000)
            if facts:
                ds = parse_dates(w)
                ch.effective = ds[-1] if ds and ch.expiry is None and "effective" in w.lower() else None
            out.append(ch)
        return out
