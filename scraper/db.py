"""SQLite store for candidates. One row per URL the agent looked at, including triage failures (the log)."""
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH

# Status values from the revised design, plus the agent-side outcomes that never reach the queue.
QUEUE_STATUSES = ("queued", "approved", "rejected", "duplicate", "skipped")
LOG_STATUSES = ("triaged_out", "extract_failed", "fetch_failed")
ALL_STATUSES = QUEUE_STATUSES + LOG_STATUSES

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  source_name TEXT,
  source_domain TEXT,
  provenance INTEGER,
  status TEXT NOT NULL,
  title TEXT,
  organisation TEXT,
  problem TEXT,
  method TEXT,
  outcome TEXT,
  year INTEGER,
  triage_json TEXT,
  extraction_json TEXT,
  text_excerpt TEXT,
  possible_duplicate_of TEXT,
  reason TEXT,
  discovered_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def url_known(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM candidates WHERE url = ?", (url,)).fetchone() is not None


def upsert_candidate(conn: sqlite3.Connection, **fields: Any) -> int:
    """Insert or update by URL. JSON-serialises triage/extraction dicts."""
    for key in ("triage_json", "extraction_json"):
        if key in fields and not isinstance(fields[key], (str, type(None))):
            fields[key] = json.dumps(fields[key], ensure_ascii=False)
    fields.setdefault("discovered_at", now())
    columns = list(fields)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "url")
    conn.execute(
        f"INSERT INTO candidates ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(url) DO UPDATE SET {updates}",
        [fields[c] for c in columns],
    )
    conn.commit()
    row = conn.execute("SELECT id FROM candidates WHERE url = ?", (fields["url"],)).fetchone()
    return int(row["id"])


def set_decision(conn: sqlite3.Connection, candidate_id: int, status: str, reason: Optional[str] = None) -> None:
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown status {status}")
    conn.execute(
        "UPDATE candidates SET status = ?, reason = ?, decided_at = ? WHERE id = ?",
        (status, reason, now(), candidate_id),
