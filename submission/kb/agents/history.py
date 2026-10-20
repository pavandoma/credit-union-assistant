"""History and conflict agent.

Compares facts that have the same meaning and the same conditions. It separates three cases:
  change      - a fact was valid earlier and has a new value later (kept as history, not a conflict)
  contradiction - two sources in one snapshot give different values for the same entity,
                  attribute and conditions (a conflict)
  different conditions - tiers, terms, products differ (not compared: the conditions are part of the key)
Choice of the winner never uses a majority vote. It uses source purpose, expiry hints and effective date."""
from __future__ import annotations

import re
from collections import defaultdict

from ..contract_types import Conflict, Source


def normval(v: str) -> str:
    s = re.sub(r"[\s,$%]", "", v.lower())
    try:
        return repr(round(float(s), 4))
    except ValueError:
        return s


COMPARABLE = re.compile(r"apy|apr|rate|yield|fee|minimum|dividend|locations|employees|counties|members", re.I)
NOISE_TYPES = {"blog"}      # calculators are kept: a calculator with an old rate is a real contradiction
YEAR_SPAN = re.compile(r"((?:19|20)\d\d)\s*[-–—]\s*(\d{2,4})")


def year_span(f):
    """Vehicle model-year window in a fact, such as 2024-27 -> ((2024, 2027), {'2024', '27'})."""
    m = YEAR_SPAN.search(f.entity + " " + " ".join(f.conditions.values()))
    if not m:
        return None, set()
    lo, hi = int(m.group(1)), m.group(2)
    hi = int(hi) if len(hi) == 4 else int(str(lo)[:2] + hi)
    return (lo, hi), {m.group(1), m.group(2)}


def _overlap(a, b) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


class HistoryConflictAgent:
    name = "history_conflict"

    def __init__(self, checker):
        self.checker = checker
        self.by_snap: dict[int, dict] = {}
        self.timeline: dict = defaultdict(list)    # (url, key) -> [(snapshot index, value)]

    # ---- ingestion ---------------------------------------------------------------
    def rebuild(self, store) -> None:
        """Recompute history for all snapshots (used when a snapshot arrives out of date order)."""
        self.by_snap = {}
        self.timeline = defaultdict(list)
        for si in range(len(store.snaps)):
            self.observe(store, si)

    def observe(self, store, si: int) -> None:
        idx = defaultdict(list)
        for url, pk in store.snaps[si].pk_by_url.items():
            for cid in store.pages[pk].cids:
                ch = store.chunks[cid]
                for f in ch.facts:
                    if f.status != "verified":
                        continue
                    idx[f.key()].append((f, ch))
        self.by_snap[si] = idx
        for key, items in idx.items():
            for f, ch in items:
                tl = self.timeline[(f.url, key)]
                if not tl or tl[-1][1] != f.value:
                    tl.append((si, f.value))

    # ---- scoring -------------------------------------------------------------------
    @staticmethod
    def rank(f, ch, snap_date: str):
        s = ch.authority
        if ch.stale_hint or (ch.expiry and ch.expiry < snap_date):
            s -= 0.6
        eff = f.effective or ch.effective or ""
        return (round(s, 3), eff)

    def winner(self, items, snap_date):
        """items: [(fact, chunk)] with at least two distinct values. Returns (fact, chunk, reason) or None."""
        scored = sorted(((self.rank(f, ch, snap_date), f, ch) for f, ch in items), key=lambda x: x[0], reverse=True)
        top = scored[0]
        rival = next((x for x in scored if normval(x[1].value) != normval(top[1].value)), None)
        if rival is None:
            return None
        if top[0] == rival[0]:
            return None
        why = []
        if top[0][0] > rival[0][0]:
            why.append(f"source purpose: {top[2].page_type} page outranks {rival[2].page_type} page")
            if rival[2].stale_hint or (rival[2].expiry and rival[2].expiry < snap_date):
                why.append("the other source is an ended or expired offer")
        if top[0][1] and top[0][1] > rival[0][1]:
            why.append(f"newer effective date {top[0][1]}")
        reason = "; ".join(why) or "higher-ranked source"
        reason = reason[0].upper() + reason[1:]
        if not self.checker.check_conflict_value(top[1].value, top[1].quote or top[2].text):
            return None
        return top[1], top[2], reason

    # ---- reporting -------------------------------------------------------------------
    @staticmethod
    def _clusters(idx):
        """Facts that mean the same thing. Model-year windows move over time (2023-26 became 2024-27), so
        windows that overlap by three years or more count as the same loan program."""
        groups = defaultdict(list)
        for key, items in idx.items():
            for f, ch in items:
                span, drop = year_span(f)
                words = tuple(w for w in key[1] if w not in drop)
                groups[(key[0], words, span is not None)].append((f, ch, span))
        out = []
        for (_, _, has_span), members in groups.items():
            if not has_span:
                out.append([(f, ch) for f, ch, _ in members])
                continue
            clusters = []
            for f, ch, span in members:
                for cl in clusters:
                    if any(s == span or _overlap(s, span) >= 3 for _, _, s in cl):
                        cl.append((f, ch, span))
                        break
                else:
                    clusters.append([(f, ch, span)])
            out += [[(f, ch) for f, ch, _ in cl] for cl in clusters]
        return out

    def conflicts(self, store, si: int) -> list:
        idx = self.by_snap.get(si, {})
        snap = store.snaps[si]
        out, seen = [], set()
        for items in self._clusters(idx):
            vals = {}
            for f, ch in items:
                vals.setdefault(normval(f.value), []).append((f, ch))
            if len(vals) < 2:
                continue
            f0 = items[0][0]
            if not COMPARABLE.search(f0.attribute) or f0.attribute in ("hours", "phone", "address"):
                continue
            items = [(f, ch) for f, ch in items if ch.page_type not in NOISE_TYPES]
            vals = {}
            for f, ch in items:
                vals.setdefault(normval(f.value), []).append((f, ch))
            if len(vals) < 2:
                continue
            f0 = items[0][0]
            cond = ", ".join(v for v in f0.conditions.values())
            topic = f"{f0.entity} {f0.attribute}" + (f" ({cond})" if cond else "")
            shown = [v[0][0].value for v in vals.values()]
            sig = (topic.lower(), tuple(sorted(shown)))
            if sig in seen:
                continue
            seen.add(sig)
            srcs = []
            for v in vals.values():
                f, ch = v[0]
                srcs.append(Source(url=f.url, snapshot_id=snap.sid, quote=(f.quote or ch.text)[:200]))
            win = self.winner(items, snap.date)
            if win:
                wf, wch, reason = win
                out.append(Conflict(topic=topic, values=shown, sources=srcs, resolution=wf.value, reason=reason))
            else:
                out.append(Conflict(topic=topic, values=shown, sources=srcs, resolution=None,
                                    reason="unresolved: sources have equal authority and no later effective date"))
        return out

    def changes(self, key_filter=None):
        """Facts whose value changed between snapshots (history, not conflicts)."""
        return {k: v for k, v in self.timeline.items() if len(v) > 1 and (key_filter is None or key_filter(k))}
