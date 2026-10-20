"""Guardrail and contract tests. They use only the small artificial data in harness/selftest.

    python -m unittest discover -s submission/tests -v
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

import solution  # noqa: E402
from contract import Answer, Conflict, Page, Snapshot  # noqa: E402
from run_eval import load_snapshots  # noqa: E402

SNAPS = load_snapshots(ROOT / "harness" / "selftest" / "snapshots")


def build(order=None):
    ks = solution.build()
    for s in order or SNAPS:
        ks.ingest(s)
    return ks


class ContractTests(unittest.TestCase):
    def test_types(self):
        ks = build()
        a = ks.answer("What is the 6-month certificate APY?")
        self.assertIsInstance(a, Answer)
        self.assertTrue(all(isinstance(c, Conflict) for c in ks.conflicts()))

    def test_current_and_historical(self):
        ks = build()
        self.assertIn("3.50%", ks.answer("What is the 6-month certificate APY?").text)
        old = ks.answer("What was the 6-month certificate APY?", "2026-01-31").text
        self.assertIn("2.95%", old)
        self.assertNotIn("3.50%", old)          # a later snapshot is never used for an earlier date

    def test_before_first_snapshot(self):
        a = build().answer("What is the 6-month certificate APY?", "2025-12-01")
        self.assertFalse(a.confident)

    def test_refusal_has_no_numbers(self):
        a = build().answer("What is the 60-month jumbo certificate rate?")
        self.assertFalse(a.confident)
        self.assertNotRegex(a.text, r"\d")

    def test_conflict_resolution(self):
        cs = build().conflicts()
        self.assertTrue(any(set(c.values) == {"2.95%", "3.50%"} and c.resolution == "3.50%" for c in cs))


class GuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = build()

    def test_bad_dates(self):
        for d in ("abc", "2026-13-45", "20260131"):
            a = self.ks.answer("What is the 6-month certificate APY?", d)
            self.assertFalse(a.confident, d)
            self.assertIn("YYYY-MM-DD", a.text)

    def test_hostile_questions_are_fast_and_safe(self):
        for q in ("", None, 12345, "$" + "1," * 20000, "12 month " * 3000, "' OR 1=1 --", "rate?",
                  "<script>alert(1)</script>", "\x00‮", "a" * 100000):
            t = time.time()
            a = self.ks.answer(q)
            self.assertLess(time.time() - t, 2.0, repr(q)[:40])
            self.assertIsInstance(a.text, str)
            self.assertFalse(a.confident, repr(q)[:40])

    def test_out_of_order_and_duplicate_ingest(self):
        q = "What is the 6-month certificate APY?"
        normal = build().answer(q).text
        self.assertEqual(build(list(reversed(SNAPS))).answer(q).text, normal)
        self.assertEqual(build(SNAPS + [SNAPS[0]]).answer(q).text, normal)

    def test_bad_pages_do_not_stop_ingest(self):
        bad = Snapshot("09_2026-04-01_bad", "2026-04-01", [
            Page(url="javascript:alert(1)", title="x", markdown="evil", fetched_at=""),
            Page(url="https://example-cu.test/empty", title=None, markdown=None, fetched_at=None)])
        ks = build(SNAPS + [bad, Snapshot("10_bad", "not-a-date", [])])
        self.assertIsInstance(ks.answer("What is the 6-month certificate APY?"), Answer)


if __name__ == "__main__":
    unittest.main()
