"""
Tests for pure functions in notion_anki_sync.py.
No real API calls — all Notion/Anki interactions are mocked or tested via data fixtures.
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Make root importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures — fake Notion page objects
# ──────────────────────────────────────────────────────────────────────────────

def _make_page(properties: dict, page_id: str = "test-page-id",
               last_edited: str = "2024-01-15T10:00:00.000Z") -> dict:
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": properties,
    }


def _title_prop(text: str) -> dict:
    return {"type": "title", "title": [{"plain_text": text}]}


def _rich_text_prop(text: str) -> dict:
    return {"type": "rich_text", "rich_text": [{"plain_text": text}]}


def _select_prop(value: str) -> dict:
    return {"type": "select", "select": {"name": value}}


def _date_prop(start: str) -> dict:
    return {"type": "date", "date": {"start": start}}


def _empty_title_prop() -> dict:
    return {"type": "title", "title": []}


# ──────────────────────────────────────────────────────────────────────────────
# Import helpers directly (bypass top-level client init)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Prevent actual env loading and client creation."""
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AI_PROVIDER", "claude")


def _import_sync():
    """Import sync module with mocked clients."""
    with patch("anthropic.Anthropic"), \
         patch("notion_client.Client"):
        import importlib
        import notion_anki_sync as m
        importlib.reload(m)
        return m


# ──────────────────────────────────────────────────────────────────────────────
# get_title_value
# ──────────────────────────────────────────────────────────────────────────────

class TestGetTitleValue:
    def setup_method(self):
        self.m = _import_sync()

    def test_returns_title_text(self):
        page = _make_page({"Name": _title_prop("Matemática")})
        assert self.m.get_title_value(page, "Name") == "Matemática"

    def test_returns_rich_text(self):
        page = _make_page({"Notes": _rich_text_prop("Conteúdo da aula")})
        assert self.m.get_title_value(page, "Notes") == "Conteúdo da aula"

    def test_empty_title_returns_empty_string(self):
        page = _make_page({"Name": _empty_title_prop()})
        assert self.m.get_title_value(page, "Name") == ""

    def test_missing_property_returns_empty_string(self):
        page = _make_page({})
        assert self.m.get_title_value(page, "Inexistente") == ""

    def test_strips_whitespace(self):
        page = _make_page({"Name": _title_prop("  Física  ")})
        assert self.m.get_title_value(page, "Name") == "Física"


# ──────────────────────────────────────────────────────────────────────────────
# get_select_value
# ──────────────────────────────────────────────────────────────────────────────

class TestGetSelectValue:
    def setup_method(self):
        self.m = _import_sync()

    def test_returns_select_name(self):
        page = _make_page({"Status": _select_prop("✅ Completa")})
        assert self.m.get_select_value(page, "Status") == "✅ Completa"

    def test_missing_select_returns_empty(self):
        page = _make_page({"Status": {"type": "select", "select": None}})
        assert self.m.get_select_value(page, "Status") == ""

    def test_missing_property_returns_empty(self):
        page = _make_page({})
        assert self.m.get_select_value(page, "Status") == ""


# ──────────────────────────────────────────────────────────────────────────────
# get_date_value
# ──────────────────────────────────────────────────────────────────────────────

class TestGetDateValue:
    def setup_method(self):
        self.m = _import_sync()

    def test_returns_date_start(self):
        page = _make_page({"Data": _date_prop("2024-03-15")})
        assert self.m.get_date_value(page, "Data") == "2024-03-15"

    def test_null_date_returns_empty(self):
        page = _make_page({"Data": {"type": "date", "date": {"start": None}}})
        assert self.m.get_date_value(page, "Data") == ""

    def test_missing_date_returns_empty(self):
        page = _make_page({"Data": {"type": "date", "date": None}})
        assert self.m.get_date_value(page, "Data") == ""

    def test_missing_property_returns_empty(self):
        page = _make_page({})
        assert self.m.get_date_value(page, "Data") == ""


