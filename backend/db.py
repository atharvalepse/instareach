"""SQLite is the single source of truth.

This replaces the old project's three-way split (SQLite + Google Sheets +
in-memory globals). Everything — campaigns, contacts, their state-machine
status, and an append-only event log — lives here. Sheets become an optional
export later, never the brain.

Pure stdlib (sqlite3): zero install, runs anywhere.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    tone          TEXT DEFAULT 'casual',
    tail          TEXT DEFAULT '',
    sequence_json TEXT DEFAULT '[]',            -- [{body, wait_hours}, ...] (step 0 = opener)
    status        TEXT DEFAULT 'draft',         -- draft|running|paused|done
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    username        TEXT NOT NULL,
    enrichment_json TEXT DEFAULT '{}',         -- full scraped row, for (re)generation
    state           TEXT DEFAULT 'queued',     -- see models.STATES
    message_number  INTEGER DEFAULT 0,         -- how many msgs sent in the sequence
    last_message    TEXT DEFAULT '',
    next_action_at  TEXT,                       -- when the follow-up scheduler should act
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(campaign_id, username)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    username    TEXT,
    type        TEXT NOT NULL,                  -- ingested|sent|seen|replied|failed|skipped|suppressed
    detail      TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Outbox: the queue between the brain (scheduler) and the hands (extension).
-- The scheduler enqueues a composed message; the browser extension pulls it,
-- delivers it from the real IG session, and reports the result back.
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  TEXT NOT NULL,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    username     TEXT NOT NULL,
    text         TEXT NOT NULL,
    step_index   INTEGER NOT NULL,          -- which sequence step this delivers
    status       TEXT DEFAULT 'pending',    -- pending | done | failed
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username);
CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
"""


def connect(path: str = "backend/outreach.db") -> sqlite3.Connection:
    """Open (and initialize) the database. Use ':memory:' for tests."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """Idempotent migrations for DB files created by an older schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)")}
    if "sequence_json" not in cols:
        conn.execute("ALTER TABLE campaigns ADD COLUMN sequence_json TEXT DEFAULT '[]'")
    conn.commit()


def log_event(conn, contact_id, username, type_, detail=""):
    conn.execute(
        "INSERT INTO events (contact_id, username, type, detail) VALUES (?,?,?,?)",
        (contact_id, username, type_, detail),
    )
