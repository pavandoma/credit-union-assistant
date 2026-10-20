"""Bridge to the organizer's types (harness/contract.py). Falls back to local copies with the same
fields, so the package still imports when the harness folder is not on sys.path."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from contract import Answer, Conflict, Page, Snapshot, Source  # type: ignore
except ImportError:
    for _d in Path(__file__).resolve().parents:
        if (_d / "harness" / "contract.py").exists():
            sys.path.insert(0, str(_d / "harness"))
            break
    try:
        from contract import Answer, Conflict, Page, Snapshot, Source  # type: ignore
    except ImportError:  # same shape as harness/contract.py
        from dataclasses import dataclass, field
        from typing import Optional

        @dataclass(frozen=True)
        class Page:
            url: str
            title: str
            markdown: str
            fetched_at: str

        @dataclass(frozen=True)
        class Snapshot:
            snapshot_id: str
            taken_on: str
            pages: list

        @dataclass
        class Source:
            url: str
            snapshot_id: Optional[str] = None
            quote: Optional[str] = None

        @dataclass
        class Answer:
            text: str
            sources: list = field(default_factory=list)
            confident: bool = True
            conflict_note: Optional[str] = None

        @dataclass
        class Conflict:
            topic: str
            values: list
            sources: list
            resolution: Optional[str] = None
            reason: Optional[str] = None

__all__ = ["Answer", "Conflict", "Page", "Snapshot", "Source"]
