"""
Unit tests for pure data-helper functions in app_flet.py.
No Flet GUI is instantiated here.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_flet


# ── load_cfg / save_cfg ────────────────────────────────────────────────────────

def test_load_cfg_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_flet, "ENV_FILE", tmp_path / "nonexistent.env")
    assert app_flet.load_cfg() == {}


def test_load_cfg_reads_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('NOTION_TOKEN="secret_abc"\nAI_PROVIDER="gemini"\n', encoding="utf-8")
    monkeypatch.setattr(app_flet, "ENV_FILE", env)
    cfg = app_flet.load_cfg()
    assert cfg.get("NOTION_TOKEN") == "secret_abc"
    assert cfg.get("AI_PROVIDER") == "gemini"


def test_save_cfg_creates_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(app_flet, "ENV_FILE", env)
    app_flet.save_cfg({"MY_KEY": "my_value"})
    assert env.exists()
    content = env.read_text(encoding="utf-8")
    assert "MY_KEY" in content
    assert "my_value" in content


def test_save_cfg_overwrites_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('TOKEN="old"\n', encoding="utf-8")
    monkeypatch.setattr(app_flet, "ENV_FILE", env)
    app_flet.save_cfg({"TOKEN": "new"})
    cfg = app_flet.load_cfg()
    assert cfg["TOKEN"] == "new"


def test_save_cfg_none_value_becomes_empty_string(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(app_flet, "ENV_FILE", env)
    app_flet.save_cfg({"EMPTY_KEY": None})
    content = env.read_text(encoding="utf-8")
    assert "EMPTY_KEY" in content


# ── load_notion_config / save_notion_config ────────────────────────────────────

def test_load_notion_config_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CFG_FILE", tmp_path / "no.json")
    assert app_flet.load_notion_config() is None


def test_load_notion_config_reads_json(tmp_path, monkeypatch):
    data = {"mode": "flat", "db_id": "abc123"}
    cfg_file = tmp_path / "notion_config.json"
    cfg_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(app_flet, "NOTION_CFG_FILE", cfg_file)
    assert app_flet.load_notion_config() == data


def test_save_notion_config_writes_json(tmp_path, monkeypatch):
    cfg_file = tmp_path / "notion_config.json"
    monkeypatch.setattr(app_flet, "NOTION_CFG_FILE", cfg_file)
    app_flet.save_notion_config({"mode": "hierarchical", "parent_db_id": "xyz"})
    saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved["mode"] == "hierarchical"
    assert saved["parent_db_id"] == "xyz"


def test_save_and_load_notion_config_roundtrip(tmp_path, monkeypatch):
    cfg_file = tmp_path / "notion_config.json"
    monkeypatch.setattr(app_flet, "NOTION_CFG_FILE", cfg_file)
    payload = {"mode": "flat", "anki_deck_root": "Notion::Med", "last_sync_time": None}
    app_flet.save_notion_config(payload)
    loaded = app_flet.load_notion_config()
    assert loaded == payload


# ── check_notion ───────────────────────────────────────────────────────────────

def test_check_notion_empty_token():
    ok, msg = app_flet.check_notion("")
    assert not ok
    assert "Token" in msg


def test_check_notion_success(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"name": "Alice"}
    monkeypatch.setattr("app_flet.requests.get", lambda *a, **kw: resp)
    ok, msg = app_flet.check_notion("secret_abc")
    assert ok
    assert "Alice" in msg


def test_check_notion_success_no_name(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {}
    monkeypatch.setattr("app_flet.requests.get", lambda *a, **kw: resp)
    ok, msg = app_flet.check_notion("secret_abc")
    assert ok
    assert "Autenticado" in msg


def test_check_notion_401(monkeypatch):
    resp = MagicMock()
    resp.status_code = 401
    resp.json.return_value = {}
    monkeypatch.setattr("app_flet.requests.get", lambda *a, **kw: resp)
    ok, msg = app_flet.check_notion("bad_token")
    assert not ok
    assert "401" in msg


def test_check_notion_connection_error(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("no network")
    monkeypatch.setattr("app_flet.requests.get", boom)
    ok, msg = app_flet.check_notion("secret_x")
    assert not ok
    assert "conexão" in msg.lower()


# ── check_anki ─────────────────────────────────────────────────────────────────

def test_check_anki_empty_host():
    ok, msg = app_flet.check_anki("")
    assert not ok
    assert "Host" in msg


def test_check_anki_ok(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {"result": 6, "error": None}
    monkeypatch.setattr("app_flet.requests.post", lambda *a, **kw: resp)
    ok, msg = app_flet.check_anki("http://localhost:8765")
    assert ok
    assert "6" in msg


def test_check_anki_error_in_body(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {"result": None, "error": "deck not found"}
    monkeypatch.setattr("app_flet.requests.post", lambda *a, **kw: resp)
    ok, msg = app_flet.check_anki("http://localhost:8765")
    assert not ok
    assert "deck not found" in msg


def test_check_anki_exception(monkeypatch):
    def boom(*a, **kw):
        raise OSError("connection refused")
    monkeypatch.setattr("app_flet.requests.post", boom)
    ok, msg = app_flet.check_anki("http://localhost:8765")
    assert not ok
    assert "Anki" in msg or "fechado" in msg.lower()


# ── check_ai_key ───────────────────────────────────────────────────────────────

def test_check_ai_key_empty_key():
    ok, msg = app_flet.check_ai_key("claude", "")
    assert not ok
    assert "Chave" in msg


def test_check_ai_key_claude_valid():
    ok, msg = app_flet.check_ai_key("claude", "sk-ant-abc123")
    assert ok
    assert "válido" in msg.lower()


def test_check_ai_key_claude_invalid():
    ok, msg = app_flet.check_ai_key("claude", "wrong-prefix-key")
    assert not ok


def test_check_ai_key_gemini_valid():
    ok, msg = app_flet.check_ai_key("gemini", "AIzaSyABCDEFGHIJKLMNOP")
    assert ok


def test_check_ai_key_gemini_too_short():
    ok, msg = app_flet.check_ai_key("gemini", "AIza")
    assert not ok


def test_check_ai_key_gemini_invalid_prefix():
    ok, msg = app_flet.check_ai_key("gemini", "sk-ant-xyz" * 3)
    assert not ok


# ── parse_stats ────────────────────────────────────────────────────────────────

def test_parse_stats_empty():
    result = app_flet.parse_stats([])
    assert result == {"disciplinas": 0, "itens": 0, "gerados": 0, "enviados": 0, "erros": 0}


def test_parse_stats_full():
    lines = [
        "Categorias processadas: 3",
        "Itens processados: 42",
        "Flashcards gerados: 120",
        "Flashcards no Anki: 118",
        "Erros: 2",
    ]
    result = app_flet.parse_stats(lines)
    assert result == {"disciplinas": 3, "itens": 42, "gerados": 120, "enviados": 118, "erros": 2}


def test_parse_stats_partial():
    lines = ["Itens processados: 7", "Erros: 0"]
    result = app_flet.parse_stats(lines)
    assert result["itens"] == 7
    assert result["erros"] == 0
    assert result["disciplinas"] == 0


def test_parse_stats_ignores_non_numeric():
    lines = ["Erros: N/A", "Itens processados: abc"]
    result = app_flet.parse_stats(lines)
    assert result["erros"] == 0
    assert result["itens"] == 0


# ── get_db_title ───────────────────────────────────────────────────────────────

def test_get_db_title_normal():
    db = {"title": [{"plain_text": "My Database"}], "id": "abc12345678"}
    assert app_flet.get_db_title(db) == "My Database"


def test_get_db_title_fallback_on_empty_title():
    db = {"title": [], "id": "abc12345678"}
    assert app_flet.get_db_title(db) == "abc12345"


def test_get_db_title_fallback_on_missing_title():
    db = {"id": "xyz98765432"}
    assert app_flet.get_db_title(db) == "xyz98765"


def test_get_db_title_fallback_on_missing_plain_text():
    db = {"title": [{}], "id": "fallback12"}
    assert app_flet.get_db_title(db) == "fallback"


# ── props_by_type ──────────────────────────────────────────────────────────────

def test_props_by_type_single():
    props = {
        "Name": {"type": "title"},
        "Due": {"type": "date"},
        "Status": {"type": "select"},
    }
    result = app_flet.props_by_type(props, "title")
    assert result == ["Name"]


def test_props_by_type_multiple_types():
    props = {
        "Name": {"type": "title"},
        "Notes": {"type": "rich_text"},
        "Due": {"type": "date"},
        "Content": {"type": "rich_text"},
    }
    result = app_flet.props_by_type(props, "title", "rich_text")
    assert set(result) == {"Name", "Notes", "Content"}


def test_props_by_type_no_match():
    props = {"X": {"type": "number"}}
    assert app_flet.props_by_type(props, "title") == []


def test_props_by_type_empty_props():
    assert app_flet.props_by_type({}, "title") == []


# ── run_sync ───────────────────────────────────────────────────────────────────

def test_run_sync_calls_popen(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr("app_flet.subprocess.Popen", mock_popen)
    app_flet.run_sync({"NOTION_TOKEN": "t", "AI_PROVIDER": "claude"})
    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert "notion_anki_sync.py" in args[1]


def test_run_sync_skips_empty_values(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return MagicMock()

    monkeypatch.setattr("app_flet.subprocess.Popen", fake_popen)
    app_flet.run_sync({"NOTION_TOKEN": "tok", "EMPTY_KEY": ""})
    assert "NOTION_TOKEN" in captured["env"]
    assert "EMPTY_KEY" not in captured["env"]


def test_run_sync_uses_pipe(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr("app_flet.subprocess.Popen", mock_popen)
    app_flet.run_sync({})
    kwargs = mock_popen.call_args[1]
    assert kwargs.get("stdout") == subprocess.PIPE


# ── list_notion_databases / get_database_properties ───────────────────────────

def test_list_notion_databases_returns_empty_without_token(monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CLIENT_AVAILABLE", True)
    dbs, err = app_flet.list_notion_databases(""); assert dbs == [] and err is not None


def test_list_notion_databases_returns_empty_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CLIENT_AVAILABLE", False)
    dbs, err = app_flet.list_notion_databases("secret_tok"); assert dbs == [] and err is not None


def test_list_notion_databases_returns_results(monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CLIENT_AVAILABLE", True)
    fake_client = MagicMock()
    fake_client.search.return_value = {
        "results": [{"id": "db1"}, {"id": "db2"}],
        "has_more": False,
    }
    monkeypatch.setattr("app_flet.NotionClient", lambda **kw: fake_client)
    result, err = app_flet.list_notion_databases("secret_tok")
    assert len(result) == 2 and err is None


def test_list_notion_databases_handles_exception(monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CLIENT_AVAILABLE", True)

    def boom(**kw):
        raise RuntimeError("API down")

    monkeypatch.setattr("app_flet.NotionClient", boom)
    dbs, err = app_flet.list_notion_databases("secret_tok"); assert dbs == [] and err is not None


def test_get_database_properties_empty_without_token(monkeypatch):
    monkeypatch.setattr(app_flet, "NOTION_CLIENT_AVAILABLE", True)
    assert app_flet.get_database_properties("", "db_id") == {}


def test_get_database_properties_returns_props(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "properties": {"Name": {"type": "title"}, "Due": {"type": "date"}}
    }
    fake_resp.raise_for_status = MagicMock()
    monkeypatch.setattr("app_flet.requests.get", lambda *a, **kw: fake_resp)
    result = app_flet.get_database_properties("secret_tok", "db_id_123")
    assert "Name" in result
    assert "Due" in result
