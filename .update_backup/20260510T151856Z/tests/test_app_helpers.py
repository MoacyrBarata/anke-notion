"""
Tests for pure/mockable helpers in app.py.
app.py is a top-level Streamlit script — we mock streamlit entirely before import.
"""

import sys
import importlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# Shared import helper — reloads app with full streamlit mock each time
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def app(mock_streamlit):
    """Import app with Streamlit fully mocked."""
    # Remove cached module so reload works cleanly
    sys.modules.pop("app", None)
    import app as _app
    return _app


# ──────────────────────────────────────────────────────────────────────────────
# check_ai_key
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckAiKey:
    def test_claude_valid_key(self, app):
        ok, msg = app.check_ai_key("claude", "sk-ant-api03-somevalidkey")
        assert ok is True
        assert "válido" in msg

    def test_claude_invalid_key(self, app):
        ok, _ = app.check_ai_key("claude", "invalid-key")
        assert ok is False

    def test_claude_empty_key(self, app):
        ok, msg = app.check_ai_key("claude", "")
        assert ok is False
        assert "não informada" in msg.lower()

    def test_gemini_valid_key(self, app):
        ok, _ = app.check_ai_key("gemini", "AIzaSyAbcdefghijklmnop")
        assert ok is True

    def test_gemini_invalid_prefix(self, app):
        ok, _ = app.check_ai_key("gemini", "wrongprefix12345678901234")
        assert ok is False

    def test_gemini_too_short(self, app):
        ok, _ = app.check_ai_key("gemini", "AIza")
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────────
# parse_stats
# ──────────────────────────────────────────────────────────────────────────────

class TestParseStats:
    def test_parses_all_metrics(self, app):
        lines = [
            "2024-01-01 [INFO] Categorias processadas : 3",
            "2024-01-01 [INFO] Itens processados      : 12",
            "2024-01-01 [INFO] Flashcards gerados     : 87",
            "2024-01-01 [INFO] Flashcards no Anki     : 85",
            "2024-01-01 [INFO] Erros                  : 2",
        ]
        s = app.parse_stats(lines)
        assert s["disciplinas"] == 3
        assert s["itens"]       == 12
        assert s["gerados"]     == 87
        assert s["enviados"]    == 85
        assert s["erros"]       == 2

    def test_empty_log_returns_zeros(self, app):
        s = app.parse_stats([])
        assert all(v == 0 for v in s.values())

    def test_partial_log(self, app):
        lines = ["Categorias processadas : 1", "Erros                  : 0"]
        s = app.parse_stats(lines)
        assert s["disciplinas"] == 1
        assert s["erros"]       == 0
        assert s["gerados"]     == 0

    def test_ignores_noise_lines(self, app):
        lines = ["Iniciando...", "Conectado.", "Erros                  : 1"]
        s = app.parse_stats(lines)
        assert s["erros"]   == 1
        assert s["gerados"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# get_db_title
# ──────────────────────────────────────────────────────────────────────────────

class TestGetDbTitle:
    def test_extracts_title(self, app):
        db = {"title": [{"plain_text": "Banco de Disciplinas"}], "id": "abc"}
        assert app.get_db_title(db) == "Banco de Disciplinas"

    def test_empty_title_falls_back_to_id_prefix(self, app):
        db = {"title": [], "id": "abcdefgh-1234"}
        result = app.get_db_title(db)
        assert len(result) <= 8

    def test_missing_title_falls_back_to_id(self, app):
        db = {"id": "xyz12345-abc"}
        assert app.get_db_title(db)  # non-empty


# ──────────────────────────────────────────────────────────────────────────────
# props_by_type
# ──────────────────────────────────────────────────────────────────────────────

class TestPropsByType:
    def _props(self):
        return {
            "Nome":   {"type": "title"},
            "Resumo": {"type": "rich_text"},
            "Status": {"type": "select"},
            "Data":   {"type": "date"},
            "Feito":  {"type": "checkbox"},
        }

    def test_filters_single_type(self, app):
        assert app.props_by_type(self._props(), "title") == ["Nome"]

    def test_filters_multiple_types(self, app):
        result = app.props_by_type(self._props(), "rich_text", "title")
        assert "Nome" in result
        assert "Resumo" in result
        assert "Status" not in result

    def test_no_match_returns_empty(self, app):
        assert app.props_by_type(self._props(), "number") == []

    def test_empty_props_returns_empty(self, app):
        assert app.props_by_type({}, "title") == []


# ──────────────────────────────────────────────────────────────────────────────
# check_notion (mocked HTTP)
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckNotion:
    def test_valid_token_returns_true(self, app):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"name": "Test User"}
        with patch("app.requests.get", return_value=resp):
            ok, msg = app.check_notion("secret_validtoken")
        assert ok is True
        assert "Autenticado" in msg

    def test_invalid_token_returns_false(self, app):
        resp = MagicMock(status_code=401)
        with patch("app.requests.get", return_value=resp):
            ok, _ = app.check_notion("secret_badtoken")
        assert ok is False

    def test_empty_token_no_network_call(self, app):
        with patch("app.requests.get") as mock_get:
            ok, _ = app.check_notion("")
        assert ok is False
        mock_get.assert_not_called()

    def test_network_error_returns_false(self, app):
        with patch("app.requests.get", side_effect=Exception("timeout")):
            ok, _ = app.check_notion("secret_token")
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────────
# check_anki (mocked HTTP)
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckAnki:
    def test_anki_available(self, app):
        resp = MagicMock()
        resp.json.return_value = {"result": 6, "error": None}
        with patch("app.requests.post", return_value=resp):
            ok, msg = app.check_anki("http://localhost:8765")
        assert ok is True
        assert "6" in msg

    def test_anki_returns_error_field(self, app):
        resp = MagicMock()
        resp.json.return_value = {"result": None, "error": "not running"}
        with patch("app.requests.post", return_value=resp):
            ok, _ = app.check_anki("http://localhost:8765")
        assert ok is False

    def test_connection_refused(self, app):
        with patch("app.requests.post", side_effect=Exception("refused")):
            ok, _ = app.check_anki("http://localhost:8765")
        assert ok is False

    def test_empty_host_returns_false(self, app):
        ok, _ = app.check_anki("")
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────────
# load_cfg / save_cfg
# ──────────────────────────────────────────────────────────────────────────────

class TestCfgIO:
    def test_save_and_load(self, tmp_path, mock_streamlit):
        sys.modules.pop("app", None)
        import app
        env_file = tmp_path / ".env"
        app.ENV_FILE = env_file

        app.save_cfg({"NOTION_TOKEN": "secret_abc", "AI_PROVIDER": "claude"})
        cfg = app.load_cfg()

        assert cfg["NOTION_TOKEN"] == "secret_abc"
        assert cfg["AI_PROVIDER"]  == "claude"

    def test_load_returns_empty_when_absent(self, tmp_path, mock_streamlit):
        sys.modules.pop("app", None)
        import app
        app.ENV_FILE = tmp_path / "nonexistent.env"
        assert app.load_cfg() == {}
