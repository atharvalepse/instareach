"""Single source of truth — works with ZERO config, upgrades to Supabase.

- No DATABASE_URL  -> local SQLite file (zero setup; on Railway it works but
  resets on redeploy since the container disk is ephemeral).
- DATABASE_URL set -> Postgres / Supabase (persistent).

The rest of the codebase is written with `?` placeholders and sqlite3.Row-style
rows; a thin Conn wrapper adapts both dialects so nothing else had to change.
"""

import os
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_PG = bool(DATABASE_URL)
SQLITE_PATH = os.environ.get("OUTREACH_DB", os.path.join(os.path.dirname(__file__), "outreach.db"))

if IS_PG:
    import psycopg
else:
    import sqlite3

# --- schema (one per dialect) -----------------------------------------------
PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS campaigns (
        id text PRIMARY KEY, name text NOT NULL, tone text DEFAULT 'casual',
        tail text DEFAULT '', sequence_json text DEFAULT '[]',
        status text DEFAULT 'draft', created_at timestamp DEFAULT (now() AT TIME ZONE 'utc'))""",
    """CREATE TABLE IF NOT EXISTS contacts (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        campaign_id text NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        username text NOT NULL, enrichment_json text DEFAULT '{}',
        state text DEFAULT 'queued', message_number int DEFAULT 0,
        last_message text DEFAULT '', next_action_at text,
        created_at timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        updated_at timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        UNIQUE(campaign_id, username))""",
    """CREATE TABLE IF NOT EXISTS events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        contact_id bigint REFERENCES contacts(id) ON DELETE CASCADE,
        username text, type text NOT NULL, detail text DEFAULT '',
        created_at timestamp DEFAULT (now() AT TIME ZONE 'utc'))""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        campaign_id text NOT NULL,
        contact_id bigint NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        username text NOT NULL, text text NOT NULL, step_index int NOT NULL,
        status text DEFAULT 'pending', created_at timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        updated_at timestamp DEFAULT (now() AT TIME ZONE 'utc'))""",
    "CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username)",
    "CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)",
]
SQLITE_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, tone TEXT DEFAULT 'casual',
        tail TEXT DEFAULT '', sequence_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'draft', created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        username TEXT NOT NULL, enrichment_json TEXT DEFAULT '{}',
        state TEXT DEFAULT 'queued', message_number INTEGER DEFAULT 0,
        last_message TEXT DEFAULT '', next_action_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(campaign_id, username))""",
    """CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
        username TEXT, type TEXT NOT NULL, detail TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
        contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        username TEXT NOT NULL, text TEXT NOT NULL, step_index INTEGER NOT NULL,
        status TEXT DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username)",
    "CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)",
]


class HybridRow:
    """Postgres row supporting row['col'], row[0], and dict(row) — like sqlite3.Row."""
    __slots__ = ("_cols", "_vals", "_map")

    def __init__(self, cols, vals):
        self._cols, self._vals, self._map = cols, list(vals), dict(zip(cols, vals))

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else self._map[k]

    def get(self, k, default=None):
        return self._map.get(k, default)

    def keys(self):
        return self._cols

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


def _hybrid_row(cursor):
    cols = [d.name for d in (cursor.description or [])]
    return lambda values: HybridRow(cols, values)


class Conn:
    """Uniform DB handle over sqlite3 / psycopg (`?` placeholders, row['col']/row[0])."""

    def __init__(self, raw, is_pg):
        self._raw, self._pg = raw, is_pg

    def execute(self, sql, params=()):
        if self._pg:
            cur = self._raw.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            return cur
        # sqlite: datetime params -> comparable 'YYYY-MM-DD HH:MM:SS' text
        params = tuple(p.strftime("%Y-%m-%d %H:%M:%S") if isinstance(p, datetime) else p for p in params)
        return self._raw.execute(sql, params)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


def connect(dsn=None) -> Conn:
    if IS_PG:
        raw = psycopg.connect(dsn or DATABASE_URL, row_factory=_hybrid_row, prepare_threshold=None)
        _init_pg(raw)
        return Conn(raw, True)
    raw = sqlite3.connect(dsn or SQLITE_PATH)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    _init_sqlite(raw)
    return Conn(raw, False)


def _init_pg(raw):
    with raw.cursor() as cur:
        for stmt in PG_SCHEMA:
            cur.execute(stmt)
        cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sequence_json text DEFAULT '[]'")
    raw.commit()


def _init_sqlite(raw):
    for stmt in SQLITE_SCHEMA:
        raw.execute(stmt)
    cols = {r[1] for r in raw.execute("PRAGMA table_info(campaigns)")}
    if "sequence_json" not in cols:
        raw.execute("ALTER TABLE campaigns ADD COLUMN sequence_json TEXT DEFAULT '[]'")
    raw.commit()


def backend_name():
    return "postgres" if IS_PG else "sqlite"


def log_event(conn, contact_id, username, type_, detail="", ts=None):
    """ts (a datetime) pins the logical event time — used so send caps count
    correctly under an injected clock in tests. Defaults to the DB's now."""
    if ts is not None:
        conn.execute(
            "INSERT INTO events (contact_id, username, type, detail, created_at) VALUES (?,?,?,?,?)",
            (contact_id, username, type_, detail, ts),
        )
    else:
        conn.execute(
            "INSERT INTO events (contact_id, username, type, detail) VALUES (?,?,?,?)",
            (contact_id, username, type_, detail),
        )


def reset_all(conn):
    """Wipe all data (tests only)."""
    if IS_PG:
        conn.execute("TRUNCATE outbox, events, contacts, campaigns RESTART IDENTITY CASCADE")
    else:
        for t in ("outbox", "events", "contacts", "campaigns"):
            conn.execute(f"DELETE FROM {t}")
    conn.commit()
