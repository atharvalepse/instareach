#!/usr/bin/env python3
"""Follow-up scheduler tests — run: python3 -m unittest discover -s backend/tests

Uses DryRunChannel + an injected `now`, so the full reply-aware sequence
(intro → wait → follow-up → reply-stops) is verified with zero Instagram
contact and without real time passing.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))                      # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # repo root

from db import connect  # noqa: E402
from dbtest import fresh_db  # noqa: E402
from ingest import ingest_leads  # noqa: E402
from channels import DryRunChannel  # noqa: E402
import scheduler  # noqa: E402

T0 = datetime(2026, 1, 1, 12, 0, 0)
LEAD = {"username": "priya", "full_name": "Priya Sharma",
        "is_business": "yes", "category": "Fitness Trainer"}
LEAD2 = {"username": "maya", "full_name": "Maya", "top_hashtags": "#travel"}

# 3-step sequence: opener now, follow-up after 48h, final after another 72h
SEQ = json.dumps([
    {"body": "Would love to collab.", "wait_hours": 0},
    {"body": "Just circling back on this!", "wait_hours": 48},
    {"body": "Last nudge — still keen?", "wait_hours": 72},
])


def setup(seq=SEQ, leads=(LEAD,), running=True):
    c = fresh_db()
    c.execute("INSERT INTO campaigns (id, name, tone, sequence_json, status) VALUES (?,?,?,?,?)",
              ("c1", "A", "casual", seq, "running" if running else "draft"))
    c.commit()
    ingest_leads(c, "c1", list(leads))
    return c


def states(c):
    return {r["username"]: (r["state"], r["message_number"])
            for r in c.execute("SELECT username, state, message_number FROM contacts")}


class TestSendLoop(unittest.TestCase):
    def test_intro_sends_to_queued(self):
        c = setup()
        ch = DryRunChannel()
        s = scheduler.run_due(c, ch, now=T0)
        self.assertEqual(s["sent"], 1)
        self.assertEqual(states(c)["priya"], ("sent", 1))
        self.assertEqual(len(ch.sent), 1)
        # opener is the full grounded personalized message + the ask
        self.assertIn("Priya", ch.sent[0][1])
        self.assertIn("Would love to collab", ch.sent[0][1])

    def test_draft_campaign_sends_nothing(self):
        c = setup(running=False)
        s = scheduler.run_due(c, DryRunChannel(), now=T0)
        self.assertEqual(s["sent"], 0)

    def test_followup_not_due_before_wait(self):
        c = setup()
        scheduler.run_due(c, DryRunChannel(), now=T0)              # intro
        s = scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=47))
        self.assertEqual(s["sent"], 0)
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(states(c)["priya"], ("sent", 1))

    def test_followup_fires_after_wait(self):
        c = setup()
        scheduler.run_due(c, DryRunChannel(), now=T0)              # intro
        ch = DryRunChannel()
        s = scheduler.run_due(c, ch, now=T0 + timedelta(hours=49))
        self.assertEqual(s["sent"], 1)
        self.assertEqual(states(c)["priya"], ("sent", 2))
        # follow-up is a light nudge, not a repeated hook
        self.assertIn("circling back", ch.sent[0][1])

    def test_reply_stops_followups(self):
        c = setup()
        scheduler.run_due(c, DryRunChannel(), now=T0)              # intro
        scheduler.mark_event(c, "c1", "priya", "replied")
        s = scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=200))
        self.assertEqual(s["sent"], 0)
        self.assertEqual(states(c)["priya"][0], "replied")

    def test_single_step_never_follows_up(self):
        c = setup(seq=json.dumps([{"body": "hi", "wait_hours": 0}]))
        scheduler.run_due(c, DryRunChannel(), now=T0)
        self.assertEqual(states(c)["priya"], ("done", 1))          # sequence exhausted
        s = scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=999))
        self.assertEqual(s["sent"], 0)

    def test_full_sequence_completes(self):
        c = setup()
        scheduler.run_due(c, DryRunChannel(), now=T0)                       # msg1
        scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=49)) # msg2
        s = scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=200))  # msg3 (final)
        self.assertEqual(s["completed"], 1)
        self.assertEqual(states(c)["priya"], ("done", 3))

    def test_blocked_pauses_campaign(self):
        c = setup(leads=(LEAD, LEAD2))
        ch = DryRunChannel(block=["priya"])
        s = scheduler.run_due(c, ch, now=T0)
        self.assertEqual(s["blocked"], 1)
        self.assertEqual(c.execute("SELECT status FROM campaigns WHERE id='c1'").fetchone()[0], "paused")
        # campaign paused -> maya (later in the list) is not sent to either
        self.assertEqual(states(c)["maya"][0], "queued")

    def test_failure_marks_contact_failed(self):
        c = setup(leads=(LEAD, LEAD2))
        ch = DryRunChannel(fail=["priya"])
        s = scheduler.run_due(c, ch, now=T0)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(states(c)["priya"][0], "failed")
        self.assertEqual(states(c)["maya"], ("sent", 1))           # others still sent

    def test_seen_keeps_followup_eligible(self):
        c = setup()
        scheduler.run_due(c, DryRunChannel(), now=T0)
        scheduler.mark_event(c, "c1", "priya", "seen")
        self.assertEqual(states(c)["priya"][0], "seen")
        s = scheduler.run_due(c, DryRunChannel(), now=T0 + timedelta(hours=49))
        self.assertEqual(s["sent"], 1)                             # seen != replied


if __name__ == "__main__":
    unittest.main(verbosity=2)
