from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# Phase 2 (human-in-the-loop) will also use this database for an audit log
# of proposals and approval decisions, so keep this module generic — a
# connection helper plus table-specific functions, not itinerary-only.
DB_PATH = Path(__file__).resolve().parent / "atlas.db"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS itinerary_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def insert_itinerary_item(
    city: str, start_date: str, end_date: str, notes: str = ""
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO itinerary_items (city, start_date, end_date, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (city, start_date, end_date, notes, datetime.now(timezone.utc).isoformat()),
        )
        return cursor.lastrowid


def list_itinerary_items() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, city, start_date, end_date, notes, created_at
            FROM itinerary_items
            ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]


# Runs once at import time, which happens during process startup via the
# tools.py -> db.py import chain — no separate FastAPI startup hook needed.
init_db()
