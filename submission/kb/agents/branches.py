"""Branch agent: locations, contact details, service hours. Each fact is linked to its branch."""
from __future__ import annotations

import re

from ..pageparse import Block
from ..records import Fact
from .common import PageCtx, make_chunk

ADDRESS = re.compile(r"^\d+[\w .,'#&/-]*,\s*[A-Z]{2},?\s*\d{5}(?:-\d{4})?$")
TIME_RANGE = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|noon)\s*[-–—]\s*\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|noon)?", re.I)
HOURS_WORDS = re.compile(r"\b(lobby|drive[- ]?up|atm|hours|open|closed|mon(?:day)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", re.I)
PHONE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
SERVICE_WORDS = re.compile(r"(drive up|video teller|24-hour atm|atm|lobby|night depository)", re.I)
TYPE_LINE = re.compile(r"^(dupaco branch|surcharge-free atm|dupaco atm|co-op shared branch|shared branch|learning lab|"
                       r"live video teller)", re.I)


class BranchAgent:
    name = "branches"

    def scan_page(self, blocks: list[Block], ctx: PageCtx):
        """Locator entries: [name] / [type] / address / status / services. Returns (chunks, used block indexes)."""
        chunks, used = [], set()
        for i, b in enumerate(blocks):
            if b.kind != "para" or not ADDRESS.match(b.text.strip()):
                continue
            addr = b.text.strip().rstrip(",")
            name = typ = ""
            for j in range(i - 1, max(-1, i - 4), -1):
                t = blocks[j].text.strip()
                if blocks[j].kind in ("heading", "para") and t:
                    if TYPE_LINE.match(t) and not typ:
                        typ = t; used.add(j)
                    elif not name and not ADDRESS.match(t):
                        name = t; used.add(j)
                        if typ:
                            break
            services = ""
            for j in range(i + 1, min(len(blocks), i + 4)):
                t = blocks[j].text.strip()
                if SERVICE_WORDS.search(t) and len(t) < 160:
                    services = t; used.add(j); break
                if ADDRESS.match(t) or blocks[j].kind == "heading":
                    break
            if i + 1 < len(blocks) and blocks[i + 1].text.strip() in ("Closed", "Open"):
                used.add(i + 1)   # live widget state, not a fact
            used.add(i)
            label = (typ or name or "Dupaco location").strip()
            title = f"{name}, {label}" if name and typ and name.lower() not in label.lower() else label
            parts = [f"{title}: {addr}"]
            if services:
                parts.append("Services: " + ", ".join(s.strip() for s in services.split("·")))
            text = ". ".join(parts)
            city = addr.split(",")[1].strip() if addr.count(",") >= 2 else ""
            facts = [Fact(entity=f"{title} ({city})" if city else title, attribute="address", value=addr,
                          url=ctx.url, quote=text, kind="text", page_type=ctx.ptype)]
            chunks.append(make_chunk(ctx, "branch", text, "branch atm location address " + city, facts=facts,
                                     order=b.order * 1000))
        return chunks, used

    def claims_text(self, b: Block) -> bool:
        return bool(TIME_RANGE.search(b.text)) and bool(HOURS_WORDS.search(b.text))

    def text(self, b: Block, ctx: PageCtx, windows_: list[str]):
        out = []
        for w in windows_:
            facts = []
            hours = TIME_RANGE.findall(w)
            if hours:
                facts.append(Fact(entity=" > ".join(b.captions) or ctx.title, attribute="hours",
                                  value="; ".join(h.strip() for h in hours[:6]), url=ctx.url, quote=w[:240],
                                  kind="text", page_type=ctx.ptype))
            for m in PHONE.finditer(w):
                facts.append(Fact(entity=ctx.title, attribute="phone", value=m.group(0), url=ctx.url,
                                  quote=w[max(0, m.start() - 40): m.end() + 10], kind="text", page_type=ctx.ptype))
            out.append(make_chunk(ctx, b.kind, w, " > ".join(b.captions) + " hours branch lobby drive-up",
                                  facts=facts, links=b.links, order=b.order * 1000))
        return out
