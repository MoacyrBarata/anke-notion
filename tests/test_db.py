"""Tests for db.py — local SQLite sync history."""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import db


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Each test gets a virgin DB file in tmp_path."""
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "sync_history.db")
    db.init_db()
    return tmp_path / "sync_history.db"


# ── Schema / init ─────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_file(self, fresh_db):
        assert fresh_db.exists()

    def test_idempotent(self, fresh_db):
        db.init_db()
        db.init_db()
        # Stores exactly one schema_version row
        with db.get_conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM schema_version;").fetchone()["n"]
        assert n == 1

    def test_schema_version_set(self, fresh_db):
        with db.get_conn() as c:
            v = c.execute("SELECT version FROM schema_version;").fetchone()["version"]
        assert v == db.SCHEMA_VERSION


# ── Run lifecycle ─────────────────────────────────────────────────────────────

class TestRuns:
    def test_start_returns_id(self, fresh_db):
        rid = db.start_run(mode="flat", provider="gemini", model="gemini-2.5-flash")
        assert isinstance(rid, int) and rid > 0

    def test_finish_run_updates_status_and_totals(self, fresh_db):
        rid = db.start_run(mode="flat", provider="gemini", model="x")
        db.finish_run(rid, "success", items_processed=3,
                      cards_generated=10, cards_inserted=9, errors=0)
        runs = db.list_runs()
        assert runs[0]["id"] == rid
        assert runs[0]["status"] == "success"
        assert runs[0]["cards_inserted"] == 9
        assert runs[0]["finished_at"] is not None

    def test_list_runs_sorted_desc(self, fresh_db):
        ids = [db.start_run() for _ in range(3)]
        rows = db.list_runs(limit=10)
        assert [r["id"] for r in rows] == list(reversed(ids))


# ── record_sync / history ────────────────────────────────────────────────────

class TestRecordSync:
    def _rec(self, **kw):
        defaults = dict(
            page_id="page-1", db_id="ds-1", db_name="DS",
            title="Title", category="Mat",
            page_last_edited_at="2026-01-01T10:00:00+00:00",
            cards_generated=5, cards_inserted=5,
            status="success",
        )
        defaults.update(kw)
        return db.record_sync(**defaults)

    def test_inserts_row(self, fresh_db):
        rid = self._rec()
        assert rid > 0
        rows = db.list_history()
        assert len(rows) == 1
        assert rows[0]["page_id"] == "page-1"
        assert rows[0]["status"]  == "success"

    def test_invalid_status_rejected(self, fresh_db):
        with pytest.raises(ValueError):
            self._rec(status="weird")

    def test_retry_count_increments_on_consecutive_errors(self, fresh_db):
        self._rec(status="error", error_msg="x")
        self._rec(status="error", error_msg="x")
        last_err = db.list_history(status="error")[0]
        assert last_err["retry_count"] == 1  # one previous error before this one

    def test_retry_count_resets_after_success(self, fresh_db):
        self._rec(status="error")
        self._rec(status="error")
        self._rec(status="success")
        # Now a fresh failure should have retry_count = 0 (no errors after success)
        self._rec(status="error")
        last_err = db.list_history(status="error")[0]
        assert last_err["retry_count"] == 0


# ── is_synced / get_last_success ─────────────────────────────────────────────

class TestIsSynced:
    def test_unknown_page_is_not_synced(self, fresh_db):
        assert db.is_synced("nope", "2026-01-01T00:00:00Z") is False

    def test_synced_when_no_edits_since(self, fresh_db):
        db.record_sync(page_id="p", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00+00:00",
                       cards_generated=1, cards_inserted=1, status="success")
        # Page edited *before* sync (i.e. unchanged) → considered synced
        assert db.is_synced("p", "2026-01-01T09:00:00+00:00") is True

    def test_not_synced_when_edited_after_sync(self, fresh_db):
        db.record_sync(page_id="p", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00+00:00",
                       cards_generated=1, cards_inserted=1, status="success")
        time.sleep(0.01)  # ensure later synced_at strictly precedes the edit
        # Page edited well after → not synced anymore
        assert db.is_synced("p", "2030-01-01T10:00:00+00:00") is False

    def test_handles_z_suffix(self, fresh_db):
        db.record_sync(page_id="p", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00Z",
                       cards_generated=1, cards_inserted=1, status="success")
        assert db.is_synced("p", "2026-01-01T09:00:00Z") is True

    def test_only_success_counts(self, fresh_db):
        # Errors don't make a page "synced"
        db.record_sync(page_id="p", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00Z",
                       cards_generated=0, cards_inserted=0,
                       status="error", error_msg="bad")
        assert db.is_synced("p", "2026-01-01T09:00:00Z") is False


# ── list_history filters ─────────────────────────────────────────────────────

class TestListHistory:
    def _seed(self):
        db.record_sync(page_id="a", db_id="d1", db_name="DS1", title="A",
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        db.record_sync(page_id="b", db_id="d2", db_name="DS2", title="B",
                       category=None, page_last_edited_at=None,
                       cards_generated=0, cards_inserted=0, status="error",
                       error_msg="boom")

    def test_no_filter_returns_all(self, fresh_db):
        self._seed()
        assert len(db.list_history()) == 2

    def test_filter_by_status(self, fresh_db):
        self._seed()
        rows = db.list_history(status="error")
        assert len(rows) == 1
        assert rows[0]["page_id"] == "b"

    def test_filter_by_db(self, fresh_db):
        self._seed()
        rows = db.list_history(db_id="d1")
        assert len(rows) == 1
        assert rows[0]["page_id"] == "a"

    def test_limit(self, fresh_db):
        for i in range(5):
            db.record_sync(page_id=f"p{i}", db_id="d", db_name="x",
                           title=str(i), category=None,
                           page_last_edited_at=None,
                           cards_generated=0, cards_inserted=0, status="success")
        assert len(db.list_history(limit=3)) == 3


# ── mark_pending / mark_all_pending ──────────────────────────────────────────

class TestMarkPending:
    def test_mark_pending_deletes_only_target(self, fresh_db):
        db.record_sync(page_id="x", db_id="d", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        db.record_sync(page_id="y", db_id="d", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        assert db.mark_pending("x") == 1
        rows = db.list_history()
        assert {r["page_id"] for r in rows} == {"y"}

    def test_mark_all_pending_scoped_to_db(self, fresh_db):
        db.record_sync(page_id="x", db_id="d1", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        db.record_sync(page_id="y", db_id="d2", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        assert db.mark_all_pending(db_id="d1") == 1
        rows = db.list_history()
        assert {r["page_id"] for r in rows} == {"y"}

    def test_mark_all_pending_global(self, fresh_db):
        db.record_sync(page_id="x", db_id="d", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=1, cards_inserted=1, status="success")
        assert db.mark_all_pending() == 1
        assert db.list_history() == []


# ── get_stats ────────────────────────────────────────────────────────────────

class TestGetStats:
    def test_empty_db(self, fresh_db):
        s = db.get_stats(days=30)
        assert s == {"days": 30, "attempts": 0, "successes": 0,
                     "errors": 0, "cards": 0, "runs": 0}

    def test_aggregates_within_window(self, fresh_db):
        db.start_run()
        db.record_sync(page_id="a", db_id="d", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=3, cards_inserted=3, status="success")
        db.record_sync(page_id="b", db_id="d", db_name=None, title=None,
                       category=None, page_last_edited_at=None,
                       cards_generated=0, cards_inserted=0, status="error")
        s = db.get_stats(days=30)
        assert s["attempts"]  == 2
        assert s["successes"] == 1
        assert s["errors"]    == 1
        assert s["cards"]     == 3
        assert s["runs"]      == 1


# ── filter_pending helper ────────────────────────────────────────────────────

class TestFilterPending:
    def test_keeps_unknown_pages(self, fresh_db):
        pages = [{"id": "a", "last_edited_time": "2026-01-01T00:00:00Z"}]
        assert len(db.filter_pending(pages)) == 1

    def test_drops_synced_unedited(self, fresh_db):
        db.record_sync(page_id="a", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00Z",
                       cards_generated=1, cards_inserted=1, status="success")
        pages = [{"id": "a", "last_edited_time": "2026-01-01T09:00:00Z"}]
        assert db.filter_pending(pages) == []

    def test_keeps_synced_then_edited(self, fresh_db):
        db.record_sync(page_id="a", db_id="d", db_name=None, title=None,
                       category=None,
                       page_last_edited_at="2026-01-01T10:00:00Z",
                       cards_generated=1, cards_inserted=1, status="success")
        pages = [{"id": "a", "last_edited_time": "2030-01-01T10:00:00Z"}]
        assert len(db.filter_pending(pages)) == 1

    def test_keeps_pages_without_id(self, fresh_db):
        pages = [{"foo": "bar"}]
        assert len(db.filter_pending(pages)) == 1


# ── Page block cache ─────────────────────────────────────────────────────────

class TestPageBlockCache:
    def test_miss_returns_none(self, fresh_db):
        assert db.get_cached_page_content("p1", "2026-01-01T00:00:00Z") is None

    def test_roundtrip(self, fresh_db):
        db.set_cached_page_content("p1", "2026-01-01T00:00:00Z", "hello world")
        assert (db.get_cached_page_content("p1", "2026-01-01T00:00:00Z")
                == "hello world")

    def test_stale_when_last_edited_changes(self, fresh_db):
        db.set_cached_page_content("p1", "2026-01-01T00:00:00Z", "old")
        # Page edited → timestamp differs → cache is invalidated.
        assert db.get_cached_page_content("p1", "2026-05-01T00:00:00Z") is None

    def test_upsert_overwrites(self, fresh_db):
        db.set_cached_page_content("p1", "2026-01-01T00:00:00Z", "v1")
        db.set_cached_page_content("p1", "2026-02-02T00:00:00Z", "v2")
        assert db.get_cached_page_content("p1", "2026-02-02T00:00:00Z") == "v2"
        # Only one row per page_id (primary key).
        with db.get_conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM page_block_cache").fetchone()["n"]
        assert n == 1

    def test_missing_page_or_timestamp_returns_none(self, fresh_db):
        assert db.get_cached_page_content("", "2026-01-01T00:00:00Z") is None
        assert db.get_cached_page_content("p1", None) is None

    def test_set_with_missing_args_is_noop(self, fresh_db):
        db.set_cached_page_content("", "2026-01-01T00:00:00Z", "x")
        db.set_cached_page_content("p1", "", "x")
        with db.get_conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM page_block_cache").fetchone()["n"]
        assert n == 0

    def test_clear_specific_page(self, fresh_db):
        db.set_cached_page_content("p1", "t1", "a")
        db.set_cached_page_content("p2", "t2", "b")
        removed = db.clear_page_cache("p1")
        assert removed == 1
        assert db.get_cached_page_content("p1", "t1") is None
        assert db.get_cached_page_content("p2", "t2") == "b"

    def test_clear_all(self, fresh_db):
        db.set_cached_page_content("p1", "t1", "a")
        db.set_cached_page_content("p2", "t2", "b")
        removed = db.clear_page_cache()
        assert removed == 2
        with db.get_conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM page_block_cache").fetchone()["n"]
        assert n == 0
