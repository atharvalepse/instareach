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
import re
import sys
from dataclasses import replace
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))                   # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # repo root
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

import models  # noqa: E402
from db import connect, log_event  # noqa: E402


# --- {{variable}} substitution from the uploaded CSV/lead data ----------------
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def apply_vars(text: str, data: dict) -> str:
    """Fill {{column}} placeholders from `data` (case-insensitive). Any variable
    with no matching value is removed, so raw {{...}} is never sent. Whitespace
    left by a removed variable is collapsed."""
    if not text:
        return text
    out = _VAR.sub(lambda m: data.get(m.group(1).lower(), ""), text)
    return " ".join(out.split())


def subst_data(raw: dict, lead: LeadEnrichment) -> dict:
    """Build the substitution map: every uploaded CSV column (lowercased) plus a
    few friendly derived names. CSV columns win over derived defaults, and
    {{first_name}} is derived from whatever name-ish column exists."""
    d = {str(k).strip().lower(): ("" if v is None else str(v)) for k, v in (raw or {}).items()}
    name_src = (d.get("first_name") or d.get("name") or d.get("full_name")
                or lead.full_name or lead.first_name or "").strip()
    first = name_src.split()[0] if name_src else "there"
    d.setdefault("first_name", first)
    d.setdefault("name", name_src or first)
    d.setdefault("username", lead.username)
    return d


# --- message composition -----------------------------------------------------
def compose_message(gen, lead: LeadEnrichment, ctx: GenerationContext,
                    step_index: int, body: str, data: dict = None) -> str:
    """Step 0 = full grounded opener (hook + your ask). Steps 1+ = light nudge.
    In both, {{column}} placeholders in `body` are filled from `data` (CSV)."""
    data = data or {}
    body = apply_vars((body or "").strip(), data)
    # a friendly first name for the greeting (from CSV name column if the scraped
    # profile had none) — "there" is our sentinel for "no real name".
    fn = data.get("first_name") or lead.first_name
    if fn == "there":
        fn = ""
    if step_index == 0:
        if fn and not lead.first_name:
            lead.first_name = fn                # so the grounded greeting uses it
        return gen.generate(lead, replace(ctx, tail=body)).text
    greeting = f"Hey {fn}" if fn else "Hey there"
    return " ".join(f"{greeting}, {body}".split())


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

        raw = json.loads(r["enrichment_json"] or "{}")
        lead = LeadEnrichment.from_dict(raw)
        ctx = GenerationContext(tone=r["tone"] or "casual")
        text = compose_message(gen, lead, ctx, step_index, seq[step_index].get("body", ""),
                               subst_data(raw, lead))

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
        log_event(conn, r["id"], r["username"], "sent", f"step {new_num}/{len(seq)}", ts=now)
        s["sent"] += 1

    conn.commit()
    return s


# --- browser channel: enqueue for the extension, apply its result ------------
# Browser sending is asynchronous: the backend can't call the browser, so it
# writes the composed message to the outbox (contact -> pending_send) and the
# extension pulls it. The follow-up state transition is deferred until the
# extension confirms delivery via apply_send_result().
_DUE_SELECT = (
    """SELECT c.id, c.username, c.state, c.message_number, c.next_action_at,
              c.enrichment_json, ca.id AS cid, ca.sequence_json, ca.tone
       FROM contacts c JOIN campaigns ca ON ca.id = c.campaign_id
       WHERE ca.status = 'running' AND c.state IN ('queued','sent','seen')
       -- follow-ups (sent/seen) BEFORE new intros (queued): they're time-sensitive
       -- and warmer, so they must not be starved when the daily cap is tight.
       ORDER BY (c.state = 'queued') ASC, c.id ASC"""
)


def _due_step(row, now):
    """Return the step_index to send for this contact, or None if not due."""
    seq = json.loads(row["sequence_json"] or "[]")
    if not seq:
        return None, seq
    if row["state"] == "queued":
        return 0, seq
    if row["message_number"] >= len(seq):
        return "exhausted", seq
    nxt = row["next_action_at"]
    if not nxt or datetime.fromisoformat(nxt) > now:
        return None, seq
    return row["message_number"], seq


# --- ban-safety caps: hard limits on how many DMs go out per hour / per day ---
# Volume is the real ban vector for cold DMs (spacing alone isn't enough).
# 80/day is a HIGH ceiling that only suits a well-aged, warmed account. LOWER
# both (e.g. HOURLY_CAP=3, DAILY_CAP=10) for a new/burner account and ramp up.
# Follow-ups are prioritized within the cap (see _DUE_SELECT ordering).
HOURLY_CAP = int(os.environ.get("HOURLY_CAP", 10))
DAILY_CAP = int(os.environ.get("DAILY_CAP", 80))


