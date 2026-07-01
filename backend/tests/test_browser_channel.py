#!/usr/bin/env python3
"""Browser-channel (outbox) tests — the async enqueue → deliver → report flow.

Simulates the extension by calling next_pending() + apply_send_result() directly,
so the full deferred state machine is verified without a browser or Instagram.
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
from ingest import ingest_leads  # noqa: E402
import scheduler  # noqa: E402

T0 = datetime(2026, 1, 1, 12, 0, 0)
LEAD = {"username": "priya", "full_name": "Priya Sharma", "is_business": "yes", "category": "Fitness Trainer"}
SEQ = json.dumps([
    {"body": "Would love to collab.", "wait_hours": 0},
    {"body": "Circling back!", "wait_hours": 48},
])


def setup(seq=SEQ, leads=(LEAD,)):
    c = connect(":memory:")
    c.execute("INSERT INTO campaigns (id, name, tone, sequence_json, status) VALUES (?,?,?,?,?)",
              ("c1", "A", "casual", seq, "running"))
    c.commit()
    ingest_leads(c, "c1", list(leads))
    return c


def contact(c, u="priya"):
    return dict(c.execute("SELECT state, message_number FROM contacts WHERE username=?", (u,)).fetchone())


class TestOutbox(unittest.TestCase):
    def test_enqueue_writes_outbox_and_marks_pending(self):
        c = setup()
        r = scheduler.enqueue_due(c, now=T0)
        self.assertEqual(r["queued"], 1)
        self.assertEqual(contact(c)["state"], "pending_send")
        self.assertEqual(contact(c)["message_number"], 0)          # not bumped yet
        pend = scheduler.next_pending(c)
        self.assertEqual(len(pend), 1)
        self.assertIn("Priya", pend[0]["text"])                    # grounded opener composed
        self.assertIn("Would love to collab", pend[0]["text"])

    def test_no_double_enqueue(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        r = scheduler.enqueue_due(c, now=T0)                        # pending_send excluded
        self.assertEqual(r["queued"], 0)

    def test_result_ok_advances_and_schedules_followup(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        oid = scheduler.next_pending(c)[0]["id"]
        self.assertTrue(scheduler.apply_send_result(c, oid, "ok", now=T0))
        row = contact(c)
        self.assertEqual(row["state"], "sent")
        self.assertEqual(row["message_number"], 1)
        nxt = c.execute("SELECT next_action_at FROM contacts WHERE username='priya'").fetchone()[0]
        self.assertIsNotNone(nxt)                                   # follow-up scheduled
        self.assertEqual(c.execute("SELECT status FROM outbox WHERE id=?", (oid,)).fetchone()[0], "done")

    def test_full_flow_intro_then_followup(self):
        c = setup()
        # intro
        scheduler.enqueue_due(c, now=T0)
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "ok", now=T0)
        # follow-up not due yet
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=1))["queued"], 0)
        # due after wait -> enqueues step 2
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=49))["queued"], 1)
        pend = scheduler.next_pending(c)[0]
        self.assertIn("Circling back", pend["text"])
        scheduler.apply_send_result(c, pend["id"], "ok", now=T0 + timedelta(hours=49))
        self.assertEqual(contact(c), {"state": "done", "message_number": 2})   # sequence complete

    def test_result_failed_marks_contact_failed(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "failed", now=T0)
        self.assertEqual(contact(c)["state"], "failed")

    def test_result_blocked_pauses_and_reverts(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "blocked", now=T0)
        self.assertEqual(contact(c)["state"], "queued")            # reverted (step 0)
        self.assertEqual(c.execute("SELECT status FROM campaigns WHERE id='c1'").fetchone()[0], "paused")

    def test_reply_while_in_flight_is_respected(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        scheduler.mark_event(c, "c1", "priya", "replied")           # they reply before we hear back
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "ok", now=T0)
        self.assertEqual(contact(c)["state"], "replied")           # not revived to 'sent'

    def test_stale_result_ignored(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        oid = scheduler.next_pending(c)[0]["id"]
        scheduler.apply_send_result(c, oid, "ok", now=T0)
        self.assertFalse(scheduler.apply_send_result(c, oid, "ok", now=T0))  # already handled


if __name__ == "__main__":
    unittest.main(verbosity=2)
