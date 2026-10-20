"""Evidence checker: checks extracted values against source text, and conflict resolutions against evidence."""
from __future__ import annotations

import re

from ..text import key_tokens, tokens


def _compact(s: str) -> str:
    return re.sub(r"[\s,]", "", s.lower())


class EvidenceChecker:
    name = "checker"

    def check_page(self, doc_text: str, chunks: list) -> None:
        """Mark each fact verified when its value, entity and conditions all appear in the page text."""
        if not any(ch.facts for ch in chunks):
            return                      # nothing to check on this page
        compact = _compact(doc_text)
        toks = set(tokens(doc_text))
        for ch in chunks:
            for f in ch.facts:
                ok = _compact(f.value) in compact
                ok = ok and set(key_tokens(f.entity)) <= toks
                for v in f.conditions.values():
                    ok = ok and set(key_tokens(v)) <= toks
                f.status = "verified" if ok else "unsupported"

    def check_conflict_value(self, value: str, quote: str) -> bool:
        return _compact(value) in _compact(quote)