def _sent_since(conn, since_dt):
    # since_dt is a datetime; psycopg adapts it to a timestamp for the comparison
    return conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE type='sent' AND created_at >= ?", (since_dt,)
    ).fetchone()["n"]


def quota(conn, now=None) -> dict:
    """How much send headroom is left, counting delivered + in-flight against caps."""
    now = now or datetime.utcnow()
    pending = conn.execute("SELECT COUNT(*) AS n FROM outbox WHERE status='pending'").fetchone()["n"]
    sent_hour = _sent_since(conn, now - timedelta(hours=1))
    sent_day = _sent_since(conn, now - timedelta(days=1))
    remaining = max(0, min(HOURLY_CAP - sent_hour - pending, DAILY_CAP - sent_day - pending))
    return {
        "hourly_cap": HOURLY_CAP, "daily_cap": DAILY_CAP,
        "sent_last_hour": sent_hour, "sent_last_day": sent_day,
        "in_flight": pending, "remaining": remaining,
    }


def enqueue_due(conn, gen=None, now=None, max_enqueue=100) -> dict:
    """Compose due messages into the outbox — but never exceed the send caps."""
    gen = gen or TemplateGenerator()
    now = now or datetime.utcnow()
    cap = min(max_enqueue, quota(conn, now)["remaining"])   # ← ban-safety gate
    queued = 0
    for r in conn.execute(_DUE_SELECT).fetchall():
        if queued >= cap:
            break
        step_index, seq = _due_step(r, now)
        if step_index is None:
            continue
        if step_index == "exhausted":
            conn.execute("UPDATE contacts SET state='done', updated_at=? WHERE id=?",
                         (now.isoformat(), r["id"]))
            continue
        raw = json.loads(r["enrichment_json"] or "{}")
        lead = LeadEnrichment.from_dict(raw)
        ctx = GenerationContext(tone=r["tone"] or "casual")
        text = compose_message(gen, lead, ctx, step_index, seq[step_index].get("body", ""),
                               subst_data(raw, lead))
        conn.execute(
            """INSERT INTO outbox (campaign_id, contact_id, username, text, step_index, status)
               VALUES (?,?,?,?,?, 'pending')""",
            (r["cid"], r["id"], r["username"], text, step_index),
        )
        conn.execute("UPDATE contacts SET state=?, updated_at=? WHERE id=?",
                     (models.PENDING_SEND, now.isoformat(), r["id"]))
        log_event(conn, r["id"], r["username"], "queued_send", f"step {step_index + 1}/{len(seq)}")
        queued += 1
    conn.commit()
    return {"queued": queued}


