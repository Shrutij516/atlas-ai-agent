from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# This database also backs phase 2's human-in-the-loop audit log, so this
# module stays generic — a connection helper plus table-specific functions,
# not itinerary-only.
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                proposed_city TEXT NOT NULL,
                proposed_start_date TEXT NOT NULL,
                proposed_end_date TEXT NOT NULL,
                proposed_notes TEXT,
                risk_level TEXT NOT NULL,
                risk_reasons TEXT NOT NULL,
                context_note TEXT,
                human_decision TEXT,
                final_city TEXT,
                final_start_date TEXT,
                final_end_date TEXT,
                final_notes TEXT,
                proposed_at TEXT NOT NULL,
                decided_at TEXT
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


def insert_audit_log_proposal(
    thread_id: str,
    proposed_city: str,
    proposed_start_date: str,
    proposed_end_date: str,
    proposed_notes: str,
    risk_level: str,
    risk_reasons: list[str],
    context_note: str,
) -> int:
    """Record a proposal the moment it interrupts, before any human decision."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_log (
                thread_id, proposed_city, proposed_start_date, proposed_end_date,
                proposed_notes, risk_level, risk_reasons, context_note, proposed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                proposed_city,
                proposed_start_date,
                proposed_end_date,
                proposed_notes,
                risk_level,
                json.dumps(risk_reasons),
                context_note,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return cursor.lastrowid


def update_audit_log_decision(
    thread_id: str,
    human_decision: str,
    final_city: str | None,
    final_start_date: str | None,
    final_end_date: str | None,
    final_notes: str | None,
) -> None:
    """Update the most recent undecided proposal for this thread.

    Scoped by thread_id + "still pending" (decided_at IS NULL) rather than a
    row id, since the caller (a resumed tool call) never saw the row that
    insert_audit_log_proposal created for it — that insert happens in a
    different process step (see main.py's interrupt handling).
    """
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audit_log
            SET human_decision = ?,
                final_city = ?,
                final_start_date = ?,
                final_end_date = ?,
                final_notes = ?,
                decided_at = ?
            WHERE id = (
                SELECT id FROM audit_log
                WHERE thread_id = ? AND decided_at IS NULL
                ORDER BY id DESC
                LIMIT 1
            )
            """,
            (
                human_decision,
                final_city,
                final_start_date,
                final_end_date,
                final_notes,
                datetime.now(timezone.utc).isoformat(),
                thread_id,
            ),
        )


def list_audit_log() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return [dict(row) for row in rows]


# Runs once at import time, which happens during process startup via the
# tools.py -> db.py import chain — no separate FastAPI startup hook needed.
init_db()
