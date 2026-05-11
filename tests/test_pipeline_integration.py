"""Pipeline integration tests for processar_plano / processar_hierarquico.

These exercise the full data flow with Notion, Anki, and AI calls mocked.
They cover behaviors the pure-function tests cannot:
- end-to-end loop iteration
- per-table max_cards override
- cache-aware get_page_content invocation
- status/sync filtering
- skipping pages without content
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AI_PROVIDER", "claude")
    monkeypatch.setenv("MAX_FLASHCARDS_POR_AULA", "5")


@pytest.fixture()
def sync_mod(monkeypatch, tmp_path):
    import db as sync_db_module
    monkeypatch.setattr(sync_db_module, "DB_FILE", tmp_path / "h.db")
    sync_db_module.init_db()

    with patch("anthropic.Anthropic"), patch("notion_client.Client"):
        import importlib
        import notion_anki_sync as m
        importlib.reload(m)
        # Patch out time.sleep so tests don't actually sleep between items.
        monkeypatch.setattr(m.time, "sleep", lambda s: None)
        return m


def _row(page_id: str, title: str, edited: str = "2026-05-11T10:00:00.000Z",
         extra_props: dict | None = None) -> dict:
    props = {"Aula": {"type": "title",
                      "title": [{"plain_text": title}]}}
    props.update(extra_props or {})
    return {"id": page_id, "last_edited_time": edited, "properties": props}


def _block(text: str, bid: str = "b1") -> dict:
    return {"type": "paragraph", "id": bid, "has_children": False,
            "paragraph": {"rich_text": [{"plain_text": text}]}}


class TestProcessarPlanoIntegration:
    def test_happy_path_generates_and_inserts(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("page-1", "Aula 1"), _row("page-2", "Aula 2")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children",
                            lambda block_id: [_block(f"texto {block_id}")])

        anki_calls = []
        monkeypatch.setattr(m, "garantir_deck", lambda d: anki_calls.append(("deck", d)))
        monkeypatch.setattr(m, "adicionar_nota",
                            lambda deck, card, cat, t, d: anki_calls.append(("note", t, card)) or True)
        monkeypatch.setattr(m, "marcar_sincronizado", MagicMock())

        ai_calls = []
        def fake_ia(cat, titulo, data, conteudo, max_cards=10):
            ai_calls.append({"titulo": titulo, "max_cards": max_cards,
                             "conteudo": conteudo})
            return [{"front": f"Q-{titulo}", "back": f"A-{titulo}"}]
        monkeypatch.setattr(m, "gerar_flashcards", fake_ia)

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "Matemática"}],
            "parent_name_prop": "Aula",
            "child_db_keyword": "",
            "child_title_prop": "Aula",
            "child_content_prop": None,
            "child_date_prop": None,
            "use_sync_field": False,
            "child_sync_prop": None,
            "child_status_prop": None,
            "anki_deck_root": "Estudo",
        }
        total = m.processar_plano(cfg)
        assert total["itens"] == 2
        assert total["gerados"] == 2
        assert total["enviados"] == 2
        assert any(c[0] == "deck" and c[1] == "Estudo::Matemática" for c in anki_calls)
        assert {c["titulo"] for c in ai_calls} == {"Aula 1", "Aula 2"}

    def test_skips_pages_without_content(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("page-empty", "Vazia")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children", lambda bid: [])
        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "gerar_flashcards", MagicMock())

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "M"}],
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": False,
        }
        total = m.processar_plano(cfg)
        assert total["itens"] == 0
        m.gerar_flashcards.assert_not_called()

    def test_per_table_max_cards_overrides_global(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("p1", "Aula X")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children",
                            lambda bid: [_block("conteudo")])
        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "adicionar_nota", lambda *a, **k: True)

        captured = {}
        def fake_ia(cat, titulo, data, conteudo, max_cards=10):
            captured["max_cards"] = max_cards
            return []
        monkeypatch.setattr(m, "gerar_flashcards", fake_ia)

        # Per-DB override: this table gets 25 cards instead of the global 5.
        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "Mat",
                              "props": {"max_cards": 25}}],
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": False,
            "max_cards": 5,  # global config
        }
        m.processar_plano(cfg)
        assert captured["max_cards"] == 25

    def test_per_table_falls_back_to_global(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("p1", "Aula X")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children",
                            lambda bid: [_block("conteudo")])
        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "adicionar_nota", lambda *a, **k: True)

        captured = {}
        def fake_ia(cat, titulo, data, conteudo, max_cards=10):
            captured["max_cards"] = max_cards
            return []
        monkeypatch.setattr(m, "gerar_flashcards", fake_ia)

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "Mat"}],  # no per-DB props
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": False,
        }
        m.processar_plano(cfg)
        # Falls back to module default MAX_FLASHCARDS_POR_AULA (5 from env).
        assert captured["max_cards"] == m.MAX_FLASHCARDS_POR_AULA

    def test_passes_last_edited_to_cache_layer(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("page-1", "Aula", edited="2026-05-11T10:00:00.000Z")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)

        seen = {}
        def fake_get_content(page_id, page_last_edited_at=None):
            seen["pid"] = page_id
            seen["edited"] = page_last_edited_at
            return "texto"
        monkeypatch.setattr(m, "get_page_content", fake_get_content)

        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "adicionar_nota", lambda *a, **k: True)
        monkeypatch.setattr(m, "gerar_flashcards", lambda *a, **k: [])

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "M"}],
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": False,
        }
        m.processar_plano(cfg)
        assert seen["pid"] == "page-1"
        assert seen["edited"] == "2026-05-11T10:00:00.000Z"

    def test_marks_sync_field_when_enabled(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("p1", "Aula")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children",
                            lambda bid: [_block("c")])
        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "adicionar_nota", lambda *a, **k: True)
        monkeypatch.setattr(m, "gerar_flashcards",
                            lambda *a, **k: [{"front": "Q", "back": "A"}])

        calls = []
        monkeypatch.setattr(m, "marcar_sincronizado",
                            lambda pid, prop, val: calls.append((pid, prop, val)))

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "M"}],
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": True,
            "child_sync_prop": "Sincronização",
            "child_sync_done": "✅ Sincronizado",
        }
        m.processar_plano(cfg)
        assert calls == [("p1", "Sincronização", "✅ Sincronizado")]

    def test_marks_error_when_ia_returns_no_cards(self, sync_mod, monkeypatch):
        m = sync_mod
        rows = [_row("p1", "Aula")]
        monkeypatch.setattr(m, "query_database_all", lambda *a, **k: rows)
        monkeypatch.setattr(m, "_list_all_block_children",
                            lambda bid: [_block("c")])
        monkeypatch.setattr(m, "garantir_deck", MagicMock())
        monkeypatch.setattr(m, "adicionar_nota", lambda *a, **k: True)
        monkeypatch.setattr(m, "gerar_flashcards", lambda *a, **k: [])

        calls = []
        monkeypatch.setattr(m, "marcar_sincronizado",
                            lambda pid, prop, val: calls.append((pid, prop, val)))

        cfg = {
            "selected_dbs": [{"id": "ds-1", "name": "M"}],
            "parent_name_prop": "Aula", "child_title_prop": "Aula",
            "use_sync_field": True,
            "child_sync_prop": "Sync",
        }
        total = m.processar_plano(cfg)
        assert total["erros"] == 1
        # Marca como erro pra ficar visível no Notion.
        assert calls == [("p1", "Sync", "❌ Erro")]
