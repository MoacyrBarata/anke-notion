"""
db.py — SQLite local store for sync history and run telemetry.

Cross-platform (Windows / Linux / macOS) — uses stdlib `sqlite3` only.
Single-process / single-writer; no WAL needed for the expected workload.

Schema lives in `sync_history.db` next to the project root and is created /
migrated automatically on first call. Schema version is tracked so future
changes can ship migrations without breaking existing installs.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Iterator

ROOT    = Path(__file__).parent
DB_FILE = ROOT / "sync_history.db"

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    mode             TEXT,
    provider         TEXT,
    model            TEXT,
    items_processed  INTEGER NOT NULL DEFAULT 0,
    cards_generated  INTEGER NOT NULL DEFAULT 0,
    cards_inserted   INTEGER NOT NULL DEFAULT 0,
    errors           INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'running',
    log_excerpt      TEXT
);

CREATE TABLE IF NOT EXISTS sync_history (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id              TEXT NOT NULL,
    db_id                TEXT,
    db_name              TEXT,
    title                TEXT,
    category             TEXT,
    synced_at            TEXT NOT NULL,
    page_last_edited_at  TEXT,
    cards_generated      INTEGER NOT NULL DEFAULT 0,
    cards_inserted       INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    error_msg            TEXT,
    retry_count          INTEGER NOT NULL DEFAULT 0,
    run_id               INTEGER,
    FOREIGN KEY (run_id) REFERENCES sync_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sync_history_page    ON sync_history(page_id);
CREATE INDEX IF NOT EXISTS idx_sync_history_status  ON sync_history(status);
CREATE INDEX IF NOT EXISTS idx_sync_history_synced  ON sync_history(synced_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_history_db      ON sync_history(db_id);
"""


# ── Path / connection ─────────────────────────────────────────────────────────

def _db_path() -> Path:
    """Indirection to allow tests to monkeypatch DB_FILE."""
    return DB_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    """Returns a sqlite3 connection with FK enforcement and dict-like rows."""
    conn = sqlite3.connect(str(_db_path()), isolation_level=None,
                           detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    c = get_conn()
    try:
        yield c
    finally:
        c.close()


# ── Init / migration ──────────────────────────────────────────────────────────

def init_db() -> None:
    """Creates schema if absent and applies pending migrations.
    Idempotent — safe to call on every app startup."""
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(_SCHEMA)
        cur = c.execute("SELECT version FROM schema_version LIMIT 1;")
        row = cur.fetchone()
        if row is None:
            c.execute("INSERT INTO schema_version(version) VALUES (?);",
                      (SCHEMA_VERSION,))
            return
        # Future: if row['version'] < SCHEMA_VERSION → migrate.


# ── Run lifecycle ─────────────────────────────────────────────────────────────

def start_run(mode: str | None = None,
              provider: str | None = None,
              model: str | None = None) -> int:
    """Inserts a new run row; returns its id. Use finish_run() to close it."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sync_runs(started_at, mode, provider, model, status) "
            "VALUES (?, ?, ?, ?, 'running');",
            (_now_iso(), mode, provider, model),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int,
               status: str,
               items_processed: int = 0,
               cards_generated: int = 0,
               cards_inserted: int = 0,
               errors: int = 0,
               log_excerpt: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE sync_runs SET finished_at = ?, status = ?, "
            "items_processed = ?, cards_generated = ?, cards_inserted = ?, "
            "errors = ?, log_excerpt = ? WHERE id = ?;",
            (_now_iso(), status, items_processed, cards_generated,
             cards_inserted, errors, log_excerpt, run_id),
        )


# ── Per-item history ──────────────────────────────────────────────────────────

def record_sync(page_id: str,
                db_id: str | None,
                db_name: str | None,
                title: str | None,
                category: str | None,
                page_last_edited_at: str | None,
                cards_generated: int,
                cards_inserted: int,
                status: str,
                error_msg: str | None = None,
                run_id: int | None = None) -> int:
    """Inserts a sync attempt for a page. Returns the new row id.
    `status`: 'success' | 'error' | 'skipped'."""
    if status not in ("success", "error", "skipped"):
        raise ValueError(f"invalid status {status!r}")
    retry = _count_prior_errors(page_id) if status in ("error", "success") else 0
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sync_history(page_id, db_id, db_name, title, category, "
            "synced_at, page_last_edited_at, cards_generated, cards_inserted, "
            "status, error_msg, retry_count, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?);",
            (page_id, db_id, db_name, title, category,
             _now_iso(), page_last_edited_at,
             cards_generated, cards_inserted,
             status, error_msg, retry, run_id),
        )
        return int(cur.lastrowid)


def _count_prior_errors(page_id: str) -> int:
    """How many times this page failed since its last success (or ever)."""
    with _conn() as c:
        last_success = c.execute(
            "SELECT MAX(synced_at) AS t FROM sync_history "
            "WHERE page_id = ? AND status = 'success';",
            (page_id,),
        ).fetchone()
        cutoff = last_success["t"] if last_success else None
        if cutoff:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM sync_history "
                "WHERE page_id = ? AND status = 'error' AND synced_at > ?;",
                (page_id, cutoff),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM sync_history "
                "WHERE page_id = ? AND status = 'error';",
                (page_id,),
            ).fetchone()
        return int(row["n"])


def is_synced(page_id: str, page_last_edited_at: str | None) -> bool:
    """True if the page has a successful sync AND was not edited since.

    Falls back to False (i.e. needs sync) on any parse error so we never
    silently skip a page the user expects to see processed.
    """
    last = get_last_success(page_id)
    if not last:
        return False
    if not page_last_edited_at:
        return True
    try:
        edited  = _parse_iso(page_last_edited_at)
        synced  = _parse_iso(last["synced_at"])
    except Exception:
        return False
    return edited <= synced


def _parse_iso(s: str) -> datetime:
    """ISO8601 → tz-aware datetime. Notion sends `Z` suffix; normalise it."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_last_success(page_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM sync_history WHERE page_id = ? AND status = 'success' "
            "ORDER BY synced_at DESC LIMIT 1;",
            (page_id,),
        ).fetchone()
        return dict(row) if row else None


