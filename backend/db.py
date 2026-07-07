"""Postgres is the single source of truth (Supabase in production).

Connect with a standard Postgres connection string in the DATABASE_URL env var
(Supabase → Project Settings → Database → Connection string; use the pooler for
a hosted deploy). Locally you can point it at any Postgres.

Design notes:
- A thin Conn wrapper keeps the rest of the codebase written with `?`
  placeholders (translated to psycopg's `%s`) and sqlite3.Row-style rows that
  support BOTH row["col"] and row[0], so ingest/scheduler/app needed almost no
  query changes in the switch from SQLite.
- Timestamps: created_at/updated_at are `timestamp` (naive UTC, matching the
  code's datetime.utcnow()); next_action_at stays TEXT (ISO strings parsed with
  datetime.fromisoformat).
"""

import os

import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS campaigns (
        id            text PRIMARY KEY,
        name          text NOT NULL,
        tone          text DEFAULT 'casual',
        tail          text DEFAULT '',
        sequence_json text DEFAULT '[]',
        status        text DEFAULT 'draft',
        created_at    timestamp DEFAULT (now() AT TIME ZONE 'utc')
    )""",
    """CREATE TABLE IF NOT EXISTS contacts (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        campaign_id     text NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        username        text NOT NULL,
        enrichment_json text DEFAULT '{}',
        state           text DEFAULT 'queued',
        message_number  int DEFAULT 0,
        last_message    text DEFAULT '',
        next_action_at  text,
        created_at      timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        updated_at      timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        UNIQUE(campaign_id, username)
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        contact_id bigint REFERENCES contacts(id) ON DELETE CASCADE,
        username   text,
        type       text NOT NULL,
        detail     text DEFAULT '',
        created_at timestamp DEFAULT (now() AT TIME ZONE 'utc')
    )""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        campaign_id text NOT NULL,
        contact_id  bigint NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        username    text NOT NULL,
        text        text NOT NULL,
        step_index  int NOT NULL,
        status      text DEFAULT 'pending',
        created_at  timestamp DEFAULT (now() AT TIME ZONE 'utc'),
        updated_at  timestamp DEFAULT (now() AT TIME ZONE 'utc')
    )""",
    "CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts(username)",
    "CREATE INDEX IF NOT EXISTS idx_contacts_campaign ON contacts(campaign_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status)",
]


class HybridRow:
    """Row that supports both mapping (row['col']) and positional (row[0]) access
    and dict(row) — mirrors sqlite3.Row so callers didn't have to change."""

    __slots__ = ("_cols", "_vals", "_map")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = list(vals)
        self._map = dict(zip(cols, self._vals))

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
    """Uniform DB handle: `?` placeholders, hybrid rows, explicit commit."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

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
    raw = psycopg.connect(dsn or DATABASE_URL, row_factory=_hybrid_row)
    _init(raw)
    return Conn(raw)


def _init(raw):
    with raw.cursor() as cur:
        for stmt in SCHEMA:
            cur.execute(stmt)
        # safety migration for DBs created before sequence_json existed
        cur.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS sequence_json text DEFAULT '[]'")
    raw.commit()


def log_event(conn, contact_id, username, type_, detail="", ts=None):
    """ts (a datetime) pins the logical event time — used so send caps count
    correctly under an injected clock in tests. Defaults to now()."""
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
    conn.execute("TRUNCATE outbox, events, contacts, campaigns RESTART IDENTITY CASCADE")
    conn.commit()
