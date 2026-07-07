"""Shared test DB helper.

Tests run against a real Postgres (set DATABASE_URL). We reuse one connection
per test process and TRUNCATE all tables before each test, giving the same
clean-slate isolation the old in-memory SQLite gave for free.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # backend/
from db import connect, reset_all  # noqa: E402

_CONN = None


def fresh_db():
    global _CONN
    if _CONN is None:
        _CONN = connect()
    reset_all(_CONN)
    return _CONN
