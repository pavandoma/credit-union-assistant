"""Tests for the answer rules that work on wording: value-exists questions, same-statement sources and
area sentences. They use only the small artificial data in harness/selftest.

    python -m unittest discover -s submission/tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

import solution  # noqa: E402
from kb.answer import AREA_REF, DAY_SPAN, EXISTS, ID_NUM, PHONE, STATE_RE, Answerer  # noqa: E402
from run_eval import load_snapshots  # noqa: E402

SNAPS = load_snapshots(ROOT / "harness" / "selftest" / "snapshots")


class PatternTests(unittest.TestCase):
    def test_exists_question(self):
        for q in ("Do any savings tiers pay 0.35% APY?", "Is there any savings tier paying 0.35% APY?",
                  "Does any certificate pay 4.2%?"):
            self.assertIsNotNone(EXISTS.match(q), q)
        for q in ("What is the 6-month certificate APY?", "Do you have a 7/1 ARM?", "Is the branch open?"):
            self.assertIsNone(EXISTS.match(q), q)

    def test_same_span_in_other_words(self):
        a = DAY_SPAN.search("Monday-Friday: 8:30 a.m.-5:30 p.m.")
        b = DAY_SPAN.search("we reply Monday–Friday, 8:30 a.m.–5:30 p.m.")
        c = DAY_SPAN.search("Monday-Friday: 9 a.m.-5 p.m.")
        self.assertEqual(Answerer._span_key(a), Answerer._span_key(b))
        self.assertNotEqual(Answerer._span_key(a), Answerer._span_key(c))

    def test_identifiers(self):
        self.assertEqual(ID_NUM.findall("Routing Number: 273974581"), ["273974581"])
        self.assertEqual(ID_NUM.findall("APY 4.20% on $100,000.00 since 2026"), [])
        self.assertEqual(PHONE.findall("call 800-373-7600 or (563) 557-7600"), ["800-373-7600", "(563) 557-7600"])

    def test_area_reference(self):
        self.assertTrue(AREA_REF.search("You live or work within our charter area."))
        self.assertEqual(set(STATE_RE.findall("You can find us in Iowa, Illinois and Wisconsin.")),
                         {"Iowa", "Illinois", "Wisconsin"})


class ExistsAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = solution.build()
        for s in SNAPS:
            cls.ks.ingest(s)

    def test_exists_questions_never_raise(self):
        for q in ("Do any certificates pay 3.50% APY?", "Do any certificates pay 99% APY?",
                  "Is there a 0% fee?", "Do any 12345678901234567890% tiers pay?", "Do any 0.5%"):
            a = self.ks.answer(q)
            self.assertIsInstance(a.text, str, q)

    def test_unmatched_product_falls_back(self):
        a = self.ks.answer("Do any spaceship loans pay 3.50% APY?")
        self.assertNotIn("top out", a.text)


if __name__ == "__main__":
    unittest.main()
