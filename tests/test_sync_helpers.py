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
        self._next_id = 0

    def _block(self, tipo: str, text: str, has_children: bool = False) -> dict:
        # Unique id per block — real Notion blocks never share ids, and the
        # extractor now dedupes by id (cycle guard).
        self._next_id += 1
        return {
            "type": tipo,
            "id": f"block-{self._next_id}",
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

    def test_heading_levels_prefixed(self):
        blocks = [
            self._block("heading_1", "Cap 1"),
            self._block("heading_2", "Sec 1.1"),
            self._block("heading_3", "Sub 1.1.1"),
        ]
        result = self.m.extrair_texto_blocos(blocks)
        assert "# Cap 1" in result
        assert "## Sec 1.1" in result
        assert "### Sub 1.1.1" in result

    def test_to_do_unchecked(self):
        block = {
            "type": "to_do", "id": "x", "has_children": False,
            "to_do": {"rich_text": [{"plain_text": "comprar leite"}], "checked": False},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "[ ] comprar leite" in result

    def test_to_do_checked(self):
        block = {
            "type": "to_do", "id": "x", "has_children": False,
            "to_do": {"rich_text": [{"plain_text": "tarefa pronta"}], "checked": True},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "[x] tarefa pronta" in result

    def test_table_row_pipe_format(self):
        block = {
            "type": "table_row", "id": "x", "has_children": False,
            "table_row": {"cells": [
                [{"plain_text": "Ativo"}],
                [{"plain_text": "100"}],
                [{"plain_text": "200"}],
            ]},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "| Ativo | 100 | 200 |" in result

    def test_table_row_no_recursion_into_children(self):
        # table_row blocks never have meaningful children — must not recurse.
        block = {
            "type": "table_row", "id": "tr", "has_children": True,
            "table_row": {"cells": [[{"plain_text": "x"}]]},
        }
        from unittest.mock import patch, MagicMock
        with patch.object(self.m, "notion", MagicMock()) as mock_notion:
            self.m.extrair_texto_blocos([block])
            mock_notion.blocks.children.list.assert_not_called()

    def test_table_renders_via_rows(self):
        # table block has no rich_text — content comes from child table_rows.
        table_block = {"type": "table", "id": "t1", "has_children": True, "table": {}}
        row_block = {
            "type": "table_row", "id": "r1", "has_children": False,
            "table_row": {"cells": [[{"plain_text": "A"}], [{"plain_text": "B"}]]},
        }
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [row_block], "has_more": False, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            result = self.m.extrair_texto_blocos([table_block])
        assert "| A | B |" in result

    def test_child_page_title(self):
        block = {
            "type": "child_page", "id": "x", "has_children": False,
            "child_page": {"title": "Subpágina"},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "## Subpágina" in result

    def test_equation_wrapped(self):
        block = {
            "type": "equation", "id": "x", "has_children": False,
            "equation": {"expression": "E = mc^2"},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "$$" in result
        assert "E = mc^2" in result

    def test_bookmark_with_caption(self):
        block = {
            "type": "bookmark", "id": "x", "has_children": False,
            "bookmark": {
                "url": "https://example.com",
                "caption": [{"plain_text": "Example"}],
            },
        }
        result = self.m.extrair_texto_blocos([block])
        assert "[Example](https://example.com)" in result

    def test_bookmark_url_only(self):
        block = {
            "type": "bookmark", "id": "x", "has_children": False,
            "bookmark": {"url": "https://example.com", "caption": []},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "https://example.com" in result

    def test_image_caption(self):
        block = {
            "type": "image", "id": "x", "has_children": False,
            "image": {"caption": [{"plain_text": "Diagrama UML"}]},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "[image: Diagrama UML]" in result

    def test_image_without_caption_skipped(self):
        block = {
            "type": "image", "id": "x", "has_children": False,
            "image": {"caption": []},
        }
        result = self.m.extrair_texto_blocos([block])
        assert result.strip() == ""

    def test_child_database_renders_title(self):
        block = {
            "type": "child_database", "id": "x", "has_children": False,
            "child_database": {"title": "Aulas 2026"},
        }
        result = self.m.extrair_texto_blocos([block])
        assert "[Tabela: Aulas 2026]" in result

    def test_column_list_recurses_to_children(self):
        col_list = {"type": "column_list", "id": "cl", "has_children": True,
                    "column_list": {}}
        column = {"type": "column", "id": "col", "has_children": True, "column": {}}
        paragraph = {
            "type": "paragraph", "id": "p", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "texto numa coluna"}]},
        }
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        calls = {"count": 0}
        def list_children(block_id, **kw):
            calls["count"] += 1
            if block_id == "cl":
                return {"results": [column], "has_more": False, "next_cursor": None}
            if block_id == "col":
                return {"results": [paragraph], "has_more": False, "next_cursor": None}
            return {"results": [], "has_more": False, "next_cursor": None}
        mock_notion.blocks.children.list.side_effect = list_children
        with patch.object(self.m, "notion", mock_notion):
            result = self.m.extrair_texto_blocos([col_list])
        assert "texto numa coluna" in result

    def test_unknown_block_type_skipped_silently(self):
        block = {"type": "weird_future_block", "id": "x", "has_children": False,
                 "weird_future_block": {}}
        result = self.m.extrair_texto_blocos([block])
        assert result == ""

    def test_link_preview_emits_url(self):
        block = {"type": "link_preview", "id": "lp", "has_children": False,
                 "link_preview": {"url": "https://link.example/preview"}}
        result = self.m.extrair_texto_blocos([block])
        assert "https://link.example/preview" in result

    def test_link_to_page_emits_reference(self):
        block = {"type": "link_to_page", "id": "ltp", "has_children": False,
                 "link_to_page": {"type": "page_id",
                                  "page_id": "abc-123"}}
        result = self.m.extrair_texto_blocos([block])
        assert "page_id:abc-123" in result

    def test_breadcrumb_skipped(self):
        block = {"type": "breadcrumb", "id": "b", "has_children": False,
                 "breadcrumb": {}}
        assert self.m.extrair_texto_blocos([block]) == ""

    def test_table_of_contents_skipped(self):
        block = {"type": "table_of_contents", "id": "toc", "has_children": False,
                 "table_of_contents": {}}
        assert self.m.extrair_texto_blocos([block]) == ""

    def test_table_renders_markdown_with_header_separator(self):
        from unittest.mock import patch, MagicMock
        table_block = {"type": "table", "id": "t1", "has_children": True,
                       "table": {}}
        header = {"type": "table_row", "id": "h", "has_children": False,
                  "table_row": {"cells": [[{"plain_text": "Coluna A"}],
                                          [{"plain_text": "Coluna B"}]]}}
        row1 = {"type": "table_row", "id": "r1", "has_children": False,
                "table_row": {"cells": [[{"plain_text": "1"}],
                                        [{"plain_text": "2"}]]}}
        row2 = {"type": "table_row", "id": "r2", "has_children": False,
                "table_row": {"cells": [[{"plain_text": "3"}],
                                        [{"plain_text": "4"}]]}}
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [header, row1, row2],
            "has_more": False, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            result = self.m.extrair_texto_blocos([table_block])
        # Header on first line
        assert "| Coluna A | Coluna B |" in result
        # Separator immediately after
        assert "|---|---|" in result
        # Body rows present
        assert "| 1 | 2 |" in result
        assert "| 3 | 4 |" in result

    def test_depth_limit_truncates_recursion(self):
        # Build a chain deeper than MAX_BLOCK_DEPTH via mocked children.
        from unittest.mock import patch, MagicMock
        deep_value = self.m.MAX_BLOCK_DEPTH + 3

        def make_layer(i):
            return {"type": "toggle", "id": f"lvl-{i}",
                    "has_children": True,
                    "toggle": {"rich_text": [{"plain_text": f"L{i}"}]}}

        mock_notion = MagicMock()
        def list_children(block_id, **kw):
            # extract numeric depth from id
            lvl = int(block_id.split("-")[-1])
            if lvl + 1 < deep_value:
                return {"results": [make_layer(lvl + 1)],
                        "has_more": False, "next_cursor": None}
            # deepest layer: a paragraph that should be cut off by guard
            return {"results": [{"type": "paragraph",
                                 "id": f"leaf-{lvl}", "has_children": False,
                                 "paragraph": {"rich_text": [
                                     {"plain_text": "DEEP_LEAF_VALUE"}]}}],
                    "has_more": False, "next_cursor": None}
        mock_notion.blocks.children.list.side_effect = list_children
        with patch.object(self.m, "notion", mock_notion):
            result = self.m.extrair_texto_blocos([make_layer(0)])
        # Leaf at depth > MAX_BLOCK_DEPTH must be absent.
        assert "DEEP_LEAF_VALUE" not in result
        # Shallow layers still render.
        assert "L0" in result

    def test_cycle_guard_visits_block_once(self):
        # Two top-level entries sharing the same id (simulates a synced_block
        # cycle). Second occurrence must be skipped.
        from unittest.mock import patch, MagicMock
        same_id = "sb-1"
        b1 = {"type": "paragraph", "id": same_id, "has_children": False,
              "paragraph": {"rich_text": [{"plain_text": "OnlyOnce"}]}}
        b2 = {"type": "paragraph", "id": same_id, "has_children": False,
              "paragraph": {"rich_text": [{"plain_text": "OnlyOnce"}]}}
        result = self.m.extrair_texto_blocos([b1, b2])
        # "OnlyOnce" should appear exactly once.
        assert result.count("OnlyOnce") == 1


# ──────────────────────────────────────────────────────────────────────────────
# _notion_retry — backoff on transient errors
# ──────────────────────────────────────────────────────────────────────────────

class TestNotionRetry:
    def setup_method(self):
        self.m = _import_sync()

    def test_succeeds_on_first_try(self, monkeypatch):
        monkeypatch.setattr(self.m, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
        calls = {"n": 0}
        def ok():
            calls["n"] += 1
            return "ok"
        assert self.m._notion_retry(ok) == "ok"
        assert calls["n"] == 1

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(self.m.time, "sleep", lambda s: None)
        calls = {"n": 0}

        class Err429(Exception):
            status = 429

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise Err429("rate_limited")
            return "ok"
        assert self.m._notion_retry(flaky) == "ok"
        assert calls["n"] == 3

    def test_retries_on_5xx(self, monkeypatch):
        monkeypatch.setattr(self.m.time, "sleep", lambda s: None)
        calls = {"n": 0}

        class Err503(Exception):
            status = 503

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise Err503("service_unavailable")
            return "ok"
        assert self.m._notion_retry(flaky) == "ok"

    def test_does_not_retry_on_4xx_client_error(self, monkeypatch):
        monkeypatch.setattr(self.m.time, "sleep", lambda s: None)
        calls = {"n": 0}

        class Err404(Exception):
            status = 404

        def fail():
            calls["n"] += 1
            raise Err404("not_found")
        import pytest
        with pytest.raises(Err404):
            self.m._notion_retry(fail)
        assert calls["n"] == 1

    def test_gives_up_after_max_retries(self, monkeypatch):
        monkeypatch.setattr(self.m.time, "sleep", lambda s: None)
        monkeypatch.setattr(self.m, "NOTION_MAX_RETRIES", 2)

        class Err500(Exception):
            status = 500

        calls = {"n": 0}
        def always_fail():
            calls["n"] += 1
            raise Err500("boom")
        import pytest
        with pytest.raises(Err500):
            self.m._notion_retry(always_fail)
        # 1 initial try + 2 retries
        assert calls["n"] == 3

    def test_honours_retry_after_header(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(self.m.time, "sleep", lambda s: sleeps.append(s))

        class Err429(Exception):
            status = 429
            headers = {"Retry-After": "3.5"}

        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise Err429("slow down")
            return "ok"
        self.m._notion_retry(flaky)
        assert sleeps and sleeps[0] >= 3.5


# ──────────────────────────────────────────────────────────────────────────────
# Cache integration in get_page_content
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPageContentCache:
    def setup_method(self):
        self.m = _import_sync()

    def test_returns_cached_when_last_edited_matches(self, monkeypatch):
        called = {"n": 0}
        def fake_get(pid, le):
            called["n"] += 1
            return "CACHED-TEXT"
        monkeypatch.setattr(self.m.sync_db, "get_cached_page_content", fake_get)
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        with patch.object(self.m, "notion", mock_notion):
            out = self.m.get_page_content("p1", "2026-05-11T10:00:00.000Z")
        assert out == "CACHED-TEXT"
        # Cache hit must short-circuit any API call.
        mock_notion.blocks.children.list.assert_not_called()
        assert called["n"] == 1

    def test_writes_to_cache_after_fetch(self, monkeypatch):
        writes = []
        monkeypatch.setattr(self.m.sync_db, "get_cached_page_content",
                            lambda pid, le: None)
        monkeypatch.setattr(self.m.sync_db, "set_cached_page_content",
                            lambda pid, le, content: writes.append((pid, le, content)))
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [{"type": "paragraph", "id": "p", "has_children": False,
                         "paragraph": {"rich_text": [{"plain_text": "novo"}]}}],
            "has_more": False, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            out = self.m.get_page_content("p1", "2026-05-11T10:00:00.000Z")
        assert out == "novo"
        assert writes == [("p1", "2026-05-11T10:00:00.000Z", "novo")]

    def test_no_cache_when_last_edited_missing(self, monkeypatch):
        # Without page_last_edited_at the cache layer is bypassed entirely.
        called = {"get": 0, "set": 0}
        monkeypatch.setattr(self.m.sync_db, "get_cached_page_content",
                            lambda *a, **k: (called.__setitem__("get", called["get"] + 1) or None))
        monkeypatch.setattr(self.m.sync_db, "set_cached_page_content",
                            lambda *a, **k: called.__setitem__("set", called["set"] + 1))
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [], "has_more": False, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            self.m.get_page_content("p1")
        assert called == {"get": 0, "set": 0}


# ──────────────────────────────────────────────────────────────────────────────
# _list_all_block_children + get_page_content
# ──────────────────────────────────────────────────────────────────────────────

class TestListAllBlockChildren:
    def setup_method(self):
        self.m = _import_sync()

    def test_single_page_no_pagination(self):
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [{"type": "paragraph", "id": "a"}],
            "has_more": False, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            out = self.m._list_all_block_children("page-id")
        assert len(out) == 1
        mock_notion.blocks.children.list.assert_called_once()

    def test_paginates_multiple_pages(self):
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        responses = [
            {"results": [{"id": "a"}, {"id": "b"}], "has_more": True,  "next_cursor": "cur1"},
            {"results": [{"id": "c"}],             "has_more": True,  "next_cursor": "cur2"},
            {"results": [{"id": "d"}],             "has_more": False, "next_cursor": None},
        ]
        mock_notion.blocks.children.list.side_effect = responses
        with patch.object(self.m, "notion", mock_notion):
            out = self.m._list_all_block_children("page-id")
        assert [b["id"] for b in out] == ["a", "b", "c", "d"]
        assert mock_notion.blocks.children.list.call_count == 3
        # second/third calls must pass start_cursor
        second_kwargs = mock_notion.blocks.children.list.call_args_list[1].kwargs
        assert second_kwargs.get("start_cursor") == "cur1"

    def test_has_more_true_but_null_cursor_breaks_loop(self):
        # Defensive: guard against infinite loop if Notion API returns
        # has_more=True but no next_cursor.
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.return_value = {
            "results": [{"id": "a"}], "has_more": True, "next_cursor": None,
        }
        with patch.object(self.m, "notion", mock_notion):
            out = self.m._list_all_block_children("page-id")
        assert out == [{"id": "a"}]
        # Exactly one call — we did NOT loop forever.
        assert mock_notion.blocks.children.list.call_count == 1


class TestGetPageContent:
    def setup_method(self):
        self.m = _import_sync()

    def test_paginated_blocks_concatenated(self):
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        page1 = [{
            "type": "paragraph", "id": "p1", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "primeira página"}]},
        }]
        page2 = [{
            "type": "paragraph", "id": "p2", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "segunda página"}]},
        }]
        mock_notion.blocks.children.list.side_effect = [
            {"results": page1, "has_more": True,  "next_cursor": "cur"},
            {"results": page2, "has_more": False, "next_cursor": None},
        ]
        with patch.object(self.m, "notion", mock_notion):
            out = self.m.get_page_content("page-id")
        assert "primeira página" in out
        assert "segunda página" in out

    def test_error_returns_empty_string(self):
        from unittest.mock import patch, MagicMock
        mock_notion = MagicMock()
        mock_notion.blocks.children.list.side_effect = Exception("boom")
        with patch.object(self.m, "notion", mock_notion):
            out = self.m.get_page_content("page-id")
        assert out == ""

    def test_recurses_into_nested_children(self):
        from unittest.mock import patch, MagicMock
        parent = {
            "type": "toggle", "id": "tog", "has_children": True,
            "toggle": {"rich_text": [{"plain_text": "Pergunta?"}]},
        }
        child = {
            "type": "paragraph", "id": "ans", "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": "Resposta nested"}]},
        }
        mock_notion = MagicMock()
        def list_children(block_id, **kw):
            if block_id == "page-id":
                return {"results": [parent], "has_more": False, "next_cursor": None}
            if block_id == "tog":
                return {"results": [child],  "has_more": False, "next_cursor": None}
            return {"results": [], "has_more": False, "next_cursor": None}
        mock_notion.blocks.children.list.side_effect = list_children
        with patch.object(self.m, "notion", mock_notion):
            out = self.m.get_page_content("page-id")
        assert "Pergunta?" in out
        assert "Resposta nested" in out


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
