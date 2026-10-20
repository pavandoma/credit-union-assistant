"""Fact store, snapshot membership and BM25 search index.

The coordinator is the only writer. Unchanged page text is stored once and shared by every snapshot
that contains it; each snapshot keeps its own page-membership map."""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .records import Chunk
from .text import tokens


@dataclass
class SnapMeta:
    sid: str
    date: str
    pk_by_url: dict = field(default_factory=dict)      # url -> page key
    titles: dict = field(default_factory=dict)         # url -> title
    fetched: dict = field(default_factory=dict)        # url -> fetched_at


@dataclass
class PageRecord:
    pk: int
    url: str
    title: str
    cids: list


class Store:
    K1, B = 1.2, 0.75

    def __init__(self):
        self.chunks: list[Chunk] = []
        self.pages: dict[int, PageRecord] = {}
        self.snaps: list[SnapMeta] = []
        self.post: dict[str, list] = defaultdict(list)    # term -> [(cid, tf)]
        self.dl: list[int] = []
        self.cpk: list[int] = []        # page key of each chunk, for a fast snapshot filter
        self._total = 0
        self._vis: dict[int, frozenset] = {}

    # ---- single write path -------------------------------------------------
    def add_page(self, pk: int, url: str, title: str, chunks: list[Chunk]) -> None:
        if pk in self.pages:
            return
        cids = []
        for ch in chunks:
            ch.pk = pk
            ch.cid = len(self.chunks)
            self.chunks.append(ch)
            cids.append(ch.cid)
            toks = tokens(f"{ch.context} {ch.text}") + tokens(title)[:6]
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, c in tf.items():
                self.post[t].append((ch.cid, c))
            self.dl.append(len(toks))
            self.cpk.append(pk)
            self._total += len(toks)
        self.pages[pk] = PageRecord(pk, url, title, cids)

    def add_snapshot(self, meta: SnapMeta) -> bool:
        """Adds a snapshot and keeps the list in date order. Returns False if it had to be inserted early."""
        in_order = not self.snaps or self.snaps[-1].date <= meta.date
        self.snaps.append(meta)
        if not in_order:
            self.snaps.sort(key=lambda s: s.date)
        self._vis = {i: frozenset(s.pk_by_url.values()) for i, s in enumerate(self.snaps)} if not in_order \
            else {**self._vis, len(self.snaps) - 1: frozenset(meta.pk_by_url.values())}
        return in_order

    # ---- reading -------------------------------------------------------------
    def snapshot_for(self, as_of: str | None) -> int | None:
        """Latest snapshot taken on or before as_of. None -> latest. Before the first -> None sentinel -1."""
        if not self.snaps:
            return None
        if not as_of:
            return len(self.snaps) - 1
        idx = -1
        for i, s in enumerate(self.snaps):
            if s.date <= as_of[:10]:
                idx = i
        return idx

    def visible(self, si: int) -> frozenset:
        return self._vis[si]

    def df(self, term: str) -> int:
        return len(self.post.get(term, ()))

    def idf(self, term: str) -> float:
        n = max(1, len(self.chunks))
        d = self.df(term)
        return math.log(1 + (n - d + 0.5) / (d + 0.5))

    def removed_pages(self, si: int) -> dict:
        """Pages seen in an earlier snapshot whose URL is absent from snapshot si.
        Returns {page key: snapshot index where it was last seen}. They are history, not current evidence."""
        out: dict[int, int] = {}
        current = self.snaps[si].pk_by_url
        newest: dict[str, tuple] = {}
        for j in range(si):
            for url, pk in self.snaps[j].pk_by_url.items():
                if url not in current:
                    newest[url] = (j, pk)
        for url, (j, pk) in newest.items():
            out[pk] = j
        return out

    def search(self, qtoks: list[str], si: int, topn: int = 80, weights: dict | None = None, vis=None):
        vis = self._vis[si] if vis is None else vis
        n = max(1, len(self.chunks))
        avg = self._total / n
        terms = [t for t in dict.fromkeys(qtoks) if self.post.get(t)]
        if len(terms) > 1:
            # words found in more than 30% of all chunks add almost nothing and cost the most time
            rare = [t for t in terms if self.df(t) <= 0.3 * n]
            terms = rare or terms
        k1, cpk, dl = self.K1, self.cpk, self.dl
        base, slope = k1 * (1 - self.B), k1 * self.B / avg
        scores: dict[int, float] = {}
        mask: dict[int, int] = {}
        for i, t in enumerate(terms):
            idf = self.idf(t) * (weights.get(t, 1.0) if weights else 1.0) * (k1 + 1)
            bit = 1 << i
            for cid, tf in self.post[t]:
                if cpk[cid] not in vis:
                    continue
                scores[cid] = scores.get(cid, 0.0) + idf * tf / (tf + base + slope * dl[cid])
                mask[cid] = mask.get(cid, 0) | bit
        top = heapq.nlargest(topn, scores.items(), key=lambda kv: (kv[1], -kv[0]))
        return [(cid, sc, {terms[i] for i in range(len(terms)) if mask[cid] >> i & 1}) for cid, sc in top]