# ──────────────────────────────────────────────────────────────────────────────
# get_rich_text_value
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRichTextValue:
    def setup_method(self):
        self.m = _import_sync()

    def test_returns_text(self):
        page = _make_page({"Resumo": _rich_text_prop("Texto do resumo")})
        assert self.m.get_rich_text_value(page, "Resumo") == "Texto do resumo"

    def test_empty_rich_text_returns_empty(self):
        page = _make_page({"Resumo": {"type": "rich_text", "rich_text": []}})
        assert self.m.get_rich_text_value(page, "Resumo") == ""

    def test_missing_property_returns_empty(self):
        page = _make_page({})
        assert self.m.get_rich_text_value(page, "Resumo") == ""

    def test_multiple_segments_concatenated(self):
        page = _make_page({
            "Resumo": {
                "type": "rich_text",
                "rich_text": [
                    {"plain_text": "Parte 1 "},
                    {"plain_text": "Parte 2"},
                ]
            }
        })
        assert self.m.get_rich_text_value(page, "Resumo") == "Parte 1 Parte 2"


# ──────────────────────────────────────────────────────────────────────────────
# extrair_texto_blocos
# ──────────────────────────────────────────────────────────────────────────────

class TestExtrairTextoBlocos:
    def setup_method(self):
        self.m = _import_sync()

    def _block(self, tipo: str, text: str, has_children: bool = False) -> dict:
        return {
            "type": tipo,
            "id": "block-id",
            "has_children": has_children,
            tipo: {"rich_text": [{"plain_text": text}]},
        }

    def test_paragraph(self):
        blocks = [self._block("paragraph", "Olá mundo")]
        result = self.m.extrair_texto_blocos(blocks)
        assert "Olá mundo" in result

    def test_heading_prefixed(self):
        blocks = [self._block("heading_2", "Título da seção")]
        result = self.m.extrair_texto_blocos(blocks)
        assert "## Título da seção" in result

    def test_divider(self):
        block = {"type": "divider", "id": "x", "has_children": False, "divider": {}}
        result = self.m.extrair_texto_blocos([block])
        assert "---" in result

    def test_empty_text_skipped(self):
        blocks = [self._block("paragraph", "   ")]
        result = self.m.extrair_texto_blocos(blocks)
        assert result.strip() == ""

    def test_multiple_blocks_joined_by_newline(self):
        blocks = [
            self._block("paragraph", "Linha 1"),
            self._block("paragraph", "Linha 2"),
        ]
        result = self.m.extrair_texto_blocos(blocks)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) == 2

    def test_code_block_wrapped(self):
        block = {
            "type": "code",
            "id": "x",
            "has_children": False,
            "code": {"rich_text": [{"plain_text": "print('hello')"}]},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "```" in result
        assert "print('hello')" in result


# ──────────────────────────────────────────────────────────────────────────────
# is_newer_than
# ──────────────────────────────────────────────────────────────────────────────

class TestIsNewerThan:
    def setup_method(self):
        self.m = _import_sync()

    def _page_with_time(self, iso: str) -> dict:
        return {"last_edited_time": iso, "id": "x", "properties": {}}

    def test_newer_page_returns_true(self):
        page = self._page_with_time("2024-06-01T10:00:00.000Z")
        assert self.m.is_newer_than(page, "2024-01-01T00:00:00+00:00") is True

    def test_older_page_returns_false(self):
        page = self._page_with_time("2023-01-01T10:00:00.000Z")
        assert self.m.is_newer_than(page, "2024-01-01T00:00:00+00:00") is False

    def test_none_timestamp_returns_true(self):
        page = self._page_with_time("2024-01-01T10:00:00.000Z")
        assert self.m.is_newer_than(page, None) is True

    def test_same_time_returns_false(self):
        ts = "2024-06-01T10:00:00+00:00"
        page = self._page_with_time("2024-06-01T10:00:00.000Z")
        assert self.m.is_newer_than(page, ts) is False


# ──────────────────────────────────────────────────────────────────────────────
# load_notion_config / save_notion_config
# ──────────────────────────────────────────────────────────────────────────────

class TestNotionConfig:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        import notion_anki_sync as m
        cfg_file = tmp_path / "notion_config.json"
        monkeypatch.setattr(m, "NOTION_CONFIG_FILE", cfg_file)

        data = {
            "version": 2,
            "mode": "hierarchical",
            "parent_db_id": "abc-123",
            "anki_deck_root": "Estudos::Concurso",
            "last_sync_time": None,
        }
        m.save_notion_config(data)
        loaded = m.load_notion_config()

        assert loaded["parent_db_id"] == "abc-123"
        assert loaded["mode"] == "hierarchical"
        assert loaded["last_sync_time"] is None

    def test_load_returns_none_when_absent(self, tmp_path, monkeypatch):
        import notion_anki_sync as m
        monkeypatch.setattr(m, "NOTION_CONFIG_FILE", tmp_path / "nonexistent.json")
        assert m.load_notion_config() is None

    def test_save_preserves_unicode(self, tmp_path, monkeypatch):
        import notion_anki_sync as m
        cfg_file = tmp_path / "notion_config.json"
        monkeypatch.setattr(m, "NOTION_CONFIG_FILE", cfg_file)

        data = {"sync_done": "✅ Sincronizado", "deck": "Estudo::Concurso"}
        m.save_notion_config(data)
        raw = cfg_file.read_text(encoding="utf-8")
        assert "✅ Sincronizado" in raw


# ──────────────────────────────────────────────────────────────────────────────
# Flashcard validation / cleaning
# ──────────────────────────────────────────────────────────────────────────────

class TestStripCodeFence:
    def test_no_fence_passes_through(self):
        import notion_anki_sync as m
        assert m._strip_code_fence('[{"a":1}]') == '[{"a":1}]'

    def test_strips_json_fence(self):
        import notion_anki_sync as m
        raw = "```json\n[{\"a\":1}]\n```"
        assert m._strip_code_fence(raw) == '[{"a":1}]'

    def test_strips_plain_fence(self):
        import notion_anki_sync as m
        raw = "```\n[{\"a\":1}]\n```"
        assert m._strip_code_fence(raw) == '[{"a":1}]'

    def test_strips_outer_whitespace(self):
        import notion_anki_sync as m
        assert m._strip_code_fence("   [1,2]   ") == "[1,2]"


class TestValidateAndClean:
    def _m(self):
        import notion_anki_sync as m
        return m

    def test_valid_cards_pass(self):
        cards = [{"front": "Q1", "back": "A1"}, {"front": "Q2", "back": "A2"}]
        assert self._m()._validate_and_clean(cards, 10) == cards

    def test_drops_missing_back(self):
        cards = [{"front": "Q", "back": ""}, {"front": "Q2", "back": "A"}]
        out = self._m()._validate_and_clean(cards, 10)
        assert len(out) == 1
        assert out[0]["front"] == "Q2"

    def test_drops_missing_front(self):
        cards = [{"back": "orphan"}, {"front": "Q", "back": "A"}]
        out = self._m()._validate_and_clean(cards, 10)
        assert [c["front"] for c in out] == ["Q"]

    def test_drops_non_dicts(self):
        cards = ["not a dict", 42, {"front": "Q", "back": "A"}, None]
        out = self._m()._validate_and_clean(cards, 10)
        assert out == [{"front": "Q", "back": "A"}]

    def test_dedupes_by_front_case_insensitive(self):
        cards = [
            {"front": "What is X?", "back": "A"},
            {"front": "what is x?", "back": "B"},   # dupe
            {"front": "Different",  "back": "C"},
        ]
        out = self._m()._validate_and_clean(cards, 10)
        assert len(out) == 2
        assert [c["back"] for c in out] == ["A", "C"]

    def test_strips_whitespace(self):
        cards = [{"front": "  Q  ", "back": "  A  "}]
        out = self._m()._validate_and_clean(cards, 10)
        assert out == [{"front": "Q", "back": "A"}]

    def test_enforces_max_cap(self):
        cards = [{"front": f"Q{i}", "back": f"A{i}"} for i in range(20)]
        out = self._m()._validate_and_clean(cards, 5)
        assert len(out) == 5
        assert out[-1]["front"] == "Q4"

    def test_max_zero_returns_empty(self):
        cards = [{"front": "Q", "back": "A"}]
        assert self._m()._validate_and_clean(cards, 0) == []
