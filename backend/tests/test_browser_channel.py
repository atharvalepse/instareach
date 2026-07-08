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
from dbtest import fresh_db  # noqa: E402
from ingest import ingest_leads  # noqa: E402
import scheduler  # noqa: E402

T0 = datetime(2026, 1, 1, 12, 0, 0)
LEAD = {"username": "priya", "full_name": "Priya Sharma", "is_business": "yes", "category": "Fitness Trainer"}
SEQ = json.dumps([
    {"body": "Hi {{first_name}}, Would love to collab.", "wait_hours": 0},
    {"body": "Circling back!", "wait_hours": 48},
])


def setup(seq=SEQ, leads=(LEAD,)):
    c = fresh_db()
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


class TestSendCaps(unittest.TestCase):
    """Ban-safety: enqueue must never exceed the hourly/daily send caps."""

    def setUp(self):
        self._h, self._d = scheduler.HOURLY_CAP, scheduler.DAILY_CAP
        scheduler.HOURLY_CAP, scheduler.DAILY_CAP = 3, 5

    def tearDown(self):
        scheduler.HOURLY_CAP, scheduler.DAILY_CAP = self._h, self._d

    def _campaign_with(self, n):
        c = fresh_db()
        c.execute("INSERT INTO campaigns (id, name, tone, sequence_json, status) VALUES ('c1','A','casual',?, 'running')",
                  (json.dumps([{"body": "hi", "wait_hours": 0}]),))
        c.commit()
        ingest_leads(c, "c1", [{"username": f"u{i}"} for i in range(n)])
        return c

    def _deliver_all(self, c, now):
        for it in scheduler.next_pending(c, 100):
            scheduler.apply_send_result(c, it["id"], "ok", now=now)

    def test_enqueue_capped_at_hourly(self):
        c = self._campaign_with(10)
        self.assertEqual(scheduler.enqueue_due(c, now=T0)["queued"], 3)   # hourly cap = 3
        self.assertEqual(len(scheduler.next_pending(c, 100)), 3)

    def test_in_flight_counts_against_cap(self):
        c = self._campaign_with(10)
        scheduler.enqueue_due(c, now=T0)                                  # 3 pending
        self.assertEqual(scheduler.enqueue_due(c, now=T0)["queued"], 0)   # no headroom (3 in flight)

    def test_hourly_window_rolls_but_daily_binds(self):
        c = self._campaign_with(10)
        scheduler.enqueue_due(c, now=T0); self._deliver_all(c, T0)        # 3 delivered
        # +1h: hourly resets, but daily cap 5 leaves room for only 2 more
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=1, minutes=1))["queued"], 2)
        self._deliver_all(c, T0 + timedelta(hours=1, minutes=1))          # 5 delivered total
        # still within the day -> daily cap reached -> nothing
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=2))["queued"], 0)

    def test_daily_window_rolls(self):
        c = self._campaign_with(10)
        scheduler.enqueue_due(c, now=T0); self._deliver_all(c, T0)
        scheduler.enqueue_due(c, now=T0 + timedelta(hours=1, minutes=1)); self._deliver_all(c, T0 + timedelta(hours=1, minutes=1))
        # +25h: daily window has rolled -> hourly cap applies again -> 3
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=25))["queued"], 3)

    def test_quota_report(self):
        c = self._campaign_with(10)
        q = scheduler.quota(c, now=T0)
        self.assertEqual(q["remaining"], 3)
        self.assertEqual(q["hourly_cap"], 3)

    def test_followups_prioritized_over_intros_within_cap(self):
        # one contact with a DUE follow-up + one brand-new intro, cap of 1
        scheduler.HOURLY_CAP, scheduler.DAILY_CAP = 1, 10
        c = fresh_db()
        c.execute("INSERT INTO campaigns (id,name,tone,sequence_json,status) VALUES ('c1','A','casual',?, 'running')",
                  (SEQ,))  # 2-step: opener + follow-up @48h
        # contact A: already got msg 1, follow-up is due now
        c.execute("""INSERT INTO contacts (campaign_id, username, enrichment_json, state, message_number, next_action_at)
                     VALUES ('c1','veteran','{\"username\":\"veteran\"}','sent',1,?)""", (T0.isoformat(),))
        # contact B: brand new, never messaged
        c.execute("""INSERT INTO contacts (campaign_id, username, enrichment_json, state)
                     VALUES ('c1','newbie','{\"username\":\"newbie\"}','queued')""")
        c.commit()
        r = scheduler.enqueue_due(c, now=T0)
        self.assertEqual(r["queued"], 1)                       # cap = 1
        pend = scheduler.next_pending(c)[0]
        self.assertEqual(pend["username"], "veteran")          # the FOLLOW-UP won, not the intro
        self.assertIn("Circling back", pend["text"])


class TestReplyPoller(unittest.TestCase):
    def test_watchlist_only_awaiting_reply_in_running(self):
        c = setup(leads=(LEAD, {"username": "maya"}))
        scheduler.enqueue_due(c, now=T0)
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "ok", now=T0)  # priya -> sent
        wl = scheduler.watchlist(c)
        self.assertIn("priya", wl)                 # sent -> awaiting reply
        self.assertIn("maya", wl)                  # pending_send -> also awaiting
        # a done/paused campaign should not appear
        c.execute("UPDATE campaigns SET status='paused' WHERE id='c1'")
        c.commit()
        self.assertEqual(scheduler.watchlist(c), [])

    def test_mark_replied_global_stops_followups(self):
        c = setup()
        scheduler.enqueue_due(c, now=T0)
        scheduler.apply_send_result(c, scheduler.next_pending(c)[0]["id"], "ok", now=T0)
        n = scheduler.mark_replied_global(c, "priya")
        self.assertEqual(n, 1)
        self.assertEqual(contact(c)["state"], "replied")
        # and no follow-up is ever enqueued again
        self.assertEqual(scheduler.enqueue_due(c, now=T0 + timedelta(hours=200))["queued"], 0)

    def test_mark_replied_global_noop_for_unknown(self):
        c = setup()
        self.assertEqual(scheduler.mark_replied_global(c, "ghost"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
