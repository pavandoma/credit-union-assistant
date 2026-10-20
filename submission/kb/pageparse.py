"""Markdown page -> blocks. Shared by all extraction agents (parsed once per page)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[((?:[^\[\]])*?)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)", re.S)
_CRLF = re.compile(r"\\+[rn]")
_MARK = re.compile(r"⟦([^⟧]*)⟧")
_FOOT = re.compile(r"(%\s*(?:APR|APY)?)(\d)(?!\d)")


@dataclass
class Block:
    kind: str                       # heading | table | para | list
    text: str = ""
    links: list = field(default_factory=list)
    header: list = field(default_factory=list)      # table header cells
    rows: list = field(default_factory=list)        # table rows (lists of cells)
    captions: tuple = ()            # nearest preceding headings / short captions
    order: int = 0


def clean_doc(md: str) -> str:
    s = md.replace("�", "'").replace(" ", " ")
    s = _CRLF.sub(" ", s)
    s = _IMG.sub("", s)
    for _ in range(3):  # nested links
        s2 = _LINK.sub(lambda m: f"{m.group(1)}⟦{m.group(2)}⟧", s)
        if s2 == s:
            break
        s = s2
    s = s.replace("\\\\", " ").replace("\\#", "#").replace("\\$", "$").replace("\\*", "*").replace("\\_", "_")
    s = re.sub(r"[*_]{2,}", "", s)
    s = re.sub(r"(?<!\w)\*(\S[^*\n]*?)\*(?!\w)", r"\1", s)
    return s


def split_marks(text: str) -> tuple[str, list]:
    """Remove link markers from text; return (clean text, [(anchor, url)])."""
    links = []

    def grab(m):
        links.append(m.group(1))
        return ""
    # anchor text is the text directly before each marker
    out, last = [], 0
    for m in _MARK.finditer(text):
        before = text[last:m.start()]
        out.append(before)
        anchor = re.split(r"[.!?\n]\s", before)[-1].strip()[-80:]
        links.append((anchor, m.group(1)))
        last = m.end()
    out.append(text[last:])
    t = "".join(out)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return t, links


def clean_cell(c: str) -> str:
    c, _ = split_marks(c)
    c = _FOOT.sub(r"\1", c)
    return c.strip()


def _is_caption(line: str) -> bool:
    t = line.strip()
    return 0 < len(t) <= 60 and not re.search(r"[.!?:;|]$", t) and not t.startswith(("*", "-", "|", "["))


def parse_page(markdown: str) -> list[Block]:
    doc = clean_doc(markdown)
    lines = doc.split("\n")
    blocks: list[Block] = []
    i, n = 0, len(lines)
    para: list[str] = []
    captions: list[str] = []

    def flush():
        if para:
            raw = " ".join(x.strip() for x in para).strip()
            para.clear()
            if not raw or set(raw) <= set("*- _"):
                return
            text, links = split_marks(raw)
            if not text:
                return
            blocks.append(Block("para", text, links, captions=tuple(captions[-2:]), order=len(blocks)))
            link_only = bool(links) and not re.sub(r"[^⟦⟧]*?⟦[^⟧]*⟧", "", raw).strip()
            if _is_caption(text) and not re.search(r"\d", text) and not link_only:
                captions.append(text)
                del captions[:-3]

    while i < n:
        ln = lines[i]
        s = ln.strip()
        if not s:
            flush(); i += 1; continue
        if re.match(r"^#{1,6}\s", s):
            flush()
            text, links = split_marks(re.sub(r"^#{1,6}\s*", "", s))
            if text:
                blocks.append(Block("heading", text, links, captions=tuple(captions[-2:]), order=len(blocks)))
                captions.append(text)
                del captions[:-3]
            i += 1; continue
        if s.startswith("|"):
            flush()
            tl = []
            while i < n and lines[i].strip().startswith("|"):
                tl.append(lines[i].strip()); i += 1
            rows = [[clean_cell(c) for c in r.strip().strip("|").split("|")] for r in tl]
            rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) or not c for c in r)]
            if len(rows) >= 2:
                blocks.append(Block("table", "", [], header=rows[0], rows=rows[1:],
                                    captions=tuple(captions[-2:]), order=len(blocks)))
                if captions:
                    captions.pop()      # the table title is used up; the next table must not inherit it
            continue
        if re.match(r"^(\*|-|\d+\.)\s+\S", s) and not re.match(r"^\*\s*\*\s*\*", s):
            flush()
            text, links = split_marks(re.sub(r"^(\*|-|\d+\.)\s+", "", s))
            if text:
                blocks.append(Block("list", text, links, captions=tuple(captions[-2:]), order=len(blocks)))
            i += 1; continue
        if re.fullmatch(r"(\*\s*){3,}", s):
            flush(); i += 1; continue
        para.append(s); i += 1
    flush()
    return blocks
