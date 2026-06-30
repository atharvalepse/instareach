"""The follow-up engine — durable, reply-aware message sequencing.

`run_due` is one pass of the loop: it sends message 1 to fresh contacts, and
later messages only to contacts whose follow-up time has arrived AND who haven't
replied. ALL state lives in the DB (contact.state, message_number,
next_action_at), so a process restart simply resumes — this is the real version
of what the old tool faked with manual re-runs.

`now` is injectable so follow-up waits are testable without real time passing.
Nothing here touches Instagram directly: it calls a SendChannel, which in tests
and the UI is DryRunChannel.
"""

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))                   # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # repo root
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

import models  # noqa: E402
from db import connect, log_event  # noqa: E402


# --- message composition -----------------------------------------------------
def compose_message(gen, lead: LeadEnrichment, ctx: GenerationContext,
                    step_index: int, body: str) -> str:
    """Step 0 = full grounded opener (hook + your ask). Steps 1+ = light nudge."""
    body = (body or "").strip()
    if step_index == 0:
        return gen.generate(lead, replace(ctx, tail=body)).text
    name = lead.first_name or "there"
    nudge = body.replace("{{first_name}}", name)
    greeting = f"Hey {lead.first_name}" if lead.first_name else "Hey there"
    return " ".join(f"{greeting}, {nudge}".split())


# --- one pass of the loop ----------------------------------------------------
def run_due(conn, channel, gen=None, now=None, max_sends=100) -> dict:
    """Process every contact that's due an action right now. Returns a summary."""
    gen = gen or TemplateGenerator()
    now = now or datetime.utcnow()
    s = {"sent": 0, "completed": 0, "failed": 0, "blocked": 0, "skipped": 0}

    rows = conn.execute(
        """SELECT c.id, c.username, c.state, c.message_number, c.next_action_at,
                  c.enrichment_json, ca.id AS cid, ca.sequence_json, ca.tone
           FROM contacts c JOIN campaigns ca ON ca.id = c.campaign_id
           WHERE ca.status = 'running' AND c.state IN ('queued','sent','seen')
           ORDER BY c.id"""
    ).fetchall()

    paused = set()
    for r in rows:
        if s["sent"] >= max_sends:
            break
        if r["cid"] in paused:
            continue
        seq = json.loads(r["sequence_json"] or "[]")
        if not seq:
            continue

        msg_num = r["message_number"]            # how many messages already sent
        if r["state"] == "queued":
            step_index = 0
        else:
            if msg_num >= len(seq):
                conn.execute("UPDATE contacts SET state='done', updated_at=? WHERE id=?",
                             (now.isoformat(), r["id"]))
                continue
            nxt = r["next_action_at"]
            if not nxt or datetime.fromisoformat(nxt) > now:
                s["skipped"] += 1                # follow-up not due yet
                continue
            step_index = msg_num

        lead = LeadEnrichment.from_dict(json.loads(r["enrichment_json"] or "{}"))
        ctx = GenerationContext(tone=r["tone"] or "casual")
        text = compose_message(gen, lead, ctx, step_index, seq[step_index].get("body", ""))

        res = channel.send(r["username"], text)
        if res.blocked:
            conn.execute("UPDATE campaigns SET status='paused' WHERE id=?", (r["cid"],))
            log_event(conn, r["id"], r["username"], "failed", f"blocked: {res.detail}")
            paused.add(r["cid"])
            s["blocked"] += 1
            continue
        if not res.ok:
            conn.execute("UPDATE contacts SET state='failed', updated_at=? WHERE id=?",
                         (now.isoformat(), r["id"]))
            log_event(conn, r["id"], r["username"], "failed", res.detail)
            s["failed"] += 1
            continue

        new_num = step_index + 1
        if new_num < len(seq):
            wait_h = float(seq[new_num].get("wait_hours", 48))
            next_at = (now + timedelta(hours=wait_h)).isoformat()
            state = "sent"
        else:
            next_at = None
            state = "done"
            s["completed"] += 1
        conn.execute(
            """UPDATE contacts SET state=?, message_number=?, last_message=?,
               next_action_at=?, updated_at=? WHERE id=?""",
            (state, new_num, text, next_at, now.isoformat(), r["id"]),
        )
        log_event(conn, r["id"], r["username"], "sent", f"step {new_num}/{len(seq)}")
        s["sent"] += 1

    conn.commit()
    return s


# --- reply / read signals (fed in via API/UI today; auto-poller later) -------
def mark_event(conn, campaign_id, username, type_) -> bool:
    """type_ = 'replied' (permanent stop) | 'seen' (still eligible for follow-up)."""
    row = conn.execute(
        "SELECT id, state FROM contacts WHERE campaign_id=? AND username=?",
        (campaign_id, username),
    ).fetchone()
    if not row:
        return False
    if type_ == "replied":
        conn.execute("UPDATE contacts SET state='replied', updated_at=datetime('now') WHERE id=?", (row["id"],))
    elif type_ == "seen":
        if row["state"] == models.SENT:
            conn.execute("UPDATE contacts SET state='seen', updated_at=datetime('now') WHERE id=?", (row["id"],))
    else:
        return False
    log_event(conn, row["id"], username, type_, "")
    conn.commit()
    return True


# --- standalone durable runner ----------------------------------------------
def run_forever(db_path, interval=60, channel=None):
    """Tick forever; state is in the DB so restarts resume cleanly."""
    import time
    from channels import DryRunChannel
    channel = channel or DryRunChannel()
    gen = TemplateGenerator()
    while True:
        s = run_due(connect(db_path), channel, gen)
        if s["sent"] or s["blocked"] or s["failed"]:
            print(f"[scheduler] {s}")
        time.sleep(interval)


if __name__ == "__main__":
    path = os.environ.get("OUTREACH_DB", os.path.join(os.path.dirname(__file__), "outreach.db"))
    run_forever(path, interval=int(os.environ.get("TICK_INTERVAL", 60)))
