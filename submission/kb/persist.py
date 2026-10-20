"""Persistent memory for the built knowledge base (used by the local UI, not by the scorer).

The first start ingests the snapshots (about a minute). This module saves the result as gzip-compressed JSON
and loads it in a few seconds on the next start. JSON is used on purpose: loading it cannot run code, unlike
pickle. The cache is used only when its fingerprint (snapshot content + this package's source code) matches,
so a changed snapshot or a code change rebuilds it."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from array import array
from pathlib import Path

from .records import Chunk, Fact
from .store import PageRecord, SnapMeta

FORMAT = 2


def _pack(plist) -> str:
    """[(chunk id, term frequency), ...] -> base64 of two packed arrays (uint32 ids, uint16 counts)."""
    ids = array("I", (int(c) for c, _ in plist))
    tfs = array("H", (min(int(t), 65535) for _, t in plist))
    return base64.b64encode(ids.tobytes() + tfs.tobytes()).decode("ascii")


def _unpack(s: str) -> list:
    raw = base64.b64decode(s)
    n = len(raw) // 6
    ids, tfs = array("I"), array("H")
    ids.frombytes(raw[:4 * n])
    tfs.frombytes(raw[4 * n:])
    return list(zip(ids, tfs))


class LazyPostings(dict):
    """Index entries decoded on first use: loading the cache does not decode millions of entries up front."""

    def __init__(self, packed: dict):
        super().__init__()
        self._packed = packed

    def _fill(self, term):
        s = self._packed.pop(term, None)
        if s is None:
            return None
        plist = _unpack(s)
        dict.__setitem__(self, term, plist)
        return plist

    def get(self, term, default=None):
        if dict.__contains__(self, term):
            return dict.__getitem__(self, term)
        plist = self._fill(term)
        return default if plist is None else plist

    def __missing__(self, term):                # store.add_page appends to post[t]
        plist = self._fill(term)
        if plist is None:
            plist = []
            dict.__setitem__(self, term, plist)
        return plist

    def __contains__(self, term):
        return dict.__contains__(self, term) or term in self._packed

    def items(self):
        for t in list(self._packed):
            self._fill(t)
        return dict.items(self)
CHUNK_FIELDS = ("cid", "pk", "url", "title", "page_type", "authority", "kind", "text", "context", "links",
                "effective", "expiry", "stale_hint", "pubdate", "group", "order")
FACT_FIELDS = ("entity", "attribute", "value", "unit", "conditions", "url", "quote", "effective", "expiry",
               "status", "kind", "page_type")


def fingerprint(snapshots) -> str:
    """SHA-256 over every page of every snapshot and over the package source code."""
    h = hashlib.sha256()
    h.update(f"format={FORMAT}".encode())
    for src in sorted(Path(__file__).resolve().parent.rglob("*.py")):
        h.update(src.name.encode())
        h.update(src.read_bytes())
    for s in snapshots:
        h.update(f"{s.snapshot_id}|{s.taken_on}".encode())
        for p in s.pages:
            h.update(f"{p.url}\0{p.title}\0".encode("utf-8", "replace"))
            h.update((p.markdown or "").encode("utf-8", "replace"))
    return h.hexdigest()


def save(coordinator, path: Path, fp: str) -> None:
    st = coordinator.store
    data = {
        "format": FORMAT, "fingerprint": fp,
        "chunks": [[getattr(c, f) for f in CHUNK_FIELDS] + [[[getattr(x, f) for f in FACT_FIELDS] for x in c.facts]]
                   for c in st.chunks],
        "pages": [[pk, p.url, p.title, p.cids] for pk, p in st.pages.items()],
        "snaps": [[s.sid, s.date, s.pk_by_url, s.titles, s.fetched] for s in st.snaps],
        "post": {t: _pack(plist) for t, plist in st.post.items()}, "dl": st.dl, "total": st._total,
        "stats": coordinator.stats, "log": coordinator.log[-50:],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)                       # atomic: a half-written cache is never read


def load(coordinator, path: Path, fp: str) -> bool:
    """Fill an empty coordinator from the cache. Returns False (and changes nothing) if the cache does not fit."""
    if not path.is_file():
        return False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("fingerprint") != fp:
            return False
        # everything is decoded into local values first: a bad file changes nothing in the store
        chunks = []
        for row in data["chunks"]:
            c = Chunk(**dict(zip(CHUNK_FIELDS, row[:-1])))
            c.links = [tuple(x) for x in c.links]
            c.facts = [Fact(**dict(zip(FACT_FIELDS, fr))) for fr in row[-1]]
            chunks.append(c)
        pages = {int(pk): PageRecord(int(pk), url, title, list(cids)) for pk, url, title, cids in data["pages"]}
        if not isinstance(data["post"], dict) or not all(isinstance(v, str) for v in data["post"].values()):
            return False
        dl, total = list(data["dl"]), int(data["total"])
        snaps = [SnapMeta(sid, date_, {u: int(k) for u, k in pk_by_url.items()}, titles, fetched)
                 for sid, date_, pk_by_url, titles, fetched in data["snaps"]]
        if len(dl) != len(chunks):
            return False
        st = coordinator.store
        st.chunks, st.cpk, st.pages = chunks, [c.pk for c in chunks], pages
        st.post, st.dl, st._total = LazyPostings(data["post"]), dl, total
        st.snaps, st._vis = [], {}
        for meta in snaps:
            st.add_snapshot(meta)
        coordinator.history.rebuild(st)
        coordinator.stats = list(data.get("stats", []))
        coordinator.log = list(data.get("log", [])) + ["loaded from cache"]
        return True
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return False                          # unreadable or old cache: the caller rebuilds it
