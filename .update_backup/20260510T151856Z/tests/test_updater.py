"""Tests for updater.py — self-update mechanism."""
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import updater


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    """Redirect updater paths to a tmp dir so tests can't touch the real repo."""
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "VERSION_FILE", tmp_path / "VERSION")
    monkeypatch.setattr(updater, "BACKUP_DIR",   tmp_path / ".update_backup")
    return tmp_path


# ── Version parsing ──────────────────────────────────────────────────────────

class TestParseSemver:
    def test_basic(self):
        assert updater._parse_semver("0.2.0") == (0, 2, 0)

    def test_strips_v_prefix(self):
        assert updater._parse_semver("v1.2.3") == (1, 2, 3)

    def test_strips_pre_release(self):
        assert updater._parse_semver("1.2.3-beta1") == (1, 2, 3)

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

    def test_unparseable_uses_string_diff(self):
        # SHA-style strings: any non-equal remote means newer
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


# ── GitHub API ───────────────────────────────────────────────────────────────

class TestGetRemoteVersion:
    def test_prefers_release(self, tmp_root, monkeypatch):
        responses = [
            {"tag_name": "v1.0.0", "html_url": "u", "body": "notes"},
        ]
        monkeypatch.setattr(updater, "_http_json",
                            lambda url: responses.pop(0))
        info = updater.get_remote_version()
        assert info["version"] == "v1.0.0"
        assert info["source"]  == "release"
        assert info["notes"]   == "notes"

    def test_falls_back_to_commits(self, tmp_root, monkeypatch):
        seq = iter([
            None,  # /releases/latest fails
            {"sha": "abcdef1234567890", "html_url": "u",
             "commit": {"message": "feat: x\n\nbody"}},
        ])
        monkeypatch.setattr(updater, "_http_json", lambda url: next(seq))
        info = updater.get_remote_version()
        assert info["version"] == "abcdef1"
        assert info["source"]  == "commit"
        assert info["sha"]     == "abcdef1234567890"
        assert info["notes"]   == "feat: x"

    def test_returns_none_on_total_failure(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_http_json", lambda url: None)
        assert updater.get_remote_version() is None


class TestCheckForUpdate:
    def test_has_update_when_remote_newer(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.1.0", encoding="utf-8")
        monkeypatch.setattr(updater, "get_remote_version", lambda: {
            "version": "0.2.0", "sha": None, "url": "u", "notes": "",
            "source": "release",
        })
        out = updater.check_for_update()
        assert out["has_update"] is True
        assert out["current"] == "0.1.0"
        assert out["latest"]  == "0.2.0"
        assert out["error"]   is None

    def test_no_update_when_equal(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(updater, "get_remote_version", lambda: {
            "version": "0.2.0", "sha": None, "url": "u", "notes": "",
            "source": "release",
        })
        assert updater.check_for_update()["has_update"] is False

    def test_error_when_remote_unreachable(self, tmp_root, monkeypatch):
        (tmp_root / "VERSION").write_text("0.2.0", encoding="utf-8")
        monkeypatch.setattr(updater, "get_remote_version", lambda: None)
        out = updater.check_for_update()
        assert out["has_update"] is False
        assert out["error"]


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
        # Simulate a broken update
        (tmp_root / "code.py").write_text("BROKEN", encoding="utf-8")
        (tmp_root / "lib" / "x.py").unlink()
        updater._restore_tree(dest)
        assert (tmp_root / "code.py").read_text(encoding="utf-8") == "v1"
        assert (tmp_root / "lib" / "x.py").read_text(encoding="utf-8") == "lib1"

    def test_restore_preserves_user_data(self, tmp_root):
        self._seed(tmp_root)
        dest = tmp_root / ".update_backup" / "x"
        updater._backup_tree(dest)
        # User edits .env after backup
        (tmp_root / ".env").write_text("SECRET=newer", encoding="utf-8")
        updater._restore_tree(dest)
        assert (tmp_root / ".env").read_text(encoding="utf-8") == "SECRET=newer"


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
        # User data preserved
        assert (tmp_root / ".env").read_text(encoding="utf-8") == "SECRET=keep"

    def test_returns_failure_when_download_fails(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_download_zip", lambda url: None)
        ok, msg = updater._apply_update_zip()
        assert ok is False
        assert "baixar" in msg.lower()


# ── apply_update strategy dispatch ───────────────────────────────────────────

class TestApplyUpdate:
    def test_explicit_zip_calls_zip(self, tmp_root, monkeypatch):
        called = {}
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (called.setdefault("z", True), "ok")[1] and (True, "ok"))
        # Easier: just stub both and assert which got called
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (False, "should not be called"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda: "")
        ok, msg = updater.apply_update("zip")
        assert ok and "zip" in msg

    def test_explicit_git_calls_git(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (True, "git ok"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (False, "should not be called"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda: "")
        ok, msg = updater.apply_update("git")
        assert ok and "git" in msg

    def test_auto_falls_back_to_zip_when_git_fails(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: True)
        monkeypatch.setattr(updater, "_apply_update_git",
                            lambda: (False, "uncommitted"))
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda: "")
        ok, msg = updater.apply_update("auto")
        assert ok
        assert "zip" in msg

    def test_auto_uses_zip_when_no_git(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_git_available", lambda: False)
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations", lambda: "")
        ok, msg = updater.apply_update("auto")
        assert ok

    def test_post_update_runs_migration(self, tmp_root, monkeypatch):
        monkeypatch.setattr(updater, "_apply_update_zip",
                            lambda: (True, "zip ok"))
        monkeypatch.setattr(updater, "_post_update_migrations",
                            lambda: "DB OK")
        ok, msg = updater.apply_update("zip")
        assert ok
        assert "DB OK" in msg


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


# ── List backups ─────────────────────────────────────────────────────────────

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
