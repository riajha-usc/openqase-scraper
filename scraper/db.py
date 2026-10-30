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
    if "page_text" not in {r["name"] for r in conn.execute("PRAGMA table_info(candidates)")}:
        conn.execute("ALTER TABLE candidates ADD COLUMN page_text TEXT")  # full fetched text, for re-judging from disk
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
    )
    conn.commit()


def get(conn: sqlite3.Connection, candidate_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()


def next_queued(conn: sqlite3.Connection, include_skipped: bool = False) -> Optional[sqlite3.Row]:
    """Highest provenance first, then oldest first, so reviewers see the best-sourced records early."""
    statuses = ("queued", "skipped") if include_skipped else ("queued",)
    marks = ", ".join("?" for _ in statuses)
    return conn.execute(
        f"SELECT * FROM candidates WHERE status IN ({marks}) ORDER BY provenance DESC, discovered_at ASC LIMIT 1",
        statuses,
    ).fetchone()


def list_status(conn: sqlite3.Connection, status: str, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM candidates WHERE status = ? ORDER BY discovered_at DESC LIMIT ?", (status, limit)
    ).fetchall()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM candidates GROUP BY status").fetchall()
    result = {s: 0 for s in ALL_STATUSES}
    result.update({r["status"]: r["n"] for r in rows})
    return result


def reset_skipped(conn: sqlite3.Connection) -> int:
    cur = conn.execute("UPDATE candidates SET status = 'queued', decided_at = NULL WHERE status = 'skipped'")
    conn.commit()
    return cur.rowcount


def known_organisations(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT organisation, url FROM candidates WHERE status IN ('queued', 'approved') AND organisation IS NOT NULL"
    ).fetchall()
    return [(r["organisation"], r["url"]) for r in rows]
