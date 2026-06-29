"""Contact state machine + tiny row helpers.

The state column drives everything downstream (who to send, who to follow up,
who to never touch again). Phase 0 only sets QUEUED / SUPPRESSED at ingest; the
send loop and follow-up scheduler (Phase 1–2) advance the rest.
"""

# --- contact states ----------------------------------------------------------
QUEUED = "queued"            # ingested, ready for message 1
SENT = "sent"               # a message went out, awaiting read/reply
SEEN = "seen"               # recipient opened it, no reply yet
REPLIED = "replied"         # recipient replied -> STOP following up
FOLLOW_UP_DUE = "follow_up_due"  # eligible for the next message in the sequence
DONE = "done"               # sequence exhausted, no reply
FAILED = "failed"           # send error (couldn't resolve / blocked / etc.)
OPTED_OUT = "opted_out"     # asked not to be contacted -> permanent suppress
SUPPRESSED = "suppressed"   # skipped at ingest (already contacted elsewhere)

STATES = {
    QUEUED, SENT, SEEN, REPLIED, FOLLOW_UP_DUE, DONE, FAILED, OPTED_OUT, SUPPRESSED,
}

# States that count as "already engaged" for cross-campaign suppression.
ENGAGED_STATES = {SENT, SEEN, REPLIED, FOLLOW_UP_DUE, DONE}


def row_to_dict(row):
    return dict(row) if row is not None else None
