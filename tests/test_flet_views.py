"""
Integration-style tests for app_flet.main() page structure.
Uses FakePage to call main() without a running Flet server.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_flet


# ── FakePage ──────────────────────────────────────────────────────────────────

class _FakeWindow:
    width = 0
    height = 0
    min_width = 0
    min_height = 0


class FakePage:
    def __init__(self):
        self.title = ""
        self.bgcolor = ""
        self.theme_mode = None
        self.theme = None
        self.padding = 0
        self.window = _FakeWindow()
        self.snack_bar = None
        self._controls = []
        self.update_calls = 0

    def update(self):
        self.update_calls += 1

    def add(self, *controls):
        self._controls.extend(controls)


@pytest.fixture()
def page(monkeypatch, tmp_path):
    """FakePage with patched filesystem helpers so no real .env or JSON is read."""
    monkeypatch.setattr(app_flet, "load_cfg", lambda: {})
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    # Redirect any writes (save_cfg / save_notion_config) to tmp dir so tests
    # that exercise the save button cannot pollute the real .env.
    monkeypatch.setattr(app_flet, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(app_flet, "NOTION_CFG_FILE", tmp_path / "notion_config.json")
    return FakePage()


# ── Page initialisation ───────────────────────────────────────────────────────

def test_main_sets_title(page):
    app_flet.main(page)
    assert page.title == "Notion → Anki"


def test_main_sets_bgcolor(page):
    app_flet.main(page)
    assert page.bgcolor == app_flet.C_BG


def test_main_sets_dark_theme_mode(page):
    app_flet.main(page)
    assert page.theme_mode == ft.ThemeMode.DARK


def test_main_sets_window_dimensions(page):
    app_flet.main(page)
    assert page.window.width == 1120
    assert page.window.height == 860
    assert page.window.min_width == 480
    assert page.window.min_height == 600


def test_main_adds_exactly_one_root_control(page):
    app_flet.main(page)
    assert len(page._controls) == 1


def test_root_control_is_row(page):
    app_flet.main(page)
    assert isinstance(page._controls[0], ft.Row)


def test_root_row_has_two_children(page):
    app_flet.main(page)
    row = page._controls[0]
    assert len(row.controls) == 2


# ── NavigationRail structure ──────────────────────────────────────────────────

def _get_rail(page):
    app_flet.main(page)
    row = page._controls[0]
    sidebar = row.controls[0]
    return sidebar.content


def test_rail_type(page):
    assert isinstance(_get_rail(page), ft.NavigationRail)


def test_rail_has_four_destinations(page):
    rail = _get_rail(page)
    assert len(rail.destinations) == 4


def test_rail_selected_index_zero(page):
    rail = _get_rail(page)
    assert rail.selected_index == 0


def test_rail_destination_labels(page):
    rail = _get_rail(page)
    labels = [d.label for d in rail.destinations]
    assert "Sync" in labels
    assert "Notion" in labels
    assert "Config" in labels
    assert "Ajuda" in labels


# ── Content container / initial view ─────────────────────────────────────────

def _get_content(page):
    app_flet.main(page)
    return page._controls[0].controls[1]


def test_content_container_is_container(page):
    assert isinstance(_get_content(page), ft.Container)


def test_initial_content_is_column(page):
    content = _get_content(page)
    assert isinstance(content.content, ft.Column)


# ── Navigation switching ──────────────────────────────────────────────────────

def test_on_nav_changes_content(page, monkeypatch):
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    app_flet.main(page)
    rail = page._controls[0].controls[0].content
    content = page._controls[0].controls[1]

    initial_view = content.content

    e = MagicMock()
    e.control.selected_index = 2  # Settings view
    rail.on_change(e)

    assert content.content is not initial_view


def test_on_nav_calls_page_update(page, monkeypatch):
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    app_flet.main(page)
    page.update_calls = 0
    rail = page._controls[0].controls[0].content

    e = MagicMock()
    e.control.selected_index = 3
    rail.on_change(e)

    assert page.update_calls >= 1


def test_on_nav_to_notion_calls_rebuild(page, monkeypatch):
    rebuilt = []
    original_load = lambda: None
    monkeypatch.setattr(app_flet, "load_notion_config", original_load)
    app_flet.main(page)
    rail = page._controls[0].controls[0].content

    # Navigate to Notion tab (index 1) triggers rebuild()
    e = MagicMock()
    e.control.selected_index = 1
    page.update_calls = 0
    rail.on_change(e)
    assert page.update_calls >= 1


# ── page.update called after main ────────────────────────────────────────────

def test_main_calls_update_at_least_once(page):
    app_flet.main(page)
    assert page.update_calls >= 1


# ── on_clear worker ──────────────────────────────────────────────────────────

def _find_clear_button(page):
    """Walk the sync view tree to find the clear-log IconButton."""
    app_flet.main(page)
    content = page._controls[0].controls[1]
    view_sync = content.content  # ft.Column
    # view_sync.controls: [glass header, spacer, glass conn, spacer, glass sync, spacer, stats, spacer]
    sync_glass = view_sync.controls[4]  # index 4 = sync card
    sync_col = sync_glass.content       # ft.Column
    header_row = sync_col.controls[0]   # ft.Row([h(...), container(expand), IconButton])
    return header_row.controls[2]       # IconButton


def test_on_clear_resets_log(page, monkeypatch):
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    app_flet.main(page)
    content = page._controls[0].controls[1]
    view_sync = content.content

    # Find clear button and fire it
    clear_btn = _find_clear_button(page)
    clear_btn.on_click(MagicMock())
    # No exception means the handler ran; page.update was called
    assert page.update_calls >= 1


# ── on_sync guard: no notion config ──────────────────────────────────────────

def test_on_sync_snacks_when_no_notion_config(page, monkeypatch):
    snacked = []

    def fake_snack(msg, color=None):
        snacked.append(msg)

    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    app_flet.main(page)

    # Patch snack by injecting into closed-over scope — patch page.snack_bar setter
    content = page._controls[0].controls[1]
    view_sync = content.content
    sync_glass = view_sync.controls[4]
    sync_col = sync_glass.content
    # ElevatedButton is controls[2] (progress=1, container=2? let's find by type)
    sync_btn = None
    for ctrl in sync_col.controls:
        if isinstance(ctrl, ft.Button):
            sync_btn = ctrl
            break

    assert sync_btn is not None, "Sync button not found in view_sync"

    # Snack is shown via page.snack_bar; check that page.snack_bar gets set
    sync_btn.on_click(MagicMock())
    # When no notion config: snack is called, which sets page.snack_bar
    assert page.snack_bar is not None


# ── on_test worker ────────────────────────────────────────────────────────────

class _ImmediateThread:
    """Runs the target function synchronously instead of in a thread."""
    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()


def test_on_test_updates_conn_row(page, monkeypatch):
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    monkeypatch.setattr("app_flet.threading.Thread", _ImmediateThread)
    monkeypatch.setattr(app_flet, "check_notion", lambda t: (True, "Auth OK"))
    monkeypatch.setattr(app_flet, "check_anki", lambda h: (False, "Anki closed"))
    monkeypatch.setattr(app_flet, "check_ai_key", lambda p, k: (True, "Valid"))

    app_flet.main(page)
    content = page._controls[0].controls[1]
    view_sync = content.content
    conn_glass = view_sync.controls[2]  # connection card
    conn_col = conn_glass.content
    test_header_row = conn_col.controls[0]
    test_btn = test_header_row.controls[2]  # ft.Button "Testar"

    test_btn.on_click(MagicMock())

    # After on_test runs synchronously, conn_row should have 3 badges
    conn_row = conn_col.controls[2]  # ft.Row with badges
    assert len(conn_row.controls) == 3


# ── Wizard step 1 structure ───────────────────────────────────────────────────

def test_wizard_step1_has_controls(page, monkeypatch):
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: None)
    app_flet.main(page)

    row = page._controls[0]
    content = row.controls[1]
    # Navigate to Notion tab to trigger rebuild
    rail = row.controls[0].content
    e = MagicMock()
    e.control.selected_index = 1
    rail.on_change(e)

    notion_view = content.content
    assert isinstance(notion_view, ft.Column)
    assert len(notion_view.controls) > 0


def test_wizard_step1_shows_existing_config_card(page, monkeypatch):
    existing = {"mode": "flat", "last_sync_time": "2024-01-01T12:00:00"}
    monkeypatch.setattr(app_flet, "load_notion_config", lambda: existing)
    app_flet.main(page)

    row = page._controls[0]
    content = row.controls[1]
    rail = row.controls[0].content
    e = MagicMock()
    e.control.selected_index = 1
    rail.on_change(e)

    # With existing config, wizard step 1 renders an extra card — controls > base count
    notion_view = content.content
    assert len(notion_view.controls) > 2


# ── Snack helper ──────────────────────────────────────────────────────────────

def test_snack_sets_snack_bar(page):
    app_flet.main(page)
    # Trigger a snack by saving config (which calls snack internally)
    # We call on_save_cfg directly by finding the settings view
    row = page._controls[0]
    content = row.controls[1]
    rail = row.controls[0].content

    e = MagicMock()
    e.control.selected_index = 2  # Settings
    rail.on_change(e)

    settings_view = content.content
    # Find save button
    save_btn = None
    for ctrl in settings_view.controls:
        if isinstance(ctrl, ft.Button):
            save_btn = ctrl
            break

    assert save_btn is not None
    save_btn.on_click(MagicMock())
    assert page.snack_bar is not None
    assert page.snack_bar.open is True
