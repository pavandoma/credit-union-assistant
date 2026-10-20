"""Knowledge system for the credit-union snapshots.

build() returns an object with ingest(snapshot), answer(question, as_of=None) and conflicts().
Architecture: see NOTES.md. Agents are plain Python components coordinated in kb/agents/coordinator.py.
No network access and no API key are needed."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb.agents.coordinator import Coordinator  # noqa: E402
from kb.answer import Answerer  # noqa: E402
from kb.contract_types import Answer  # noqa: E402


class KnowledgeBase:
    def __init__(self):
        self.coordinator = Coordinator()
        self.answerer = Answerer(self.coordinator)
        self._conflicts = None
        self._lock = threading.Lock()   # ingestion writes; answers and conflicts only read

    def ingest(self, snapshot) -> None:
        with self._lock:
            try:
                self.coordinator.ingest(snapshot)
            except Exception as exc:    # a damaged snapshot must not stop the other snapshots
                sid = getattr(snapshot, "snapshot_id", "?")
                print(f"[solution] ingest of {sid} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._conflicts = None

    def answer(self, question, as_of=None):
        try:
            return self.answerer.answer(question, as_of)
        except Exception as exc:        # never crash the caller; an honest unknown is the safe result
            return Answer(text="I don't know. Please contact us.", confident=False,
                          conflict_note=f"internal error: {type(exc).__name__}")

    def conflicts(self):
        with self._lock:
            if self._conflicts is None:
                co = self.coordinator
                if not co.store.snaps:
                    return []
                try:
                    self._conflicts = co.history.conflicts(co.store, len(co.store.snaps) - 1)
                except Exception as exc:
                    print(f"[solution] conflicts failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    return []
            return list(self._conflicts)


def build() -> KnowledgeBase:
    return KnowledgeBase()
