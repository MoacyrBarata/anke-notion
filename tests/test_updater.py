"""Tests for updater.py — self-update mechanism."""
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import updater


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    """Redirect updater paths to a tmp dir so tests can't touch the real repo."""
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "VERSION_FILE",  tmp_path / "VERSION")
    monkeypatch.setattr(updater, "BACKUP_DIR",    tmp_path / ".update_backup")
    monkeypatch.setattr(updater, "STAGING_DIR",   tmp_path / ".update_staging")
    monkeypatch.setattr(updater, "LOCK_FILE",     tmp_path / ".update.lock")
    monkeypatch.setattr(updater, "MANIFEST_FILE", tmp_path / ".update_manifest.json")
    return tmp_path


# ── Version parsing ──────────────────────────────────────────────────────────

class TestParseSemver:
    def test_basic(self):
        assert updater._parse_semver("0.2.0") == (0, 2, 0, ())

    def test_strips_v_prefix(self):
        assert updater._parse_semver("v1.2.3") == (1, 2, 3, ())

    def test_keeps_pre_release(self):
        out = updater._parse_semver("1.2.3-beta1")
        assert out is not None
        assert out[:3] == (1, 2, 3)
        assert out[3] != ()  # has prerelease ids

    def test_strips_build_metadata(self):
        out = updater._parse_semver("1.2.3+build.5")
        assert out == (1, 2, 3, ())

    def test_short_returns_none(self):
        assert updater._parse_semver("1.2") is None

    def test_garbage_returns_none(self):
        assert updater._parse_semver("not-a-version") is None


class TestIsNewer:
    def test_greater_major(self):
        assert updater._is_newer("1.0.0", "0.9.9")

    def test_greater_patch(self):
        assert updater._is_newer("0.2.1", "0.2.0")

    def test_equal_returns_false(self):
        assert updater._is_newer("0.2.0", "0.2.0") is False

    def test_older_returns_false(self):
        assert updater._is_newer("0.1.0", "0.2.0") is False

    def test_release_greater_than_prerelease_same_core(self):
        assert updater._is_newer("1.0.0", "1.0.0-beta1") is True
        assert updater._is_newer("1.0.0-beta1", "1.0.0") is False

    def test_prerelease_ordering(self):
        assert updater._is_newer("1.0.0-beta2", "1.0.0-beta1") is True
        assert updater._is_newer("1.0.0-beta1", "1.0.0-beta2") is False
        assert updater._is_newer("1.0.0-rc1", "1.0.0-beta1") is True

    def test_unparseable_uses_string_diff(self):
        assert updater._is_newer("abc1234", "def5678") is True
        assert updater._is_newer("abc1234", "abc1234") is False


# ── Current version ──────────────────────────────────────────────────────────

