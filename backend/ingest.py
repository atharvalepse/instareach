"""Lead ingestion — the extension's 'Send to campaign' lands here.

Takes raw scraped rows (exactly the igscrapper full-enrichment shape), and for
each one:
  - validates a username (via LeadEnrichment),
  - dedups within the campaign (UNIQUE constraint),
  - applies cross-campaign suppression (never DM someone already engaged in
    another campaign — a core 'safety + reply-rate' rule),
  - stores the full enrichment so openers can be (re)generated later.

Pure function over a sqlite connection — no web framework needed to test it.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import List

sys.path.insert(0, os.path.dirname(__file__))                   # backend/ for sibling modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # repo root for shared/
from shared.generator import LeadEnrichment  # noqa: E402

import models  # noqa: E402
from db import log_event  # noqa: E402


@dataclass
class IngestSummary:
    inserted: int = 0
    duplicates: int = 0       # already in this campaign
    suppressed: int = 0       # already engaged in another campaign
    errors: List[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "suppressed": self.suppressed,
            "errors": self.errors,
        }


def _engaged_elsewhere(conn, username, campaign_id) -> bool:
    placeholders = ",".join("?" * len(models.ENGAGED_STATES))
    row = conn.execute(
        f"""SELECT 1 FROM contacts
            WHERE username = ? AND campaign_id != ? AND state IN ({placeholders})
            LIMIT 1""",
        (username, campaign_id, *models.ENGAGED_STATES),
    ).fetchone()
    return row is not None


def ingest_leads(conn, campaign_id, rows, suppress_cross_campaign=True) -> IngestSummary:
    summary = IngestSummary()

    if not conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone():
        summary.errors.append(f"campaign '{campaign_id}' does not exist")
        return summary

    for i, row in enumerate(rows):
        try:
            lead = LeadEnrichment.from_dict(row)
        except Exception as e:
            summary.errors.append(f"row {i}: {e}")
            continue

        if not lead.username:
            summary.errors.append(f"row {i}: missing username")
            continue

        if suppress_cross_campaign and _engaged_elsewhere(conn, lead.username, campaign_id):
            summary.suppressed += 1
            log_event(conn, None, lead.username, models.SUPPRESSED, "engaged in another campaign")
            continue

        # ON CONFLICT keeps the transaction alive (an INSERT that raised would
        # abort the whole Postgres transaction). RETURNING tells us insert vs dup.
        cur = conn.execute(
            """INSERT INTO contacts (campaign_id, username, enrichment_json, state)
               VALUES (?,?,?,?)
               ON CONFLICT (campaign_id, username) DO NOTHING
               RETURNING id""",
            (campaign_id, lead.username, json.dumps(row), models.QUEUED),
        )
        inserted = cur.fetchone()
        if inserted:
            log_event(conn, inserted["id"], lead.username, "ingested", "")
            summary.inserted += 1
        else:
            summary.duplicates += 1        # already in this campaign

    conn.commit()
    return summary
