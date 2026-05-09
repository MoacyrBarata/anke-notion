"""
Tests for pure/file-I/O functions in launcher.py.
No subprocess spawning, no GUI, no Streamlit.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import launcher


# ──────────────────────────────────────────────────────────────────────────────
# get_os / _os
# ──────────────────────────────────────────────────────────────────────────────

class TestGetOs:
    def test_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert launcher._os() == "windows"

    def test_mac(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert launcher._os() == "mac"

    def test_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert launcher._os() == "linux"

    def test_unknown_defaults_to_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        assert launcher._os() == "linux"


# ──────────────────────────────────────────────────────────────────────────────
# get_venv_python / _venv_python
# ──────────────────────────────────────────────────────────────────────────────

class TestGetVenvPython:
    def test_windows_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        result = launcher._venv_python()
        assert "Scripts" in str(result)
        assert str(result).endswith(".exe")

    def test_unix_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        result = launcher._venv_python()
        assert "bin" in str(result)
        assert "python" in str(result)
        assert not str(result).endswith(".exe")


# ──────────────────────────────────────────────────────────────────────────────
# _count_packages
# ──────────────────────────────────────────────────────────────────────────────

class TestCountPackages:
    def test_counts_non_comment_lines(self, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text(
            "# Core\n"
            "notion-client==3.0.0\n"
            "requests==2.33.1\n"
            "\n"
            "# IA\n"
            "anthropic>=0.99.0\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(launcher, "REQ_FILE", req)
        pkgs = launcher._count_packages()
        assert len(pkgs) == 3
        assert "notion-client" in pkgs
        assert "requests" in pkgs
        assert "anthropic" in pkgs

    def test_strips_version_specifiers(self, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text("streamlit>=1.35.0\npillow\n", encoding="utf-8")
        monkeypatch.setattr(launcher, "REQ_FILE", req)
        pkgs = launcher._count_packages()
        assert "streamlit" in pkgs
        assert "pillow" in pkgs
        assert all(">=" not in p and "==" not in p for p in pkgs)

    def test_empty_file_returns_empty_list(self, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text("# only comments\n\n", encoding="utf-8")
        monkeypatch.setattr(launcher, "REQ_FILE", req)
        assert launcher._count_packages() == []

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "REQ_FILE", tmp_path / "nonexistent.txt")
        assert launcher._count_packages() == []


# ──────────────────────────────────────────────────────────────────────────────
# _skip_streamlit_email_prompt
# ──────────────────────────────────────────────────────────────────────────────

class TestSkipEmailPrompt:
    def test_creates_credentials_file(self, tmp_path, monkeypatch):
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        launcher._skip_streamlit_email_prompt()
        creds = fake_home / ".streamlit" / "credentials.toml"
        assert creds.exists()
        content = creds.read_text(encoding="utf-8")
        assert '[general]' in content
        assert 'email' in content

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        creds_dir = fake_home / ".streamlit"
        creds_dir.mkdir(parents=True)
        creds_file = creds_dir / "credentials.toml"
        creds_file.write_text('[general]\nemail = "myemail@test.com"\n')

        launcher._skip_streamlit_email_prompt()

        content = creds_file.read_text()
        assert "myemail@test.com" in content  # original preserved


# ──────────────────────────────────────────────────────────────────────────────
# _needs_install
# ──────────────────────────────────────────────────────────────────────────────

class TestNeedsInstall:
    def test_true_when_marker_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "REQ_MARKER", tmp_path / ".installed")
        assert launcher._needs_install() is True

    def test_false_when_marker_present_and_req_older(self, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text("streamlit\n")
        marker = tmp_path / ".installed"
        marker.touch()

        import os, time
        # Make sure marker is newer than req
        os.utime(req,    (time.time() - 100, time.time() - 100))
        os.utime(marker, (time.time(),       time.time()))

        monkeypatch.setattr(launcher, "REQ_FILE",   req)
        monkeypatch.setattr(launcher, "REQ_MARKER", marker)
        assert launcher._needs_install() is False

    def test_true_when_req_newer_than_marker(self, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text("streamlit\n")
        marker = tmp_path / ".installed"
        marker.touch()

        import os, time
        os.utime(marker, (time.time() - 100, time.time() - 100))
        os.utime(req,    (time.time(),       time.time()))

        monkeypatch.setattr(launcher, "REQ_FILE",   req)
        monkeypatch.setattr(launcher, "REQ_MARKER", marker)
        assert launcher._needs_install() is True


# ──────────────────────────────────────────────────────────────────────────────
# Terminal helpers (no crashes, no side effects)
# ──────────────────────────────────────────────────────────────────────────────

class TestTerminalHelpers:
    def test_render_bar_does_not_raise(self, capsys):
        launcher._term_progress(5, 10, "teste")
        launcher._term_progress(0, 0, "edge case zero total")
        launcher._term_progress(10, 10, "completo")

    def test_term_progress_done_does_not_raise(self, capsys):
        launcher._term_progress_done()

    def test_spinner_starts_and_stops(self):
        import time
        with launcher._Spinner("Testando spinner..."):
            time.sleep(0.15)
        # If we reach here the spinner started and stopped cleanly

    def test_ansi_helpers_return_string(self):
        assert isinstance(launcher._green("ok"), str)
        assert isinstance(launcher._cyan("ok"), str)
        assert isinstance(launcher._yellow("ok"), str)
        assert isinstance(launcher._bold("ok"), str)
        assert isinstance(launcher._dim("ok"), str)