class TestGetCurrentVersion:
    def test_reads_version_file(self, tmp_root):
        (tmp_root / "VERSION").write_text("0.5.1\n", encoding="utf-8")
        assert updater.get_current_version() == "0.5.1"

    def test_falls_back_when_missing(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git", lambda *a, **k: "")
        assert updater.get_current_version() == "unknown"

    def test_uses_git_sha_when_no_file(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git", lambda *a, **k: "abc1234")
        assert updater.get_current_version() == "abc1234"


# ── HTTP error taxonomy ─────────────────────────────────────────────────────

class TestCheckForUpdate:
    def test_has_update_when_remote_newer(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.1.0", encoding="utf-8")
        monkeypatch.setattr(updater, "_http_json", lambda url: {
            "ok": True,
            "data": {"tag_name": "0.2.0", "html_url": "u", "body": ""},
        })
        out = updater.check_for_update()
        assert out["has_update"] is True
        assert out["current"] == "0.1.0"
        assert out["latest"]  == "0.2.0"
        assert out["error"]   is None
        assert out["error_kind"] is None

    def test_no_update_when_equal(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(updater, "_http_json", lambda url: {
            "ok": True,
            "data": {"tag_name": "0.2.0", "html_url": "u", "body": ""},
        })
        assert updater.check_for_update()["has_update"] is False

    def test_error_when_remote_unreachable(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(updater, "_http_json", lambda url: {
            "ok": False, "kind": "network", "msg": "down",
        })
        out = updater.check_for_update()
        assert out["has_update"] is False
        assert out["error"]
        assert out["error_kind"] == "network"

    def test_error_kind_ratelimit(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(updater, "_http_json", lambda url: {
            "ok": False, "kind": "ratelimit", "msg": "HTTP 403",
        })
        out = updater.check_for_update()
        assert out["error_kind"] == "ratelimit"
        assert "GITHUB_TOKEN" in out["error"]


# ── Backup / restore (zip strategy) ──────────────────────────────────────────

class TestBackupRestore:
    def _seed(self, tmp_root):
        (tmp_root / "code.py").write_text("v1", encoding="utf-8")
        (tmp_root / "lib").mkdir()
        (tmp_root / "lib" / "x.py").write_text("lib1", encoding="utf-8")
        (tmp_root / ".env").write_text("SECRET=keep_me", encoding="utf-8")
        (tmp_root / "notion_config.json").write_text("{\"x\":1}",
                                                     encoding="utf-8")
        (tmp_root / "sync_history.db").write_bytes(b"db-bytes")

    def test_backup_skips_user_data(self, tmp_root):
        self._seed(tmp_root)
        dest = tmp_root / ".update_backup" / "x"
        updater._backup_tree(dest)
        assert (dest / "code.py").exists()
        assert (dest / "lib" / "x.py").exists()
        assert not (dest / ".env").exists()
        assert not (dest / "notion_config.json").exists()
        assert not (dest / "sync_history.db").exists()

    def test_restore_recovers_code(self, tmp_root):
        self._seed(tmp_root)
        dest = tmp_root / ".update_backup" / "x"
        updater._backup_tree(dest)
        (tmp_root / "code.py").write_text("BROKEN", encoding="utf-8")
        (tmp_root / "lib" / "x.py").unlink()
        updater._restore_tree(dest)
        assert (tmp_root / "code.py").read_text(encoding="utf-8") == "v1"
        assert (tmp_root / "lib" / "x.py").read_text(encoding="utf-8") == "lib1"

    def test_restore_preserves_user_data(self, tmp_root):
        self._seed(tmp_root)
        dest = tmp_root / ".update_backup" / "x"
        updater._backup_tree(dest)
        (tmp_root / ".env").write_text("SECRET=newer", encoding="utf-8")
        updater._restore_tree(dest)
        assert (tmp_root / ".env").read_text(encoding="utf-8") == "SECRET=newer"


# ── Zip validation ─────────────────────────────────────────────────────────

class TestValidateZip:
    def _make_zip(self, top: str, files: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{top}/", b"")
            for path, data in files.items():
                zf.writestr(f"{top}/{path}", data)
        return buf.getvalue()

    def test_valid_zip_accepted(self):
        raw = self._make_zip("anke-notion-main", {"a.py": b"x"})
        zf, top, err = updater._validate_zip(raw)
        assert zf is not None
        assert top == "anke-notion-main"
        assert err == ""
        zf.close()

    def test_empty_zip_rejected(self):
        zf, top, err = updater._validate_zip(b"")
        assert zf is None
        assert "vazio" in err

    def test_bad_zip_rejected(self):
        zf, top, err = updater._validate_zip(b"not-a-zip")
        assert zf is None

    def test_wrong_prefix_rejected(self):
        raw = self._make_zip("malicious-repo-main", {"a.py": b"x"})
        zf, top, err = updater._validate_zip(raw)
        assert zf is None
        assert "prefixo" in err.lower() or "inesperado" in err.lower()

    def test_path_traversal_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("anke-notion-main/", b"")
            zf.writestr("anke-notion-main/../evil.py", b"x")
        zf, top, err = updater._validate_zip(buf.getvalue())
        assert zf is None


# ── Zip apply pipeline ───────────────────────────────────────────────────────

class TestApplyZip:
    def _make_zip(self, top: str, files: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{top}/", b"")
            for path, data in files.items():
                zf.writestr(f"{top}/{path}", data)
        return buf.getvalue()

    def test_swaps_files_keeps_user_data(self, tmp_root, monkeypatch):
        (tmp_root / "code.py").write_text("OLD", encoding="utf-8")
        (tmp_root / ".env").write_text("SECRET=keep", encoding="utf-8")
        (tmp_root / "notion_config.json").write_text("{}", encoding="utf-8")

        raw = self._make_zip("anke-notion-main", {
            "code.py":             b"NEW",
            "newfile.py":          b"hello",
            ".env":                b"SHOULD_NOT_OVERWRITE",
            "notion_config.json":  b"{}",
        })
        monkeypatch.setattr(updater, "_download_zip", lambda url: raw)

        ok, msg = updater._apply_update_zip()
        assert ok, msg
        assert (tmp_root / "code.py").read_text(encoding="utf-8") == "NEW"
        assert (tmp_root / "newfile.py").read_text(encoding="utf-8") == "hello"
        assert (tmp_root / ".env").read_text(encoding="utf-8") == "SECRET=keep"

    def test_deletes_files_removed_upstream(self, tmp_root, monkeypatch):
        (tmp_root / "lib").mkdir()
        (tmp_root / "lib" / "old.py").write_text("OLD", encoding="utf-8")
        (tmp_root / "lib" / "kept.py").write_text("KEPT", encoding="utf-8")
        (tmp_root / "code.py").write_text("OLD", encoding="utf-8")
        # Prior manifest must list the files we shipped before — only those
        # are eligible for deletion.
        updater._write_manifest({"code.py", "lib/old.py", "lib/kept.py"})

        raw = self._make_zip("anke-notion-main", {
            "code.py":      b"NEW",
            "lib/kept.py":  b"NEW",
            # lib/old.py absent → should be deleted
        })
        monkeypatch.setattr(updater, "_download_zip", lambda url: raw)
        ok, msg = updater._apply_update_zip()
        assert ok, msg
        assert not (tmp_root / "lib" / "old.py").exists()
        assert (tmp_root / "lib" / "kept.py").read_text(encoding="utf-8") == "NEW"

    def test_preserves_user_added_files(self, tmp_root, monkeypatch):
        """User adds files locally that are NOT in the manifest → never
        deleted, even if absent from the new release."""
        (tmp_root / "code.py").write_text("OLD", encoding="utf-8")
        (tmp_root / "my_notes.md").write_text("personal", encoding="utf-8")
        (tmp_root / "scripts").mkdir()
        (tmp_root / "scripts" / "user.sh").write_text("#!/bin/sh", encoding="utf-8")
        # Manifest only lists what WE shipped (code.py).
        updater._write_manifest({"code.py"})

        raw = self._make_zip("anke-notion-main", {"code.py": b"NEW"})
        monkeypatch.setattr(updater, "_download_zip", lambda url: raw)
        ok, msg = updater._apply_update_zip()
        assert ok, msg
        # User-added files survive.
        assert (tmp_root / "my_notes.md").exists()
        assert (tmp_root / "scripts" / "user.sh").exists()

    def test_first_run_skips_deletion(self, tmp_root, monkeypatch):
        """No manifest yet → updater must not delete anything (conservative)."""
        (tmp_root / "code.py").write_text("OLD", encoding="utf-8")
        (tmp_root / "stale.py").write_text("STALE", encoding="utf-8")
        # No manifest written.
        raw = self._make_zip("anke-notion-main", {"code.py": b"NEW"})
        monkeypatch.setattr(updater, "_download_zip", lambda url: raw)
        ok, msg = updater._apply_update_zip()
        assert ok
        assert (tmp_root / "stale.py").exists()  # untouched

    def test_writes_manifest_after_success(self, tmp_root, monkeypatch):
        raw = self._make_zip("anke-notion-main", {
            "code.py":     b"NEW",
            "lib/x.py":    b"NEW",
        })
        monkeypatch.setattr(updater, "_download_zip", lambda url: raw)
        ok, _ = updater._apply_update_zip()
        assert ok
        m = updater._read_manifest()
        assert "code.py" in m
        assert "lib/x.py" in m

    def test_returns_failure_when_download_fails(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_download_zip", lambda url: None)
        ok, msg = updater._apply_update_zip()
        assert ok is False
        assert "baixar" in msg.lower()

    def test_invalid_zip_restores_backup(self, tmp_root, monkeypatch):
        (tmp_root / "code.py").write_text("OLD", encoding="utf-8")
        monkeypatch.setattr(updater, "_download_zip", lambda url: b"garbage")
        ok, msg = updater._apply_update_zip()
        assert ok is False
        assert (tmp_root / "code.py").read_text(encoding="utf-8") == "OLD"


# ── apply_update strategy dispatch ───────────────────────────────────────────

class TestApplyUpdate:
    def test_explicit_zip_calls_zip(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (False, "should not be called"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda h: "")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        ok, msg = updater.apply_update("zip")
        assert ok and "zip" in msg

    def test_explicit_git_calls_git(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (True, "git ok"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (False, "should not be called"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda h: "")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        ok, msg = updater.apply_update("git")
        assert ok and "git" in msg

    def test_auto_falls_back_to_zip_when_git_fails(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: True)
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (False, "uncommitted"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda h: "")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        ok, msg = updater.apply_update("auto")
        assert ok
        assert "zip" in msg

    def test_auto_uses_zip_when_no_git(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: False)
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda h: "")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        ok, msg = updater.apply_update("auto")
        assert ok

    def test_post_update_runs_migration(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations",
                            lambda h: "DB OK")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        ok, msg = updater.apply_update("zip")
        assert ok
        assert "DB OK" in msg

    def test_lock_blocks_concurrent(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda h: "")
        monkeypatch.setattr(updater, "prune_backups", lambda keep=5: 0)
        # Pre-create lock file as if another process held it.
        updater.LOCK_FILE.write_text("99999", encoding="utf-8")
        ok, msg = updater.apply_update("zip")
        assert ok is False
        assert "andamento" in msg.lower() or "lock" in msg.lower()


# ── Deps reinstall trigger ────────────────────────────────────────────────────

class TestDepsReinstall:
    def test_skips_when_unchanged(self, tmp_root, monkeypatch):
        (tmp_root / "requirements.txt").write_text("flet\n", encoding="utf-8")
        h = updater._hash_file(tmp_root / "requirements.txt")
        called = {"n": 0}
        monkeypatch.setattr(updater, "_reinstall_deps",
                            lambda: (called.__setitem__("n", called["n"] + 1) or (True, "")))
        # Stub init_db to avoid touching real DB
        import db
        monkeypatch.setattr(db, "init_db", lambda: None)
        out = updater._post_update_migrations(h)
        assert called["n"] == 0
        assert "DB OK" in out

    def test_runs_when_changed(self, tmp_root, monkeypatch):
        (tmp_root / "requirements.txt").write_text("flet\n", encoding="utf-8")
        old_hash = "abc123"  # mismatch
        called = {"n": 0}
        def fake_install():
            called["n"] += 1
            return True, "pip ok"
        monkeypatch.setattr(updater, "_reinstall_deps", fake_install)
        import db
        monkeypatch.setattr(db, "init_db", lambda: None)
        out = updater._post_update_migrations(old_hash)
        assert called["n"] == 1
        assert "deps" in out


# ── Rollback ─────────────────────────────────────────────────────────────────

class TestRollback:
    def test_missing_backup_returns_error(self, tmp_root):
        ok, msg = updater.rollback(tmp_root / "nope")
        assert not ok
        assert "não encontrado" in msg.lower()

    def test_rollback_restores(self, tmp_root):
        (tmp_root / "code.py").write_text("v1", encoding="utf-8")
        backup = tmp_root / ".update_backup" / "snap"
        updater._backup_tree(backup)
        (tmp_root / "code.py").write_text("BROKEN", encoding="utf-8")
        ok, _ = updater.rollback(backup)
        assert ok
        assert (tmp_root / "code.py").read_text(encoding="utf-8") == "v1"


# ── List + prune backups ─────────────────────────────────────────────────────

class TestListBackups:
    def test_empty(self, tmp_root):
        assert updater.list_backups() == []

    def test_lists_dirs_only_descending(self, tmp_root):
        b = tmp_root / ".update_backup"
        b.mkdir()
        (b / "20260101T000000Z").mkdir()
        (b / "20260201T000000Z").mkdir()
        (b / "rogue-file.txt").write_text("ignore me")
        out = [p.name for p in updater.list_backups()]
        assert out == ["20260201T000000Z", "20260101T000000Z"]


class TestPruneBackups:
    def test_keeps_newest_n(self, tmp_root):
        b = tmp_root / ".update_backup"
        b.mkdir()
        for ts in ("20260101T000000Z", "20260201T000000Z",
                   "20260301T000000Z", "20260401T000000Z",
                   "20260501T000000Z", "20260601T000000Z"):
            (b / ts).mkdir()
        removed = updater.prune_backups(keep=3)
        assert removed == 3
        names = [p.name for p in updater.list_backups()]
        assert names == ["20260601T000000Z", "20260501T000000Z",
                         "20260401T000000Z"]

    def test_zero_keep_clears_all(self, tmp_root):
        b = tmp_root / ".update_backup"
        b.mkdir()
        (b / "x").mkdir()
        (b / "y").mkdir()
        assert updater.prune_backups(keep=0) == 2
        assert updater.list_backups() == []

    def test_fewer_than_keep_no_op(self, tmp_root):
        b = tmp_root / ".update_backup"
        b.mkdir()
        (b / "only").mkdir()
        assert updater.prune_backups(keep=5) == 0
        assert len(updater.list_backups()) == 1


# ── HTTP error taxonomy ──────────────────────────────────────────────────────

class TestHttpJson:
    def _mock_urlopen(self, payload):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__  = MagicMock(return_value=False)
        cm.read = MagicMock(return_value=payload)
        return MagicMock(return_value=cm)

    def test_ok_returns_data(self, monkeypatch):
        opener = self._mock_urlopen(b'{"x": 1}')
        monkeypatch.setattr(updater, "urlopen", opener)
        out = updater._http_json("https://api.test/x")
        assert out["ok"] is True
        assert out["data"] == {"x": 1}

    def test_403_is_ratelimit(self, monkeypatch):
        def boom(*a, **k):
            raise HTTPError("u", 403, "Forbidden", {}, None)
        monkeypatch.setattr(updater, "urlopen", boom)
        out = updater._http_json("https://api.test/x")
        assert out["ok"] is False
        assert out["kind"] == "ratelimit"

    def test_429_is_ratelimit(self, monkeypatch):
        def boom(*a, **k):
            raise HTTPError("u", 429, "Too many", {}, None)
        monkeypatch.setattr(updater, "urlopen", boom)
        assert updater._http_json("https://api.test/x")["kind"] == "ratelimit"

    def test_500_is_http(self, monkeypatch):
        def boom(*a, **k):
            raise HTTPError("u", 500, "boom", {}, None)
        monkeypatch.setattr(updater, "urlopen", boom)
        assert updater._http_json("https://api.test/x")["kind"] == "http"

    def test_url_error_is_network(self, monkeypatch):
        def boom(*a, **k):
            raise URLError("dns down")
        monkeypatch.setattr(updater, "urlopen", boom)
        assert updater._http_json("https://api.test/x")["kind"] == "network"

    def test_bad_json_kind(self, monkeypatch):
        opener = self._mock_urlopen(b"<<<not json>>>")
        monkeypatch.setattr(updater, "urlopen", opener)
        assert updater._http_json("https://api.test/x")["kind"] == "json"

    def test_sends_github_token_when_set(self, monkeypatch):
        opener = self._mock_urlopen(b'{"x": 1}')
        captured = {}
        def fake(req, timeout=None):
            captured["headers"] = dict(req.headers)
            return opener.return_value
        monkeypatch.setattr(updater, "urlopen", fake)
        monkeypatch.setenv("GITHUB_TOKEN", "secret-tok")
        updater._http_json("https://api.test/x")
        assert any("Bearer secret-tok" == v
                   for v in captured["headers"].values())


# ── get_remote_version ───────────────────────────────────────────────────────

class TestGetRemoteVersion:
    def test_prefers_release(self, tmp_root, monkeypatch):
        responses = iter([
            {"ok": True, "data": {"tag_name": "v1.0.0", "html_url": "u",
                                  "body": "notes"}},
        ])
        monkeypatch.setattr(updater, "_http_json", lambda url: next(responses))
        info = updater.get_remote_version()
        assert info["version"] == "v1.0.0"
        assert info["source"]  == "release"
        assert info["notes"]   == "notes"

    def test_falls_back_to_commits(self, tmp_root, monkeypatch):
        responses = iter([
            {"ok": False, "kind": "http", "msg": "no release"},
            {"ok": True, "data": {
                "sha": "abcdef1234567890",
                "html_url": "u",
                "commit": {"message": "feat: x\n\nbody"},
            }},
        ])
        monkeypatch.setattr(updater, "_http_json", lambda url: next(responses))
        info = updater.get_remote_version()
        assert info["version"] == "abcdef1"
        assert info["source"]  == "commit"
        assert info["sha"]     == "abcdef1234567890"
        assert info["notes"]   == "feat: x"

    def test_returns_none_on_total_failure(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_http_json",
                            lambda url: {"ok": False, "kind": "network", "msg": ""})
        assert updater.get_remote_version() is None


# ── Hash + user data helpers ─────────────────────────────────────────────────

class TestHashFile:
    def test_missing_file_empty(self, tmp_root):
        assert updater._hash_file(tmp_root / "nope.txt") == ""

    def test_changes_with_content(self, tmp_root):
        f = tmp_root / "x.txt"
        f.write_text("a")
        h1 = updater._hash_file(f)
        f.write_text("b")
        h2 = updater._hash_file(f)
        assert h1 and h2 and h1 != h2

    def test_stable_for_same_content(self, tmp_root):
        f = tmp_root / "x.txt"
        f.write_bytes(b"hello world")
        assert updater._hash_file(f) == updater._hash_file(f)


class TestIsUserData:
    def test_top_level_match(self):
        assert updater._is_user_data(".env")
        assert updater._is_user_data("notion_config.json")
        assert updater._is_user_data("sync_history.db")

    def test_nested_under_user_dir(self):
        assert updater._is_user_data(".venv/lib/site-packages/x.py")

    def test_handles_windows_separators(self):
        assert updater._is_user_data(r".venv\lib\x.py")

    def test_non_user_path(self):
        assert updater._is_user_data("app.py") is False
        assert updater._is_user_data("lib/foo.py") is False


# ── Lock ─────────────────────────────────────────────────────────────────────

class TestLock:
    def test_acquire_creates_file(self, tmp_root):
        ok, msg = updater._acquire_lock()
        assert ok and updater.LOCK_FILE.exists()
        updater._release_lock()

    def test_acquire_blocks_when_held(self, tmp_root):
        updater.LOCK_FILE.write_text(str(os.getpid()))
        ok, msg = updater._acquire_lock()
        assert ok is False
        assert "andamento" in msg.lower()

    def test_stale_lock_auto_released(self, tmp_root, monkeypatch):
        updater.LOCK_FILE.write_text("99999")
        # Backdate mtime past stale threshold.
        old = time.time() - updater.LOCK_STALE_S - 60
        os.utime(updater.LOCK_FILE, (old, old))
        ok, msg = updater._acquire_lock()
        assert ok is True
        updater._release_lock()

    def test_release_idempotent(self, tmp_root):
        # Releasing without acquiring must not raise.
        updater._release_lock()
        updater._release_lock()


# ── apply_update lock release on exception ──────────────────────────────────

class TestApplyUpdateLocking:
    def test_lock_released_on_exception(self, tmp_root, monkeypatch):
        def boom():
            raise RuntimeError("kaboom")
        monkeypatch.setattr(updater, "_apply_update_zip", boom)
        with pytest.raises(RuntimeError):
            updater.apply_update("zip")
        # Lock file should be gone, threading.Lock unlocked.
        assert not updater.LOCK_FILE.exists()
        assert updater._lock.acquire(blocking=False)
        updater._lock.release()

    def test_failure_path_skips_prune(self, tmp_root, monkeypatch):
        called = {"prune": 0}
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (False, "oops"))
        monkeypatch.setattr(updater, "prune_backups",
                            lambda keep=5: called.__setitem__("prune",
                                                              called["prune"] + 1))
        ok, _ = updater.apply_update("zip")
        assert ok is False
        assert called["prune"] == 0


# ── Manifest helpers ─────────────────────────────────────────────────────────

class TestManifest:
    def test_empty_when_missing(self, tmp_root):
        assert updater._read_manifest() == set()

    def test_round_trip(self, tmp_root):
        updater._write_manifest({"a.py", "lib/b.py"})
        assert updater._read_manifest() == {"a.py", "lib/b.py"}

    def test_corrupt_returns_empty(self, tmp_root):
        updater.MANIFEST_FILE.write_text("<<<not json>>>", encoding="utf-8")
        assert updater._read_manifest() == set()

    def test_wrong_shape_returns_empty(self, tmp_root):
        updater.MANIFEST_FILE.write_text(json.dumps({"files": "nope"}),
                                         encoding="utf-8")
        assert updater._read_manifest() == set()

    def test_skips_user_data_in_deletion(self, tmp_root):
        # User data should never be deleted via the orphan pass even if
        # somehow listed in the prior manifest.
        (tmp_root / ".env").write_text("SECRET=keep", encoding="utf-8")
        (tmp_root / "removed.py").write_text("x", encoding="utf-8")
        updater._write_manifest({".env", "removed.py", "code.py"})
        deleted = updater._delete_orphans({"code.py"})  # neither path retained
        assert deleted == 1
        assert (tmp_root / ".env").exists()
        assert not (tmp_root / "removed.py").exists()


# ── Venv detection + reinstall ──────────────────────────────────────────────

class TestVenvPython:
    def test_no_venv_returns_none(self, tmp_root):
        assert updater._venv_python() is None

    def test_returns_venv_python_when_present(self, tmp_root, monkeypatch):
        venv = tmp_root / ".venv"
        if sys.platform == "win32":
            (venv / "Scripts").mkdir(parents=True)
            py = venv / "Scripts" / "python.exe"
        else:
            (venv / "bin").mkdir(parents=True)
            py = venv / "bin" / "python"
        py.write_text("")
        out = updater._venv_python()
        assert out == py


class TestReinstallDeps:
    def test_no_requirements_skipped(self, tmp_root):
        ok, msg = updater._reinstall_deps()
        assert ok is True

    def test_invokes_pip(self, tmp_root, monkeypatch):
        (tmp_root / "requirements.txt").write_text("flet\n", encoding="utf-8")
        captured = {}
        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""
        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _Result()
        monkeypatch.setattr(updater.subprocess, "run", fake_run)
        ok, msg = updater._reinstall_deps()
        assert ok
        assert "pip" in captured["cmd"]
        assert "install" in captured["cmd"]
        assert "-r" in captured["cmd"]

    def test_pip_failure_surfaced(self, tmp_root, monkeypatch):
        (tmp_root / "requirements.txt").write_text("flet\n", encoding="utf-8")
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "ERROR: could not install"
        monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _Result())
        ok, msg = updater._reinstall_deps()
        assert ok is False
        assert "ERROR" in msg or "could not install" in msg


# ── Restart helper ──────────────────────────────────────────────────────────

class TestRestartApp:
    def test_calls_popen_with_launcher_on_win(self, tmp_root, monkeypatch):
        (tmp_root / "launcher.py").write_text("")
        captured = {}
        def fake_popen(args, **kw):
            captured["args"] = args
            return MagicMock()
        monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(updater.sys, "platform", "win32")
        monkeypatch.setattr(updater.sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))
        with pytest.raises(SystemExit):
            updater.restart_app()
        assert any("launcher.py" in str(a) for a in captured["args"])

    def test_uses_execv_on_posix(self, tmp_root, monkeypatch):
        (tmp_root / "launcher.py").write_text("")
        captured = {}
        def fake_execv(path, argv):
            captured["path"] = path
            captured["argv"] = argv
            raise SystemExit(0)
        monkeypatch.setattr(updater.os, "execv", fake_execv)
        monkeypatch.setattr(updater.sys, "platform", "linux")
        with pytest.raises(SystemExit):
            updater.restart_app()
        assert any("launcher.py" in str(a) for a in captured["argv"])


# ── Git helpers ─────────────────────────────────────────────────────────────

class TestGitHelpers:
    def test_git_unavailable_when_no_dotgit(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater.shutil, "which", lambda c: "/usr/bin/git")
        # No .git dir → not git managed
        assert updater._git_available() is False

    def test_git_unavailable_when_no_binary(self, tmp_root, monkeypatch):
        (tmp_root / ".git").mkdir()
        monkeypatch.setattr(updater.shutil, "which", lambda c: None)
        assert updater._git_available() is False

    def test_clean_when_not_git(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: False)
        assert updater._git_is_clean() is True

    def test_dirty_when_porcelain_nonempty(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: True)
        monkeypatch.setattr(updater, "_git",
                            lambda *a, **k: " M file.py")
        assert updater._git_is_clean() is False