def list_history(limit: int = 50,
                 status: str | None = None,
                 db_id: str | None = None) -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if db_id:
        where.append("db_id = ?")
        params.append(db_id)
    sql = "SELECT * FROM sync_history"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY synced_at DESC LIMIT ?;"
    params.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def list_runs(limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?;",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_pending(page_id: str) -> int:
    """Removes all history rows for a page so the next run re-processes it.
    Returns number of deleted rows."""
    with _conn() as c:
        cur = c.execute("DELETE FROM sync_history WHERE page_id = ?;", (page_id,))
        return cur.rowcount


def mark_all_pending(db_id: str | None = None) -> int:
    """Wipes history. Optionally scoped to one data_source."""
    with _conn() as c:
        if db_id:
            cur = c.execute("DELETE FROM sync_history WHERE db_id = ?;", (db_id,))
        else:
            cur = c.execute("DELETE FROM sync_history;")
        return cur.rowcount


def get_stats(days: int = 30) -> dict:
    """Aggregate stats over the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as c:
        agg = c.execute(
            "SELECT COUNT(*) AS attempts, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successes, "
            "SUM(CASE WHEN status='error'   THEN 1 ELSE 0 END) AS errors, "
            "SUM(cards_inserted) AS cards "
            "FROM sync_history WHERE synced_at >= ?;",
            (cutoff,),
        ).fetchone()
        runs = c.execute(
            "SELECT COUNT(*) AS n FROM sync_runs WHERE started_at >= ?;",
            (cutoff,),
        ).fetchone()
        return {
            "days":      days,
            "attempts":  int(agg["attempts"] or 0),
            "successes": int(agg["successes"] or 0),
            "errors":    int(agg["errors"] or 0),
            "cards":     int(agg["cards"] or 0),
            "runs":      int(runs["n"] or 0),
        }


def filter_pending(pages: Iterable[dict]) -> list[dict]:
    """Helper: given Notion page dicts, returns only the ones not yet synced
    OR edited since last success. Pages without `id` are kept as-is."""
    out = []
    for p in pages:
        pid    = p.get("id")
        edited = p.get("last_edited_time")
        if not pid or not is_synced(pid, edited):
            out.append(p)
    return out
