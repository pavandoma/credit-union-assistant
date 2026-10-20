"""Coordinator: processes snapshots oldest to newest, assigns page content to the specialists,
validates completion, and merges everything through one write path (the Store)."""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

from ..pageparse import clean_doc, parse_page
from ..records import AUTHORITY, page_type
from ..store import SnapMeta, Store
from ..text import parse_dates
from .branches import BranchAgent
from .checker import EvidenceChecker
from .common import PageCtx, windows
from .fees_products import FeesProductsAgent
from .history import HistoryConflictAgent
from .rates import RatesAgent, is_rate_table

MIN_TEXT = 12
MAX_PAGE_CHARS = 2_000_000          # a page larger than this is cut before parsing (largest real page: ~0.2 MB)
TEASER = re.compile(r"(\.\.\.|…)\s*$|^(?:keep reading|continue|cancel|\d+ min read)\b", re.I)


def page_key(url: str, markdown: str) -> int:
    return int(hashlib.md5((url + "\0" + markdown).encode("utf-8", "replace")).hexdigest()[:15], 16)


class Coordinator:
    def __init__(self, workers: int = 1):
        # Page tasks are independent and can run on a thread pool (workers > 1). The work is pure Python, so
        # threads share one interpreter lock: measured 52.6 s with 4 threads against 35.0 s with 1. Default: 1.
        self.store = Store()
        self.checker = EvidenceChecker()
        self.rates = RatesAgent()
        self.fees = FeesProductsAgent()
        self.branches = BranchAgent()
        self.history = HistoryConflictAgent(self.checker)
        self.workers = workers
        self.log: list[str] = []
        self.stats: list[dict] = []

    # ---- one page: route blocks to specialists --------------------------------------
    def process_page(self, url: str, title: str, markdown: str):
        ptype = page_type(url)
        head = " ".join(markdown.splitlines()[:14])
        ds = parse_dates(head) if ptype in ("news", "blog") else []
        ctx = PageCtx(url, title, ptype, AUTHORITY.get(ptype, 0.5), ds[0] if ds else None)
        doc = clean_doc(markdown)
        blocks = parse_page(markdown)
        chunks, used = self.branches.scan_page(blocks, ctx) if ptype in ("contact", "info", "home") else ([], set())
        group = 0
        seen_text = set()
        for idx, b in enumerate(blocks):
            if idx in used or b.kind == "heading":
                continue
            if b.kind == "table":
                group += 1
                agent = self.rates if is_rate_table(b) else self.fees
                chunks += agent.table(b, blocks, idx, ctx, group)
                continue
            if len(b.text) < MIN_TEXT or b.text in seen_text:
                continue
            if TEASER.search(b.text):       # "related article" teasers belong to other pages
                continue
            seen_text.add(b.text)
            ws = windows(b.text)
            if self.rates.claims_text(b):
                chunks += self.rates.text(b, ctx, ws)
            elif self.branches.claims_text(b):
                chunks += self.branches.text(b, ctx, ws)
            else:
                chunks += self.fees.text(b, ctx, ws)
        self.checker.check_page(doc, chunks)
        return chunks

    # ---- one snapshot -------------------------------------------------------------------
    def _safe_process(self, url: str, title: str, markdown: str):
        """One bad page must not stop the snapshot: it is logged and stored with no chunks."""
        try:
            return self.process_page(url, title, markdown)
        except Exception as exc:  # pragma: no cover - defensive
            self.log.append(f"page skipped ({type(exc).__name__}): {url[:120]}")
            return []

    def ingest(self, snapshot) -> None:
        sid = str(getattr(snapshot, "snapshot_id", "") or "")
        taken = str(getattr(snapshot, "taken_on", "") or "")[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", taken):
            raise ValueError(f"snapshot {sid!r}: taken_on must be an ISO date, got {taken!r}")
        if any(s.sid == sid for s in self.store.snaps):
            self.log.append(f"{sid}: already ingested, skipped")
            return                                   # idempotent: the same snapshot twice changes nothing
        meta = SnapMeta(sid, taken)
        todo, seen = [], set()
        for p in snapshot.pages or []:
            url = str(getattr(p, "url", "") or "")
            if not url.startswith(("http://", "https://")):
                continue                             # only web pages are evidence
            markdown = str(getattr(p, "markdown", "") or "")[:MAX_PAGE_CHARS]
            title = str(getattr(p, "title", "") or "")[:300]
            pk = page_key(url, markdown)
            meta.pk_by_url[url] = pk
            meta.titles[url] = title
            meta.fetched[url] = str(getattr(p, "fetched_at", "") or "")
            if pk not in self.store.pages and pk not in seen:
                seen.add(pk)
                todo.append((pk, url, title, markdown))
        # independent extraction tasks; results are merged below on this thread only
        if todo:
            if self.workers > 1:
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    results = list(pool.map(lambda t: self._safe_process(t[1], t[2], t[3]), todo))
            else:
                results = [self._safe_process(t[1], t[2], t[3]) for t in todo]
            for (pk, url, title, _), chunks in zip(todo, results):
                self.store.add_page(pk, url, title, chunks)
        in_order = self.store.add_snapshot(meta)
        # completion check: every page of the snapshot must be in the store
        missing = [u for u, pk in meta.pk_by_url.items() if pk not in self.store.pages]
        if missing:
            raise RuntimeError(f"snapshot {meta.sid}: {len(missing)} pages not stored")
        if in_order:
            self.history.observe(self.store, len(self.store.snaps) - 1)
        else:
            self.history.rebuild(self.store)         # a snapshot arrived out of date order
        self.stats.append({"snapshot_id": meta.sid, "date": meta.date, "pages": len(meta.pk_by_url),
                           "new_or_changed": len(todo)})
        self.log.append(f"{meta.sid}: {len(meta.pk_by_url)} pages, {len(todo)} new/changed")
