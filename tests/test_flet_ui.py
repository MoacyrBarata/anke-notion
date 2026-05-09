"""
Unit tests for Flet UI component factory functions in app_flet.py.
Each test constructs a widget and asserts structural properties —
no running server or FakePage required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app_flet


# ── _ball / _bonly ─────────────────────────────────────────────────────────────

class TestBall:
    def test_returns_border(self):
        result = app_flet._ball(2, "#aabbcc")
        assert isinstance(result, ft.Border)

    def test_all_sides_have_same_width(self):
        b = app_flet._ball(3, "#ffffff")
        assert b.top.width == 3
        assert b.right.width == 3
        assert b.bottom.width == 3
        assert b.left.width == 3

    def test_all_sides_have_same_color(self):
        b = app_flet._ball(1, "#001122")
        assert b.top.color == "#001122"
        assert b.left.color == "#001122"

    def test_zero_width(self):
        b = app_flet._ball(0, "#000000")
        assert b.top.width == 0


class TestBonly:
    def test_returns_border(self):
        result = app_flet._bonly(top=(1, "#aabbcc"))
        assert isinstance(result, ft.Border)

    def test_specified_sides(self):
        b = app_flet._bonly(top=(2, "#111111"), bottom=(4, "#222222"))
        assert b.top.width == 2.0
        assert b.top.color == "#111111"
        assert b.bottom.width == 4.0
        assert b.bottom.color == "#222222"

    def test_unspecified_side_is_default_borderside(self):
        b = app_flet._bonly(top=(1, "#fff"))
        assert isinstance(b.left, ft.BorderSide)


# ── glass ──────────────────────────────────────────────────────────────────────

class TestGlass:
    def test_returns_container(self):
        assert isinstance(app_flet.glass(ft.Text("x")), ft.Container)

    def test_has_glass_bgcolor(self):
        c = app_flet.glass(ft.Text("x"))
        assert c.bgcolor == app_flet.C_GLASS

    def test_has_border(self):
        c = app_flet.glass(ft.Text("x"))
        assert isinstance(c.border, ft.Border)

    def test_has_border_radius(self):
        c = app_flet.glass(ft.Text("x"))
        assert c.border_radius == 18

    def test_expand_false_by_default(self):
        c = app_flet.glass(ft.Text("x"))
        assert not c.expand

    def test_expand_true(self):
        c = app_flet.glass(ft.Text("x"), expand=True)
        assert c.expand

    def test_custom_padding(self):
        c = app_flet.glass(ft.Text("x"), padding=8)
        assert c.padding == 8

    def test_has_shadow(self):
        c = app_flet.glass(ft.Text("x"))
        assert c.shadow is not None

    def test_content_is_stored(self):
        inner = ft.Text("hello")
        c = app_flet.glass(inner)
        assert c.content is inner


# ── h ─────────────────────────────────────────────────────────────────────────

class TestH:
    def test_returns_text(self):
        assert isinstance(app_flet.h("Title"), ft.Text)

    def test_value(self):
        t = app_flet.h("My Title")
        assert t.value == "My Title"

    def test_default_size(self):
        t = app_flet.h("X")
        assert t.size == 18

    def test_custom_size(self):
        t = app_flet.h("X", size=24)
        assert t.size == 24

    def test_default_color(self):
        t = app_flet.h("X")
        assert t.color == app_flet.C_TEXT

    def test_custom_color(self):
        t = app_flet.h("X", color="#ff0000")
        assert t.color == "#ff0000"


# ── dim ───────────────────────────────────────────────────────────────────────

class TestDim:
    def test_returns_text(self):
        assert isinstance(app_flet.dim("hint"), ft.Text)

    def test_value(self):
        t = app_flet.dim("some hint")
        assert t.value == "some hint"

    def test_default_color(self):
        t = app_flet.dim("x")
        assert t.color == app_flet.C_DIM

    def test_default_size(self):
        t = app_flet.dim("x")
        assert t.size == 13

    def test_custom_size(self):
        t = app_flet.dim("x", size=10)
        assert t.size == 10


# ── badge ─────────────────────────────────────────────────────────────────────

class TestBadge:
    def test_returns_container(self):
        assert isinstance(app_flet.badge("Notion", True, "OK"), ft.Container)

    def test_ok_bgcolor_contains_success_color(self):
        b = app_flet.badge("Notion", True, "OK")
        assert app_flet.C_SUCCESS in b.bgcolor

    def test_fail_bgcolor_contains_error_color(self):
        b = app_flet.badge("Notion", False, "Err")
        assert app_flet.C_ERROR in b.bgcolor

    def test_expand(self):
        b = app_flet.badge("X", True, "y")
        assert b.expand

    def test_has_border(self):
        b = app_flet.badge("X", True, "y")
        assert isinstance(b.border, ft.Border)


# ── btn ───────────────────────────────────────────────────────────────────────

class TestBtn:
    def test_returns_elevated_button(self):
        assert isinstance(app_flet.btn("Save", on_click=lambda e: None), ft.ElevatedButton)

    def test_content_is_text(self):
        b = app_flet.btn("Save", on_click=lambda e: None)
        assert b.content == "Save"

    def test_has_on_click(self):
        cb = lambda e: None
        b = app_flet.btn("X", on_click=cb)
        assert b.on_click is cb

    def test_icon_kwarg(self):
        b = app_flet.btn("X", on_click=lambda e: None, icon=ft.Icons.SAVE_ROUNDED)
        assert b.icon == ft.Icons.SAVE_ROUNDED

    def test_custom_width(self):
        b = app_flet.btn("X", on_click=lambda e: None, width=200)
        assert b.width == 200

    def test_has_style(self):
        b = app_flet.btn("X", on_click=lambda e: None)
        assert b.style is not None


# ── ghost_btn ─────────────────────────────────────────────────────────────────

class TestGhostBtn:
    def test_returns_outlined_button(self):
        assert isinstance(app_flet.ghost_btn("Cancel", on_click=lambda e: None), ft.OutlinedButton)

    def test_content_is_text(self):
        b = app_flet.ghost_btn("Cancel", on_click=lambda e: None)
        assert b.content == "Cancel"

    def test_has_on_click(self):
        cb = lambda e: None
        b = app_flet.ghost_btn("X", on_click=cb)
        assert b.on_click is cb

    def test_has_style(self):
        b = app_flet.ghost_btn("X", on_click=lambda e: None)
        assert b.style is not None


# ── field ─────────────────────────────────────────────────────────────────────

class TestField:
    def test_returns_textfield(self):
        assert isinstance(app_flet.field("Token"), ft.TextField)

    def test_label(self):
        f = app_flet.field("My Label")
        assert f.label == "My Label"

    def test_value(self):
        f = app_flet.field("X", value="hello")
        assert f.value == "hello"

    def test_empty_value_default(self):
        f = app_flet.field("X")
        assert f.value == ""

    def test_password_false_default(self):
        f = app_flet.field("X")
        assert not f.password

    def test_password_true(self):
        f = app_flet.field("X", password=True)
        assert f.password
        assert f.can_reveal_password

    def test_hint_text(self):
        f = app_flet.field("X", hint="Enter token...")
        assert f.hint_text == "Enter token..."

    def test_custom_width(self):
        f = app_flet.field("X", width=300)
        assert f.width == 300

    def test_accent_focused_border(self):
        f = app_flet.field("X")
        assert f.focused_border_color == app_flet.C_ACCENT


# ── dropdown ──────────────────────────────────────────────────────────────────

class TestDropdown:
    def test_returns_dropdown(self):
        assert isinstance(app_flet.dropdown("Model", ["a"]), ft.Dropdown)

    def test_label(self):
        d = app_flet.dropdown("My Label", ["x"])
        assert d.label == "My Label"

    def test_options_count(self):
        d = app_flet.dropdown("M", ["a", "b", "c"])
        assert len(d.options) == 3

    def test_default_value_is_first(self):
        d = app_flet.dropdown("M", ["alpha", "beta"])
        assert d.value == "alpha"

    def test_explicit_value(self):
        d = app_flet.dropdown("M", ["a", "b", "c"], value="b")
        assert d.value == "b"

    def test_invalid_value_falls_back_to_first(self):
        d = app_flet.dropdown("M", ["x", "y"], value="z")
        assert d.value == "x"

    def test_empty_options_value_is_none(self):
        d = app_flet.dropdown("M", [])
        assert d.value is None

    def test_empty_options_no_error(self):
        d = app_flet.dropdown("M", [])
        assert len(d.options) == 0

    def test_accent_focused_border(self):
        d = app_flet.dropdown("M", ["a"])
        assert d.focused_border_color == app_flet.C_ACCENT
