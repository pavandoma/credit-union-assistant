"""Tests for the UI memory modules: working memory (chat follow-ups) and the saved knowledge cache.

    python -m unittest discover -s submission/tests -v
"""
from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

import solution  # noqa: E402
from kb import persist  # noqa: E402
from kb.working_memory import is_follow_up, resolve  # noqa: E402
from run_eval import load_snapshots  # noqa: E402

SNAPS = load_snapshots(ROOT / "harness" / "selftest" / "snapshots")
AUTO = "What's the APR for a 60-month auto loan on a 2025 model-year vehicle?"


class WorkingMemoryTests(unittest.TestCase):
    def test_new_condition_replaces_old_one(self):
        q, used = resolve("What about 72 months?", AUTO)
        self.assertTrue(used)
        self.assertIn("72 months", q)
        self.assertNotIn("60-month", q)
        self.assertIn("2025", q)                 # the other conditions stay

    def test_new_year_replaces_old_year(self):
        q, used = resolve("And for a 2024 vehicle?", AUTO)
        self.assertTrue(used)
        self.assertIn("2024", q)
        self.assertNotIn("2025", q)
        self.assertIn("60-month", q)

    def test_date_follow_up(self):
        q, used = resolve("and in May 2026?", "What is the 6-month certificate APY?")
        self.assertTrue(used)
        self.assertIn("May 2026", q)
        self.assertIn("certificate", q)

    def test_new_attribute_keeps_the_subject(self):
        prev = "What is the address of the Galena Branch?"
        for follow in ("What are its hours?", "What about the hours?"):
            q, used = resolve(follow, prev)
            self.assertTrue(used, follow)
            self.assertIn("Galena Branch", q)
            self.assertIn("hours", q)
            self.assertNotIn("address", q)

    def test_full_question_is_not_changed(self):
        for q in ("What is the address of the Galena Branch?", "What are the drive-up hours?"):
            self.assertFalse(is_follow_up(q), q)
            self.assertEqual(resolve(q, AUTO), (q, False))

    def test_no_previous_question(self):
        self.assertEqual(resolve("What about 72 months?", None), ("What about 72 months?", False))
        self.assertEqual(resolve("What about 72 months?", ""), ("What about 72 months?", False))

    def test_hostile_input(self):
        q, _ = resolve("and " + "x" * 5000, AUTO * 50)
        self.assertLessEqual(len(q), 1000)
        self.assertEqual(resolve("", AUTO), ("", False))


class PersistTests(unittest.TestCase):
    QUESTIONS = ("What is the 6-month certificate APY?", "What is the 12-month certificate APY?",
                 "What is the lost debit card fee?", "What is the 60-month jumbo certificate rate?")

    def build(self):
        ks = solution.build()
        for s in SNAPS:
            ks.ingest(s)
        return ks

    def test_round_trip_gives_same_answers(self):
        ks = self.build()
        fp = persist.fingerprint(SNAPS)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "kb.json.gz"
            persist.save(ks.coordinator, path, fp)
            ks2 = solution.build()
            self.assertTrue(persist.load(ks2.coordinator, path, fp))
        for q in self.QUESTIONS:
            self.assertEqual(ks2.answer(q).text, ks.answer(q).text, q)
        self.assertEqual(ks2.answer(self.QUESTIONS[0], "2026-01-31").text,
                         ks.answer(self.QUESTIONS[0], "2026-01-31").text)
        self.assertEqual([(c.topic, c.values, c.resolution) for c in ks2.conflicts()],
                         [(c.topic, c.values, c.resolution) for c in ks.conflicts()])

    def test_wrong_fingerprint_or_bad_file_is_refused(self):
        ks = self.build()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "kb.json.gz"
            persist.save(ks.coordinator, path, "abc")
            self.assertFalse(persist.load(solution.build().coordinator, path, "other"))
            path.write_bytes(b"not gzip")
            self.assertFalse(persist.load(solution.build().coordinator, path, "abc"))
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write('{"format": 2, "fingerprint": "abc", "chunks": "x"}')
            self.assertFalse(persist.load(solution.build().coordinator, path, "abc"))
            self.assertFalse(persist.load(solution.build().coordinator, Path(d) / "missing.gz", "abc"))

    def test_fingerprint_changes_with_content(self):
        self.assertEqual(persist.fingerprint(SNAPS), persist.fingerprint(SNAPS))
        self.assertNotEqual(persist.fingerprint(SNAPS), persist.fingerprint(SNAPS[:1]))


if __name__ == "__main__":
    unittest.main()
