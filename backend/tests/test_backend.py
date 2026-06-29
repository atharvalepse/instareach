#!/usr/bin/env python3
"""Backend Phase-0 tests — run: python3 -m unittest discover -s backend/tests

Covers DB-as-source-of-truth, ingest (dedup + cross-campaign suppression +
errors), the SendChannel contract via DryRunChannel, and the end-to-end
ingest -> generate-opener flow. Pure stdlib + the zero-dep generator; no Flask,
no network, no instagrapi.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))          # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # repo root

from db import connect  # noqa: E402
from ingest import ingest_leads  # noqa: E402
import models  # noqa: E402
from channels import DryRunChannel, ServerChannel  # noqa: E402
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

LEAD = {"username": "@FitWithPriya", "full_name": "Priya Sharma",
        "is_business": "yes", "category": "Fitness Trainer", "top_hashtags": "#fit"}


def mkdb():
    c = connect(":memory:")
    c.execute("INSERT INTO campaigns (id, name) VALUES ('c1', 'A')")
    c.execute("INSERT INTO campaigns (id, name) VALUES ('c2', 'B')")
    c.commit()
    return c


class TestDB(unittest.TestCase):
    def test_schema_and_fk(self):
        c = mkdb()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 2)
        # cascade: deleting a campaign removes its contacts
        ingest_leads(c, "c1", [LEAD])
        c.execute("DELETE FROM campaigns WHERE id='c1'")
        c.commit()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 0)


class TestIngest(unittest.TestCase):
    def test_insert_and_normalizes_username(self):
        c = mkdb()
        s = ingest_leads(c, "c1", [LEAD])
        self.assertEqual(s.inserted, 1)
        row = c.execute("SELECT username, state FROM contacts").fetchone()
        self.assertEqual(row["username"], "FitWithPriya")   # @ stripped
        self.assertEqual(row["state"], models.QUEUED)

    def test_dedup_within_campaign(self):
        c = mkdb()
        s = ingest_leads(c, "c1", [LEAD, LEAD, dict(LEAD, username="other")])
        self.assertEqual(s.inserted, 2)
        self.assertEqual(s.duplicates, 1)

    def test_cross_campaign_suppression(self):
        c = mkdb()
        ingest_leads(c, "c1", [LEAD])
        # mark as engaged in c1
        c.execute("UPDATE contacts SET state='sent' WHERE username='FitWithPriya'")
        c.commit()
        s = ingest_leads(c, "c2", [LEAD])   # same person, different campaign
        self.assertEqual(s.suppressed, 1)
        self.assertEqual(s.inserted, 0)

    def test_no_suppression_when_only_queued_elsewhere(self):
        c = mkdb()
        ingest_leads(c, "c1", [LEAD])       # stays 'queued' (not engaged)
        s = ingest_leads(c, "c2", [LEAD])
        self.assertEqual(s.inserted, 1)     # queued elsewhere != engaged

    def test_errors_for_bad_rows(self):
        c = mkdb()
        s = ingest_leads(c, "c1", [{"full_name": "no username"}, {"username": ""}])
        self.assertEqual(s.inserted, 0)
        self.assertEqual(len(s.errors), 2)

    def test_unknown_campaign(self):
        c = mkdb()
        s = ingest_leads(c, "nope", [LEAD])
        self.assertTrue(s.errors)

    def test_events_logged(self):
        c = mkdb()
        ingest_leads(c, "c1", [LEAD])
        types = [r["type"] for r in c.execute("SELECT type FROM events").fetchall()]
        self.assertIn("ingested", types)


class TestChannels(unittest.TestCase):
    def test_dryrun_records(self):
        ch = DryRunChannel()
        r = ch.send("u", "hi")
        self.assertTrue(r.ok)
        self.assertEqual(ch.sent, [("u", "hi")])

    def test_dryrun_simulated_failure_and_block(self):
        ch = DryRunChannel(fail=["bad"], block=["stop"])
        self.assertFalse(ch.send("bad", "x").ok)
        self.assertTrue(ch.send("stop", "x").blocked)
        self.assertEqual(ch.sent, [])   # neither was delivered

    def test_server_channel_importable_without_instagrapi(self):
        # constructing must not require the optional dep; send() degrades cleanly
        ch = ServerChannel("u", "p")
        r = ch.send("target", "hi")
        self.assertFalse(r.ok)          # instagrapi almost certainly absent here
        self.assertIn("instagrapi", r.detail.lower())


class TestEndToEnd(unittest.TestCase):
    def test_ingest_then_generate_openers(self):
        c = mkdb()
        ingest_leads(c, "c1", [LEAD, {"username": "wanderlens", "full_name": "Maya",
                                      "top_hashtags": "#travelphotography"}])
        gen = TemplateGenerator()
        ctx = GenerationContext(tone="professional", tail="Quick question for you.")
        rows = c.execute("SELECT username, enrichment_json FROM contacts WHERE state='queued'").fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            lead = LeadEnrichment.from_dict(json.loads(r["enrichment_json"]))
            msg = gen.generate(lead, ctx)
            self.assertNotRegex(msg.text, r"[{}%]")
            self.assertIn("Quick question", msg.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
