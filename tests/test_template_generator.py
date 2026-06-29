#!/usr/bin/env python3
"""Tests for the template tier — run: python3 -m unittest discover tests"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator
from shared.generator.spintax import spin, variants


class TestSpintax(unittest.TestCase):
    def test_resolves_all_groups(self):
        out = spin("{a|b} {c|d}", random.Random(1))
        self.assertNotIn("{", out)
        self.assertIn(out, {"a c", "a d", "b c", "b d"})

    def test_nested(self):
        for _ in range(50):
            out = spin("{x|{y|z}}", random.Random(_))
            self.assertIn(out, {"x", "y", "z"})

    def test_variants_enumerates(self):
        self.assertEqual(set(variants("{a|b}{c|d}")), {"ac", "ad", "bc", "bd"})

    def test_empty_option_allowed(self):
        self.assertIn(spin("hi{!|}", random.Random(0)), {"hi!", "hi"})


class TestSchema(unittest.TestCase):
    def test_tolerant_parsing(self):
        lead = LeadEnrichment.from_dict({
            "username": "@Foo_Bar", "full_name": "Priya Sharma",
            "is_business": "yes", "top_hashtags": "#yoga #flow",
            "inferred_activity": "video-first (reels/short-form)",
            "followers": "1,200".replace(",", ""), "category": "Fitness Trainer",
        })
        self.assertEqual(lead.username, "Foo_Bar")
        self.assertEqual(lead.first_name, "Priya")
        self.assertTrue(lead.is_business)
        self.assertEqual(lead.primary_topic, "yoga")
        self.assertEqual(lead.activity_kind, "video")
        self.assertEqual(lead.clean_category, "fitness trainer")

    def test_no_name_falls_back_clean(self):
        self.assertEqual(LeadEnrichment.from_dict({"username": "x", "full_name": "rk"}).first_name, "")
        self.assertEqual(LeadEnrichment.from_dict({"username": "x", "full_name": "🔥"}).first_name, "")


class TestGenerator(unittest.TestCase):
    def setUp(self):
        self.gen = TemplateGenerator()

    def _lead(self, **kw):
        base = {"username": "u", "full_name": "Priya Sharma"}
        base.update(kw)
        return LeadEnrichment.from_dict(base)

    def test_no_unfilled_placeholders_ever(self):
        # exhaustively across many seeds and signal combinations
        leads = [
            self._lead(is_business="yes", category="Fitness Trainer", top_hashtags="#fit"),
            self._lead(top_hashtags="#travel #35mm"),
            self._lead(inferred_activity="video-first (reels/short-form)"),
            self._lead(inferred_activity="carousel-heavy (educational/portfolio)"),
            self._lead(inferred_activity="photo/lifestyle"),
            self._lead(inferred_activity="mixed media"),
            self._lead(full_name="", inferred_activity="no public posts"),  # thin
        ]
        for lead in leads:
            for attempt in range(25):
                msg = self.gen.generate(lead, attempt=attempt)
                self.assertNotRegex(msg.text, r"[{}%]")
                self.assertTrue(msg.text[0].isupper())

    def test_grounding_uses_real_fields_only(self):
        biz = self._lead(is_business="yes", category="Fitness Trainer", top_hashtags="#x")
        msg = self.gen.generate(biz)
        self.assertTrue(msg.grounded)
        self.assertIn("category", msg.used_fields)
        self.assertIn("fitness trainer", msg.text.lower())

    def test_topic_hook_mentions_topic(self):
        msg = self.gen.generate(self._lead(top_hashtags="#pottery"))
        self.assertIn("pottery", msg.text.lower())
        self.assertIn("top_hashtags", msg.used_fields)

    def test_thin_lead_is_generic_not_fabricated(self):
        msg = self.gen.generate(self._lead(full_name="", inferred_activity="no public posts"))
        self.assertFalse(msg.grounded)
        self.assertEqual(msg.used_fields, [])

    def test_deterministic_seed_and_regenerate_varies(self):
        lead = self._lead(top_hashtags="#a")
        a1 = self.gen.generate(lead, attempt=0).text
        a1b = self.gen.generate(lead, attempt=0).text
        self.assertEqual(a1, a1b)  # same seed -> stable
        variants_seen = {self.gen.generate(lead, attempt=i).text for i in range(8)}
        self.assertGreater(len(variants_seen), 1)  # regenerate -> variety

    def test_uses_first_name_when_present(self):
        self.assertIn("Priya", self.gen.generate(self._lead(top_hashtags="#a")).text)

    def test_tail_appended_and_length_capped(self):
        lead = self._lead(top_hashtags="#a")
        msg = self.gen.generate(lead, GenerationContext(tail="Would love to collaborate."))
        self.assertIn("collaborate", msg.text.lower())
        capped = self.gen.generate(lead, GenerationContext(tail="x " * 200, max_chars=120))
        self.assertLessEqual(len(capped.text), 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
