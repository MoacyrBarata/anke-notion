"""
ui_components.py — Color palette, border helpers, and widget factory functions.
All functions are pure (no state, no page references) and safe to import anywhere.
"""
import flet as ft

# ── Palette (blue-shifted) ─────────────────────────────────────────────────────
C_BG        = "#070d1a"
C_GLASS     = "#ffffff,0.051"
C_GLASS_HVR = "#ffffff,0.094"
C_BORDER    = "#ffffff,0.094"
C_ACCENT    = "#3b82f6"
C_ACCENT2   = "#38bdf8"
C_SUCCESS   = "#34d399"
C_WARNING   = "#e98a34"
C_ERROR     = "#f56691"
C_TEXT      = "#eaf0ff"
C_DIM       = "#8aa0c8"
C_MUTED     = "#465879"


# ── Border helpers ─────────────────────────────────────────────────────────────

def _ball(width: float, color: str) -> ft.Border:
    s = ft.BorderSide(width, color)
    return ft.Border(top=s, right=s, bottom=s, left=s)


def _bonly(**sides) -> ft.Border:
    return ft.Border(**{k: ft.BorderSide(v[0], v[1]) for k, v in sides.items()})


# ── Widget factories ───────────────────────────────────────────────────────────

def glass(content, padding=20, expand=False, margin=None, height=None):
    return ft.Container(
        content=content,
        bgcolor=C_GLASS,
        border=_ball(1, C_BORDER),
        border_radius=18,
        padding=padding,
        expand=expand,
        margin=margin,
        height=height,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=28,
            color="#000000,0.157",
            offset=ft.Offset(0, 6),
        ),
    )


def h(text, size=18, color=C_TEXT, weight=ft.FontWeight.W_600):
    return ft.Text(text, size=size, color=color, weight=weight)


def dim(text, size=13, color=C_DIM):
    return ft.Text(text, size=size, color=color)


def badge(label, ok, msg):
    c  = C_SUCCESS if ok else C_ERROR
    bg = f"{C_SUCCESS},0.1" if ok else f"{C_ERROR},0.1"
    ic = ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED if ok else ft.Icons.CANCEL_OUTLINED
    return ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ic, color=c, size=15),
                    ft.Text(label, color=c, size=13, weight=ft.FontWeight.W_600)], spacing=5),
            ft.Text(msg, color=C_DIM, size=11),
        ], spacing=3),
        bgcolor=bg,
        border=_ball(1, f"{c},0.2"),
        border_radius=12,
        padding=14,
        expand=True,
    )


def btn(text, on_click, icon=None, color=C_ACCENT, width=None, ref=None):
    return ft.Button(
        text, icon=icon, on_click=on_click, width=width, ref=ref,
        style=ft.ButtonStyle(
            bgcolor=color,
            color=C_TEXT,
            shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.padding.Padding(left=22, right=22, top=13, bottom=13),
            elevation=0,
            overlay_color="#ffffff,0.094",
        ),
    )


def ghost_btn(text, on_click, icon=None):
    return ft.OutlinedButton(
        text, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(
            color=C_DIM,
            side=ft.BorderSide(1, C_BORDER),
            shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.padding.Padding(left=18, right=18, top=13, bottom=13),
            overlay_color="#ffffff,0.051",
        ),
    )


def field(label, value="", password=False, hint="", width=None):
    return ft.TextField(
        label=label, value=value, password=password,
        can_reveal_password=password, hint_text=hint, width=width,
        label_style=ft.TextStyle(color=C_DIM, size=12),
        text_style=ft.TextStyle(color=C_TEXT, size=14),
        hint_style=ft.TextStyle(color=C_MUTED, size=12),
        bgcolor=C_GLASS,
        border_color=C_BORDER,
        focused_border_color=C_ACCENT,
        border_radius=12,
        cursor_color=C_ACCENT,
        content_padding=ft.padding.Padding(left=14, right=14, top=12, bottom=12),
    )


def dropdown(label, options, value=None):
    """`options` aceita strings ou tuplas (key, text). Quando tupla, key vira
    o valor armazenado e text vira o que aparece na lista."""
    keys = []
    opts = []
    for o in options:
        if isinstance(o, tuple):
            k, t = o[0], o[1]
        else:
            k, t = o, o
        keys.append(k)
        opts.append(ft.dropdown.Option(key=k, text=t))
    val = value if value in keys else (keys[0] if keys else None)
    return ft.Dropdown(
        label=label, options=opts, value=val,
        bgcolor="#0e1a32",
        border_color=C_BORDER,
        focused_border_color=C_ACCENT,
        border_radius=12,
        color=C_TEXT,
        label_style=ft.TextStyle(color=C_DIM, size=12),
        text_style=ft.TextStyle(color=C_TEXT, size=14),
    )


def hint(text):
    return ft.Container(
        content=ft.Text(text, size=11, color=C_MUTED, italic=True),
        padding=ft.padding.Padding(left=4, right=0, top=2, bottom=4),
    )


def field_with_hint(control, hint_text):
    return ft.Column([control, hint(hint_text)], spacing=0, tight=True)
