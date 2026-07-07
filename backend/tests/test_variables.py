#!/usr/bin/env python3
"""{{variable}} substitution from uploaded CSV data — run via unittest discover."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))                      # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # repo root

import scheduler  # noqa: E402
from db import connect  # noqa: E402
from dbtest import fresh_db  # noqa: E402
from ingest import ingest_leads  # noqa: E402
from csv_import import rows_from_csv  # noqa: E402
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

GEN = TemplateGenerator()


def compose(step, body, raw):
    lead = LeadEnrichment.from_dict(raw)
    return scheduler.compose_message(GEN, lead, GenerationContext(tone="casual"),
                                     step, body, scheduler.subst_data(raw, lead))


class TestApplyVars(unittest.TestCase):
    def test_fills_and_is_case_insensitive(self):
        d = {"company": "Acme", "city": "NYC"}
        self.assertEqual(scheduler.apply_vars("at {{Company}} in {{CITY}}", d), "at Acme in NYC")

    def test_missing_removed_no_raw_syntax(self):
        out = scheduler.apply_vars("hi {{unknown}} there", {})
        self.assertNotIn("{{", out)
        self.assertEqual(out, "hi there")

    def test_plain_text_unchanged(self):
        self.assertEqual(scheduler.apply_vars("50% off, no vars", {}), "50% off, no vars")


class TestComposeWithVars(unittest.TestCase):
    RAW = {"username": "a", "full_name": "Priya Sharma", "company": "Acme", "city": "NYC"}

    def test_opener_fills_csv_columns(self):
        txt = compose(0, "Saw your team at {{company}} in {{city}}.", self.RAW)
        self.assertIn("Acme", txt)
        self.assertIn("NYC", txt)
        self.assertNotRegex(txt, r"\{\{")

    def test_followup_fills_name_and_columns(self):
        txt = compose(1, "still growing at {{company}}, {{first_name}}?", self.RAW)
        self.assertIn("Acme", txt)
        self.assertIn("Priya", txt)

    def test_opener_with_percent_does_not_crash(self):
        txt = compose(0, "we can get you 50% more reach", self.RAW)
        self.assertIn("50%", txt)

    def test_unfilled_var_stripped_not_sent(self):
        txt = compose(0, "hey, quick one about {{missingcol}}", self.RAW)
        self.assertNotIn("{{", txt)
        self.assertNotIn("missingcol", txt)


class TestEndToEndCSVVars(unittest.TestCase):
    def test_csv_upload_then_substitute(self):
        csv = "instagram,name,company,city\n@priya,Priya,Acme Inc,Mumbai\n"
        rows = rows_from_csv(csv)
        c = fresh_db()
        c.execute("""INSERT INTO campaigns (id,name,tone,sequence_json,status)
                     VALUES ('c1','A','casual',?, 'running')""",
                  (json.dumps([{"body": "love your work at {{company}} in {{city}}!", "wait_hours": 0}]),))
        c.commit()
        ingest_leads(c, "c1", rows)
        scheduler.enqueue_due(c)
        text = scheduler.next_pending(c)[0]["text"]
        self.assertIn("Acme Inc", text)
        self.assertIn("Mumbai", text)
        self.assertNotRegex(text, r"\{\{")


if __name__ == "__main__":
    unittest.main(verbosity=2)