def next_pending(conn, limit=10):
    """What the extension polls: the oldest pending outbox items."""
    rows = conn.execute(
        "SELECT id, username, text FROM outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def apply_send_result(conn, outbox_id, status, now=None) -> bool:
    """Extension reports back: status = ok|sent | failed | blocked."""
    now = now or datetime.utcnow()
    o = conn.execute("SELECT * FROM outbox WHERE id=?", (outbox_id,)).fetchone()
    if not o or o["status"] != "pending":
        return False
    contact = conn.execute("SELECT * FROM contacts WHERE id=?", (o["contact_id"],)).fetchone()
    if not contact:
        return False
    camp = conn.execute("SELECT sequence_json FROM campaigns WHERE id=?", (o["campaign_id"],)).fetchone()
    seq = json.loads((camp["sequence_json"] if camp else "[]") or "[]")
    step_index = o["step_index"]

    if status in ("ok", "sent", "done"):
        # If they replied while it was in flight, respect the stop — don't revive.
        if contact["state"] == models.REPLIED:
            conn.execute("UPDATE outbox SET status='done', updated_at=? WHERE id=?", (now.isoformat(), o["id"]))
            conn.commit()
            return True
        new_num = step_index + 1
        if new_num < len(seq):
            next_at = (now + timedelta(hours=float(seq[new_num].get("wait_hours", 48)))).isoformat()
            state = models.SENT
        else:
            next_at, state = None, models.DONE
        conn.execute(
            """UPDATE contacts SET state=?, message_number=?, last_message=?,
               next_action_at=?, updated_at=? WHERE id=?""",
            (state, new_num, o["text"], next_at, now.isoformat(), contact["id"]),
        )
        conn.execute("UPDATE outbox SET status='done', updated_at=? WHERE id=?", (now.isoformat(), o["id"]))
        log_event(conn, contact["id"], o["username"], "sent", f"step {new_num}/{len(seq)} (browser)", ts=now)
    elif status == "blocked":
        # revert so it retries when the campaign is resumed; pause the campaign
        revert = models.QUEUED if step_index == 0 else models.SENT
        next_at = None if step_index == 0 else now.isoformat()
        conn.execute("UPDATE contacts SET state=?, next_action_at=?, updated_at=? WHERE id=?",
                     (revert, next_at, now.isoformat(), contact["id"]))
        conn.execute("UPDATE campaigns SET status='paused' WHERE id=?", (o["campaign_id"],))
        conn.execute("UPDATE outbox SET status='failed', updated_at=? WHERE id=?", (now.isoformat(), o["id"]))
        log_event(conn, contact["id"], o["username"], "failed", "blocked (browser) — campaign paused")
    else:  # failed
        conn.execute("UPDATE contacts SET state='failed', updated_at=? WHERE id=?",
                     (now.isoformat(), contact["id"]))
        conn.execute("UPDATE outbox SET status='failed', updated_at=? WHERE id=?", (now.isoformat(), o["id"]))
        log_event(conn, contact["id"], o["username"], "failed", "send failed (browser)")
    conn.commit()
    return True


# --- reply / read signals (fed in via API/UI today; auto-poller later) -------
def watchlist(conn):
    """Usernames we're awaiting a reply from (sent/seen) in running campaigns —
    what the extension's reply-poller watches the IG inbox for."""
    rows = conn.execute(
        """SELECT DISTINCT c.username FROM contacts c JOIN campaigns ca ON ca.id = c.campaign_id
           WHERE ca.status='running' AND c.state IN ('sent','seen','pending_send')"""
    ).fetchall()
    return [r["username"] for r in rows]


def mark_replied_global(conn, username) -> int:
    """A reply came in on IG — stop follow-ups for this user across all running
    campaigns. Called by the auto reply-poller."""
    rows = conn.execute(
        """SELECT c.id FROM contacts c JOIN campaigns ca ON ca.id = c.campaign_id
           WHERE c.username=? AND ca.status='running'
             AND c.state IN ('sent','seen','pending_send')""",
        (username,),
    ).fetchall()
    for r in rows:
        conn.execute("UPDATE contacts SET state='replied', updated_at=CURRENT_TIMESTAMP WHERE id=?", (r["id"],))
        log_event(conn, r["id"], username, "replied", "auto-detected from IG inbox")
    conn.commit()
    return len(rows)


def mark_event(conn, campaign_id, username, type_) -> bool:
    """type_ = 'replied' (permanent stop) | 'seen' (still eligible for follow-up)."""
    row = conn.execute(
        "SELECT id, state FROM contacts WHERE campaign_id=? AND username=?",
        (campaign_id, username),
    ).fetchone()
    if not row:
        return False
    if type_ == "replied":
        conn.execute("UPDATE contacts SET state='replied', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    elif type_ == "seen":
        if row["state"] == models.SENT:
            conn.execute("UPDATE contacts SET state='seen', updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
    else:
        return False
    log_event(conn, row["id"], username, type_, "")
    conn.commit()
    return True


# --- standalone durable runner ----------------------------------------------
def run_forever(interval=300, mode="enqueue"):
    """Tick forever; state is in the DB so restarts resume cleanly.

    mode='enqueue' (default, browser channel): queue due messages for the
      extension to deliver. mode='dryrun': deliver via DryRunChannel (testing).
    """
    import time
    gen = TemplateGenerator()
    while True:
        c = None
        try:
            c = connect()
            if mode == "dryrun":
                from channels import DryRunChannel
                s = run_due(c, DryRunChannel(), gen)
                if s["sent"] or s["blocked"] or s["failed"]:
                    print(f"[scheduler] {s}")
            else:
                r = enqueue_due(c, gen)
                if r["queued"]:
                    print(f"[scheduler] enqueued {r['queued']}")
        except Exception as e:
            print(f"[scheduler] error: {e}")
        finally:
            if c is not None:
                c.close()
        time.sleep(interval)


if __name__ == "__main__":
    run_forever(interval=int(os.environ.get("AUTO_TICK_SECONDS", 300)),
                mode=os.environ.get("SCHEDULER_MODE", "enqueue"))
