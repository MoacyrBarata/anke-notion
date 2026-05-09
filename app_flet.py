#!/usr/bin/env python3
"""
app_flet.py — Interface Notion → Anki (Flet / Liquid Glass)
Rodar: python app_flet.py
"""

import os
import sys
import json
import threading
import subprocess
import requests
import flet as ft
from pathlib import Path
from dotenv import dotenv_values, set_key

try:
    from notion_client import Client as NotionClient
    NOTION_CLIENT_AVAILABLE = True
except ImportError:
    NOTION_CLIENT_AVAILABLE = False

ROOT            = Path(__file__).parent
ENV_FILE        = ROOT / ".env"
NOTION_CFG_FILE = ROOT / "notion_config.json"

# ── Palette ────────────────────────────────────────────────────────────────────
C_BG        = "#090915"
C_GLASS     = "#ffffff0d"
C_GLASS_HVR = "#ffffff18"
C_BORDER    = "#ffffff18"
C_ACCENT    = "#7c6af7"
C_ACCENT2   = "#a78bfa"
C_CYAN      = "#22d3ee"
C_SUCCESS   = "#34d399"
C_WARNING   = "#fbbf24"
C_ERROR     = "#f87171"
C_TEXT      = "#eeeeff"
C_DIM       = "#9090b8"
C_MUTED     = "#505070"


# ── Data helpers ───────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    return dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}


def save_cfg(updates: dict):
    ENV_FILE.touch(exist_ok=True)
    for k, v in updates.items():
        set_key(str(ENV_FILE), k, str(v) if v is not None else "")


def load_notion_config():
    if NOTION_CFG_FILE.exists():
        with open(NOTION_CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_notion_config(data: dict):
    with open(NOTION_CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_notion(token: str):
    if not token:
        return False, "Token não informado"
    try:
        r = requests.get(
            "https://api.notion.com/v1/users/me",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"},
            timeout=5,
        )
        if r.status_code == 200:
            name = r.json().get("name", "")
            return True, f"Autenticado{' — ' + name if name else ''}"
        return False, f"HTTP {r.status_code}"
    except Exception:
        return False, "Sem conexão"


def check_anki(host: str):
    if not host:
        return False, "Host não informado"
    try:
        r = requests.post(host, json={"action": "version", "version": 6}, timeout=3)
        data = r.json()
        if data.get("error"):
            return False, data["error"]
        return True, f"AnkiConnect v{data.get('result', '?')}"
    except Exception:
        return False, "Anki fechado ou AnkiConnect ausente"


def check_ai_key(provider: str, key: str):
    if not key:
        return False, "Chave não informada"
    if provider == "claude":
        ok = key.startswith("sk-ant-")
        return ok, "Formato válido" if ok else "Esperado: sk-ant-..."
    ok = key.startswith("AIza") and len(key) > 20
    return ok, "Formato válido" if ok else "Esperado: AIza..."


def list_notion_databases(token: str) -> list:
    if not NOTION_CLIENT_AVAILABLE or not token:
        return []
    try:
        client = NotionClient(auth=token)
        results, cursor = [], None
        while True:
            kwargs = {"filter": {"property": "object", "value": "database"}}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.search(**kwargs)
            results.extend(resp["results"])
            if not resp["has_more"]:
                break
            cursor = resp["next_cursor"]
        return results
    except Exception:
        return []


def get_database_properties(token: str, db_id: str) -> dict:
    if not NOTION_CLIENT_AVAILABLE or not token:
        return {}
    try:
        client = NotionClient(auth=token)
        return client.databases.retrieve(db_id).get("properties", {})
    except Exception:
        return {}


def get_db_title(db: dict) -> str:
    try:
        return db["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return db.get("id", "—")[:8]


def props_by_type(props: dict, *types: str) -> list:
    return [k for k, v in props.items() if v["type"] in types]


def run_sync(env_override: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({k: v for k, v in env_override.items() if v})
    return subprocess.Popen(
        [sys.executable, str(ROOT / "notion_anki_sync.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(ROOT), env=env,
    )


def parse_stats(lines: list) -> dict:
    stats = {"disciplinas": 0, "itens": 0, "gerados": 0, "enviados": 0, "erros": 0}
    mapping = {
        "Categorias processadas": "disciplinas",
        "Itens processados":      "itens",
        "Flashcards gerados":     "gerados",
        "Flashcards no Anki":     "enviados",
        "Erros":                  "erros",
    }
    for line in lines:
        for label, key in mapping.items():
            if label in line:
                try:
                    stats[key] = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
    return stats


# ── UI Components ──────────────────────────────────────────────────────────────

def glass(content, padding=20, expand=False, margin=None, height=None):
    return ft.Container(
        content=content,
        bgcolor=C_GLASS,
        border=ft.border.all(1, C_BORDER),
        border_radius=18,
        padding=padding,
        expand=expand,
        margin=margin,
        height=height,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=28,
            color="#00000028",
            offset=ft.Offset(0, 6),
        ),
    )


def h(text, size=18, color=C_TEXT, weight=ft.FontWeight.W_600):
    return ft.Text(text, size=size, color=color, weight=weight)


def dim(text, size=13, color=C_DIM):
    return ft.Text(text, size=size, color=color)


def badge(label, ok, msg):
    c  = C_SUCCESS if ok else C_ERROR
    bg = C_SUCCESS + "1a" if ok else C_ERROR + "1a"
    ic = ft.icons.CHECK_CIRCLE_OUTLINE_ROUNDED if ok else ft.icons.CANCEL_OUTLINED
    return ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ic, color=c, size=15),
                    ft.Text(label, color=c, size=13, weight=ft.FontWeight.W_600)], spacing=5),
            ft.Text(msg, color=C_DIM, size=11),
        ], spacing=3),
        bgcolor=bg,
        border=ft.border.all(1, c + "33"),
        border_radius=12,
        padding=14,
        expand=True,
    )


def btn(text, on_click, icon=None, color=C_ACCENT, width=None):
    return ft.ElevatedButton(
        text=text, icon=icon, on_click=on_click, width=width,
        style=ft.ButtonStyle(
            bgcolor=color,
            color=C_TEXT,
            shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.padding.Padding(left=22, right=22, top=13, bottom=13),
            elevation=0,
            overlay_color="#ffffff18",
        ),
    )


def ghost_btn(text, on_click, icon=None):
    return ft.OutlinedButton(
        text=text, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(
            color=C_DIM,
            side=ft.BorderSide(1, C_BORDER),
            shape=ft.RoundedRectangleBorder(radius=12),
            padding=ft.padding.Padding(left=18, right=18, top=13, bottom=13),
            overlay_color="#ffffff0d",
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
    opts = [ft.dropdown.Option(o) for o in options]
    val  = value if value in options else (options[0] if options else None)
    return ft.Dropdown(
        label=label, options=opts, value=val,
        bgcolor=C_GLASS,
        border_color=C_BORDER,
        focused_border_color=C_ACCENT,
        border_radius=12,
        color=C_TEXT,
        label_style=ft.TextStyle(color=C_DIM, size=12),
        padding=ft.padding.Padding(left=4, right=4, top=0, bottom=0),
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main(page: ft.Page):
    page.title        = "Notion → Anki"
    page.bgcolor      = C_BG
    page.theme_mode   = ft.ThemeMode.DARK
    page.theme        = ft.Theme(color_scheme_seed=C_ACCENT)
    page.padding      = 0
    page.window.width      = 1120
    page.window.height     = 760
    page.window.min_width  = 820
    page.window.min_height = 580

    # ── App state ──────────────────────────────────────────────────────────────
    cfg = load_cfg()
    state = dict(
        cfg=cfg,
        conn_status=None,
        log_lines=[],
        last_stats=None,
        sync_running=False,
        sync_result=None,
        notion_dbs=None,
        setup_step=1,
        setup_mode="hierarchical",
        setup_parent_db_id=None,
        setup_parent_db_name=None,
        setup_child_props=None,
    )

    # ── Shared setting fields ──────────────────────────────────────────────────
    f_token    = field("Notion Token",       cfg.get("NOTION_TOKEN", ""),          password=True, hint="secret_...")
    f_ant_key  = field("Anthropic API Key",  cfg.get("ANTHROPIC_API_KEY", ""),     password=True, hint="sk-ant-...")
    f_gem_key  = field("Gemini API Key",     cfg.get("GEMINI_API_KEY", ""),        password=True, hint="AIza...")
    f_host     = field("Anki Host",          cfg.get("ANKI_HOST", "http://localhost:8765"))
    f_cards    = field("Máx. flashcards",    cfg.get("MAX_FLASHCARDS_POR_AULA", "10"), width=140)

    prov_radio = ft.RadioGroup(
        value=cfg.get("AI_PROVIDER", "claude"),
        content=ft.Row([
            ft.Radio(value="claude", label="Claude", fill_color=C_ACCENT,
                     label_style=ft.TextStyle(color=C_DIM, size=13)),
            ft.Radio(value="gemini", label="Gemini", fill_color=C_ACCENT,
                     label_style=ft.TextStyle(color=C_DIM, size=13)),
        ], spacing=24),
    )
    gem_model_dd = dropdown(
        "Modelo Gemini",
        ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
        value=cfg.get("GEMINI_MODEL", "gemini-2.0-flash"),
    )

    # ── Snackbar ───────────────────────────────────────────────────────────────
    def snack(msg, color=C_SUCCESS):
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg, color="#ffffff"),
            bgcolor=color,
            duration=3000,
        )
        page.snack_bar.open = True
        page.update()

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 0 — SINCRONIZAR
    # ══════════════════════════════════════════════════════════════════════════

    conn_row     = ft.Row(controls=[dim("Clique em Testar para verificar.")], spacing=10)
    log_field    = ft.Text("", size=11, color=C_DIM, selectable=True,
                           font_family="monospace", no_wrap=False)
    result_text  = ft.Text("", size=13)
    progress_bar = ft.ProgressBar(visible=False, color=C_ACCENT,
                                  bgcolor=C_BORDER, height=3, border_radius=2)
    stats_row    = ft.Row(controls=[], spacing=10)
    sync_btn_ref = ft.Ref[ft.ElevatedButton]()

    def get_key_and_prov():
        p = prov_radio.value or "claude"
        k = f_ant_key.value if p == "claude" else f_gem_key.value
        return k, p

    def on_test(e):
        def work():
            token = f_token.value or ""
            host  = f_host.value  or "http://localhost:8765"
            key, prov = get_key_and_prov()
            n_ok, n_msg = check_notion(token)
            a_ok, a_msg = check_ai_key(prov, key)
            k_ok, k_msg = check_anki(host)
            state["conn_status"] = {
                "notion": (n_ok, n_msg),
                "ai":     (a_ok, a_msg, prov.capitalize()),
                "anki":   (k_ok, k_msg),
            }
            s = state["conn_status"]
            conn_row.controls = [
                badge("Notion", *s["notion"]),
                badge(s["ai"][2], s["ai"][0], s["ai"][1]),
                badge("Anki", *s["anki"]),
            ]
            page.update()
        threading.Thread(target=work, daemon=True).start()

    def on_sync(e):
        if state["sync_running"]:
            return
        if not load_notion_config():
            snack("Configure o Notion antes de sincronizar!", C_WARNING)
            return
        state["sync_running"] = True
        state["log_lines"]    = []
        state["last_stats"]   = None
        state["sync_result"]  = None
        if sync_btn_ref.current:
            sync_btn_ref.current.disabled = True
        progress_bar.visible = True
        log_field.value      = ""
        result_text.value    = ""
        stats_row.controls   = []
        page.update()

        env = {
            "NOTION_TOKEN":            f_token.value,
            "AI_PROVIDER":             prov_radio.value,
            "ANTHROPIC_API_KEY":       f_ant_key.value,
            "GEMINI_API_KEY":          f_gem_key.value,
            "GEMINI_MODEL":            gem_model_dd.value,
            "ANKI_HOST":               f_host.value,
            "MAX_FLASHCARDS_POR_AULA": f_cards.value or "10",
        }

        def work():
            proc = run_sync(env)
            for line in proc.stdout:
                state["log_lines"].append(line)
                log_field.value = "".join(state["log_lines"][-100:])
                page.update()
            proc.wait()
            state["sync_running"] = False
            state["last_stats"]   = parse_stats(state["log_lines"])
            state["sync_result"]  = "success" if proc.returncode == 0 else "error"
            if sync_btn_ref.current:
                sync_btn_ref.current.disabled = False
            progress_bar.visible = False
            s = state["last_stats"]
            if state["sync_result"] == "success":
                result_text.value = "✅  Sincronização concluída!"
                result_text.color = C_SUCCESS
            else:
                result_text.value = "❌  Erro na sincronização. Veja o log."
                result_text.color = C_ERROR

            def stat_card(label, val, warn=False):
                c = C_ERROR if warn and val > 0 else C_ACCENT2
                return ft.Container(
                    content=ft.Column([
                        ft.Text(str(val), size=26, weight=ft.FontWeight.W_700, color=c),
                        dim(label, size=11),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
                    bgcolor=C_GLASS,
                    border=ft.border.all(1, C_BORDER),
                    border_radius=14,
                    padding=ft.padding.Padding(left=20, right=20, top=16, bottom=16),
                    expand=True,
                )

            stats_row.controls = [
                stat_card("Categorias",  s["disciplinas"]),
                stat_card("Itens",       s["itens"]),
                stat_card("Gerados",     s["gerados"]),
                stat_card("No Anki",     s["enviados"]),
                stat_card("Erros",       s["erros"], warn=True),
            ]
            page.update()
        threading.Thread(target=work, daemon=True).start()

    def on_clear(e):
        state["log_lines"] = []
        log_field.value    = ""
        result_text.value  = ""
        stats_row.controls = []
        page.update()

    view_sync = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.icons.SYNC_ROUNDED, color=C_ACCENT, size=22),
                    h("Sincronizar")], spacing=10),
            dim("Busca itens do Notion, gera flashcards com IA e envia ao Anki."),
        ], spacing=6)),

        ft.Container(height=12),

        glass(ft.Column([
            ft.Row([
                h("Conexões", size=14),
                ft.Spacer(),
                btn("Testar", on_test, icon=ft.icons.WIFI_ROUNDED),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=10),
            conn_row,
        ], spacing=0)),

        ft.Container(height=12),

        glass(ft.Column([
            ft.Row([
                h("Sincronização", size=14),
                ft.Spacer(),
                ft.IconButton(
                    icon=ft.icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=C_MUTED, tooltip="Limpar log",
                    on_click=on_clear,
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            dim("Processa apenas itens novos ou modificados."),
            ft.Container(height=10),
            progress_bar,
            ft.Container(height=4),
            ft.ElevatedButton(
                ref=sync_btn_ref,
                text="▶   Iniciar Sincronização",
                on_click=on_sync,
                style=ft.ButtonStyle(
                    bgcolor=C_ACCENT,
                    color=C_TEXT,
                    shape=ft.RoundedRectangleBorder(radius=12),
                    padding=ft.padding.Padding(left=32, right=32, top=14, bottom=14),
                    elevation=0,
                    overlay_color="#ffffff18",
                ),
            ),
            result_text,
            ft.Container(height=8),
            ft.Container(
                content=ft.Column([log_field], scroll=ft.ScrollMode.AUTO),
                bgcolor="#ffffff08",
                border=ft.border.all(1, C_BORDER),
                border_radius=12,
                padding=12,
                height=210,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
        ], spacing=8)),

        ft.Container(height=12),
        stats_row,
        ft.Container(height=16),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 1 — CONFIGURAR NOTION (wizard)
    # ══════════════════════════════════════════════════════════════════════════

    setup_col = ft.Column(controls=[], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    def dot(n):
        done   = n < state["setup_step"]
        active = n == state["setup_step"]
        c   = C_SUCCESS if done else (C_ACCENT if active else C_MUTED)
        bg  = C_SUCCESS + "22" if done else (C_ACCENT + "22" if active else C_GLASS)
        lbl = "✓" if done else str(n)
        return ft.Container(
            content=ft.Text(lbl, color=c, size=11, weight=ft.FontWeight.W_700,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=bg, border=ft.border.all(2, c),
            border_radius=20, width=32, height=32,
            alignment=ft.alignment.center,
        )

    def line(active):
        return ft.Container(bgcolor=C_ACCENT if active else C_BORDER,
                            height=2, expand=True, border_radius=1)

    def rebuild():
        step    = state["setup_step"]
        token   = f_token.value or load_cfg().get("NOTION_TOKEN", "")
        ctrls   = []

        # Step indicator
        ctrls.append(glass(ft.Column([
            ft.Row([dot(1), line(step > 1), dot(2), line(step > 2),
                    dot(3), line(step > 3), dot(4)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            ft.Container(height=6),
            ft.Row([dim(l, size=10) for l in ["Modo", "Database", "Campos", "Anki"]],
                   alignment=ft.MainAxisAlignment.SPACE_AROUND),
        ], spacing=0), padding=ft.padding.Padding(left=20, right=20, top=14, bottom=14)))
        ctrls.append(ft.Container(height=12))

        # ── Step 1 ────────────────────────────────────────────────────────────
        if step == 1:
            m_radio = ft.RadioGroup(
                value=state["setup_mode"],
                content=ft.Column([
                    ft.Container(
                        content=ft.Row([
                            ft.Radio(value="hierarchical", fill_color=C_ACCENT),
                            ft.Column([
                                ft.Text("🗂  Hierárquico", color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                                dim("DB pai → DB filho. Categorias com subcategorias."),
                            ], spacing=2),
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=C_GLASS, border=ft.border.all(1, C_BORDER),
                        border_radius=12, padding=14, margin=ft.margin.Margin(left=0, right=0, top=0, bottom=8),
                    ),
                    ft.Container(
                        content=ft.Row([
                            ft.Radio(value="flat", fill_color=C_ACCENT),
                            ft.Column([
                                ft.Text("📋  Plano", color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                                dim("DB único onde cada linha vira flashcards."),
                            ], spacing=2),
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=C_GLASS, border=ft.border.all(1, C_BORDER),
                        border_radius=12, padding=14,
                    ),
                ], spacing=0),
            )

            def next1(e):
                state["setup_mode"] = m_radio.value or "hierarchical"
                state["setup_step"] = 2
                rebuild()

            ctrls.append(glass(ft.Column([
                h("Passo 1 — Modo de estrutura", size=15),
                ft.Container(height=12),
                m_radio,
                ft.Container(height=16),
                ft.Row([btn("Próximo →", next1)], alignment=ft.MainAxisAlignment.END),
            ], spacing=0)))

            existing = load_notion_config()
            if existing:
                mode_lbl = "Hierárquico" if existing.get("mode") == "hierarchical" else "Plano"
                last     = (existing.get("last_sync_time") or "Nunca")[:16].replace("T", " ")
                ctrls.append(ft.Container(height=12))
                ctrls.append(glass(ft.Column([
                    ft.Row([
                        ft.Icon(ft.icons.CHECK_CIRCLE_OUTLINE_ROUNDED, color=C_SUCCESS, size=16),
                        ft.Text("Configuração salva", color=C_SUCCESS, size=13, weight=ft.FontWeight.W_600),
                        ft.Spacer(),
                        ghost_btn("Reconfigurar", lambda e: (
                            state.update({"setup_step": 1, "notion_dbs": None}) or rebuild()
                        )),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                    ft.Row([
                        ft.Text(existing.get("parent_db_name", "—"), color=C_TEXT, size=13,
                                weight=ft.FontWeight.W_500),
                        dim(f"· {mode_lbl}"),
                        dim(f"· Deck: {existing.get('anki_deck_root', '')}"),
                    ], spacing=8),
                    dim(f"Último sync: {last}", size=11),
                ], spacing=6)))

        # ── Step 2 ────────────────────────────────────────────────────────────
        elif step == 2:
            if not token:
                ctrls.append(glass(ft.Row([
                    ft.Icon(ft.icons.WARNING_AMBER_ROUNDED, color=C_WARNING),
                    ft.Text("Insira o Notion Token nas Configurações.", color=C_WARNING, size=13),
                ], spacing=8)))
            elif state["notion_dbs"] is None:
                def load_dbs():
                    state["notion_dbs"] = list_notion_databases(token)
                    rebuild()
                    page.update()
                threading.Thread(target=load_dbs, daemon=True).start()
                ctrls.append(glass(ft.Row([
                    ft.ProgressRing(width=18, height=18, color=C_ACCENT, stroke_width=2),
                    dim("Buscando databases no Notion..."),
                ], spacing=12)))
            else:
                dbs = state["notion_dbs"]
                if not dbs:
                    ctrls.append(glass(ft.Row([
                        ft.Icon(ft.icons.ERROR_ROUNDED, color=C_ERROR),
                        ft.Text("Nenhum database encontrado. Verifique token e permissões.", color=C_ERROR, size=13),
                    ], spacing=8)))
                else:
                    opts   = {get_db_title(d): d["id"] for d in dbs}
                    labels = list(opts.keys())
                    db_dd  = dropdown(
                        "Database" + (" principal" if state["setup_mode"] == "hierarchical" else " de conteúdo"),
                        labels,
                    )

                    def back2(e):
                        state["setup_step"] = 1; rebuild()

                    def next2(e):
                        if db_dd.value:
                            state["setup_parent_db_id"]   = opts[db_dd.value]
                            state["setup_parent_db_name"] = db_dd.value
                            state["setup_step"] = 3; rebuild()

                    ctrls.append(glass(ft.Column([
                        h("Passo 2 — Database principal", size=15),
                        ft.Container(height=12),
                        db_dd,
                        ft.Container(height=16),
                        ft.Row([ghost_btn("← Voltar", back2), btn("Próximo →", next2)],
                               alignment=ft.MainAxisAlignment.END, spacing=10),
                    ], spacing=0)))

        # ── Step 3 ────────────────────────────────────────────────────────────
        elif step == 3:
            db_id   = state["setup_parent_db_id"]
            db_name = state["setup_parent_db_name"]
            mode    = state["setup_mode"]
            props   = get_database_properties(token, db_id)

            if not props:
                ctrls.append(glass(ft.Row([
                    ft.Icon(ft.icons.ERROR_ROUNDED, color=C_ERROR),
                    ft.Text("Não foi possível buscar propriedades. Verifique o token.", color=C_ERROR, size=13),
                ], spacing=8)))
            else:
                t_opts  = props_by_type(props, "title") or list(props.keys())
                tx_opts = ["(nenhum)"] + props_by_type(props, "rich_text")
                d_opts  = ["(nenhum)"] + props_by_type(props, "date", "created_time", "last_edited_time")
                s_opts  = ["(nenhum)"] + props_by_type(props, "select", "status")
                tx_all  = ["(nenhum)"] + props_by_type(props, "rich_text", "title")

                dd_title    = dropdown("Campo título", t_opts)
                dd_content  = dropdown("Campo conteúdo (texto/resumo)", tx_opts)
                dd_date     = dropdown("Campo data (opcional)", d_opts)
                dd_sync_p   = dropdown("Campo sync (opcional)", s_opts)
                dd_status_p = dropdown("Campo status (filtro, opcional)", s_opts)
                dd_child_t  = dropdown("Campo título do item filho", tx_all) if mode == "hierarchical" else None
                f_kw        = field("Palavra-chave do DB filho", "Aulas") if mode == "hierarchical" else None
                f_sync_done = field("Valor = sincronizado", "✅ Sincronizado")
                f_status_v  = field("Valor = pronto", "✅ Completa")
                use_sync    = ft.Switch(value=True, active_color=C_ACCENT, label="Usar campo de sync no Notion",
                                        label_style=ft.TextStyle(color=C_DIM, size=13))

                def back3(e):
                    state["setup_step"] = 2; rebuild()

                def next3(e):
                    state["setup_child_props"] = {
                        "parent_title_prop": dd_title.value,
                        "child_keyword":     f_kw.value if f_kw else "",
                        "child_title":       dd_child_t.value if dd_child_t else dd_title.value,
                        "content_prop":      None if dd_content.value == "(nenhum)" else dd_content.value,
                        "date_prop":         None if dd_date.value    == "(nenhum)" else dd_date.value,
                        "use_sync":          use_sync.value,
                        "sync_prop":         None if dd_sync_p.value  == "(nenhum)" else dd_sync_p.value,
                        "sync_done":         f_sync_done.value or "✅ Sincronizado",
                        "status_prop":       None if dd_status_p.value == "(nenhum)" else dd_status_p.value,
                        "status_val":        f_status_v.value or None,
                    }
                    state["setup_step"] = 4; rebuild()

                hier_extras = ([
                    ft.Divider(color=C_BORDER, height=1),
                    dim("Database filho:", size=11, color=C_DIM),
                    f_kw, dd_child_t,
                ] if mode == "hierarchical" else [])

                ctrls.append(glass(ft.Column([
                    h(f"Passo 3 — Campos: {db_name}", size=15),
                    ft.Container(height=10),
                    dd_title,
                    *hier_extras,
                    ft.Divider(color=C_BORDER, height=1),
                    dim("Conteúdo:", size=11, color=C_DIM),
                    dd_content, dd_date,
                    ft.Divider(color=C_BORDER, height=1),
                    dim("Controle de sync:", size=11, color=C_DIM),
                    use_sync,
                    dd_sync_p, f_sync_done,
                    dd_status_p, f_status_v,
                    ft.Container(height=8),
                    ft.Row([ghost_btn("← Voltar", back3), btn("Próximo →", next3)],
                           alignment=ft.MainAxisAlignment.END, spacing=10),
                ], spacing=10)))

        # ── Step 4 ────────────────────────────────────────────────────────────
        elif step == 4:
            existing = load_notion_config()
            f_deck   = field("Deck raiz no Anki",
                             existing.get("anki_deck_root", "Notion::Sync") if existing else "Notion::Sync")
            p    = state.get("setup_child_props") or {}
            mode = state["setup_mode"]

            summary = [
                ("Modo",           "Hierárquico" if mode == "hierarchical" else "Plano"),
                ("DB principal",   state.get("setup_parent_db_name", "")),
                ("Campo título",   p.get("parent_title_prop", "")),
                ("Conteúdo",       p.get("content_prop") or "(blocos da página)"),
                ("Controle sync",  "campo Notion" if p.get("use_sync") else "timestamp"),
            ]
            summary_col = ft.Column([
                ft.Row([
                    ft.Text(k + ":", color=C_MUTED, size=12, width=120),
                    ft.Text(v,       color=C_TEXT,  size=12),
                ]) for k, v in summary
            ], spacing=4)

            def back4(e):
                state["setup_step"] = 3; rebuild()

            def save(e):
                p = state.get("setup_child_props") or {}
                ex = load_notion_config()
                save_notion_config({
                    "version":               2,
                    "mode":                  state["setup_mode"],
                    "parent_db_id":          state["setup_parent_db_id"],
                    "parent_db_name":        state["setup_parent_db_name"],
                    "parent_name_prop":      p.get("parent_title_prop", ""),
                    "child_db_keyword":      p.get("child_keyword", ""),
                    "child_title_prop":      p.get("child_title", ""),
                    "child_content_prop":    p.get("content_prop"),
                    "child_date_prop":       p.get("date_prop"),
                    "use_sync_field":        p.get("use_sync", True),
                    "child_sync_prop":       p.get("sync_prop"),
                    "child_sync_done":       p.get("sync_done", "✅ Sincronizado"),
                    "child_status_prop":     p.get("status_prop"),
                    "child_status_complete": p.get("status_val"),
                    "anki_deck_root":        f_deck.value or "Notion::Sync",
                    "last_sync_time":        ex.get("last_sync_time") if ex else None,
                })
                state["setup_step"] = 1
                state["notion_dbs"] = None
                snack("✅ Configuração salva! Vá para Sincronizar.")
                rebuild()

            ctrls.append(glass(ft.Column([
                h("Passo 4 — Anki", size=15),
                ft.Container(height=10),
                f_deck,
                ft.Container(height=12),
                glass(ft.Column([
                    dim("Resumo da configuração", size=11, color=C_DIM),
                    ft.Container(height=6),
                    summary_col,
                ], spacing=0), padding=14),
                ft.Container(height=12),
                ft.Row([ghost_btn("← Voltar", back4),
                        btn("💾 Salvar configuração", save, color=C_SUCCESS)],
                       alignment=ft.MainAxisAlignment.END, spacing=10),
            ], spacing=0)))

        setup_col.controls = ctrls
        page.update()

    rebuild()

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 2 — CONFIGURAÇÕES (API keys)
    # ══════════════════════════════════════════════════════════════════════════

    def on_save_cfg(e):
        save_cfg({
            "NOTION_TOKEN":            f_token.value,
            "AI_PROVIDER":             prov_radio.value,
            "ANTHROPIC_API_KEY":       f_ant_key.value,
            "GEMINI_API_KEY":          f_gem_key.value,
            "GEMINI_MODEL":            gem_model_dd.value,
            "ANKI_HOST":               f_host.value,
            "MAX_FLASHCARDS_POR_AULA": f_cards.value,
        })
        snack("✅ Configurações salvas!")

    view_settings = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.icons.SETTINGS_ROUNDED, color=C_ACCENT, size=22),
                    h("Configurações")], spacing=10),
            dim("Chaves de API e preferências de sincronização."),
        ], spacing=6)),
        ft.Container(height=12),
        glass(ft.Column([
            dim("NOTION", size=10, color=C_MUTED),
            ft.Container(height=6),
            f_token,
        ], spacing=4)),
        ft.Container(height=10),
        glass(ft.Column([
            dim("INTELIGÊNCIA ARTIFICIAL", size=10, color=C_MUTED),
            ft.Container(height=8),
            prov_radio,
            ft.Container(height=8),
            f_ant_key,
            ft.Container(height=8),
            f_gem_key,
            ft.Container(height=8),
            gem_model_dd,
        ], spacing=4)),
        ft.Container(height=10),
        glass(ft.Column([
            dim("ANKI", size=10, color=C_MUTED),
            ft.Container(height=6),
            f_host,
            ft.Container(height=8),
            ft.Row([dim("Máx. flashcards por item:"), f_cards],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=4)),
        ft.Container(height=16),
        btn("💾 Salvar configurações", on_save_cfg, icon=ft.icons.SAVE_ROUNDED),
        ft.Container(height=20),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 3 — AJUDA
    # ══════════════════════════════════════════════════════════════════════════

    def help_block(title, items):
        return ft.Container(
            content=ft.Column([
                ft.Text(title, color=C_ACCENT, size=14, weight=ft.FontWeight.W_600),
                ft.Container(height=8),
                ft.Column([
                    ft.Row([
                        ft.Container(
                            content=ft.Text(str(i + 1), color=C_ACCENT, size=10,
                                            weight=ft.FontWeight.W_700,
                                            text_align=ft.TextAlign.CENTER),
                            bgcolor=C_ACCENT + "22", border_radius=10,
                            width=22, height=22, alignment=ft.alignment.center,
                        ),
                        ft.Text(item, color=C_DIM, size=13, expand=True),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)
                    for i, item in enumerate(items)
                ], spacing=8),
            ], spacing=0),
            bgcolor=C_GLASS,
            border=ft.border.all(1, C_BORDER),
            border_radius=16,
            padding=20,
            margin=ft.margin.Margin(left=0, right=0, top=0, bottom=10),
            shadow=ft.BoxShadow(spread_radius=0, blur_radius=20,
                                color="#00000022", offset=ft.Offset(0, 4)),
        )

    view_help = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.icons.HELP_OUTLINE_ROUNDED, color=C_ACCENT, size=22),
                    h("Como usar")], spacing=10),
            dim("Guia rápido de configuração e sincronização."),
        ], spacing=6)),
        ft.Container(height=12),
        help_block("1. Configurar integração Notion", [
            "Acesse notion.so/my-integrations",
            "Crie uma integração e copie o Token interno",
            "Em cada database: ··· → Conectar → sua integração",
            "Cole o token na aba Configurações",
        ]),
        help_block("2. Instalar AnkiConnect", [
            "Anki → Ferramentas → Complementos → Obter Complementos",
            "Código do plugin: 2055492159",
            "Reinicie o Anki e mantenha-o aberto",
        ]),
        help_block("3. Configurar estrutura Notion", [
            "Vá para Configurar Notion e escolha o modo",
            "Hierárquico: DB de categorias com DB filho em cada página",
            "Plano: DB único onde cada linha vira flashcards",
            "Mapeie os campos e defina o deck do Anki",
        ]),
        help_block("4. Sincronizar", [
            "Sincronizar → Testar Conexões",
            "Confirme Notion, IA e Anki conectados",
            "Iniciar Sincronização e acompanhe o log",
        ]),
        ft.Container(height=16),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # LAYOUT — Rail + Content
    # ══════════════════════════════════════════════════════════════════════════

    views = [view_sync, setup_col, view_settings, view_help]

    content = ft.Container(
        content=view_sync,
        expand=True,
        padding=ft.padding.Padding(left=28, right=28, top=24, bottom=16),
    )

    def on_nav(e):
        idx = e.control.selected_index
        content.content = views[idx]
        if idx == 1:
            rebuild()
        page.update()

    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=88,
        group_alignment=-0.9,
        bgcolor=C_GLASS,
        indicator_color=C_ACCENT + "2a",
        indicator_shape=ft.RoundedRectangleBorder(radius=12),
        leading=ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Text("N⚡A", size=15, weight=ft.FontWeight.W_800, color=C_ACCENT),
                    bgcolor=C_ACCENT + "22",
                    border=ft.border.all(1, C_ACCENT + "44"),
                    border_radius=14,
                    padding=ft.padding.Padding(left=14, right=14, top=10, bottom=10),
                ),
                dim("Notion Anki", size=9, color=C_MUTED),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=5),
            padding=ft.padding.Padding(left=0, right=0, top=20, bottom=20),
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.icons.SYNC_OUTLINED,
                selected_icon=ft.icons.SYNC_ROUNDED,
                label="Sync",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.ACCOUNT_TREE_OUTLINED,
                selected_icon=ft.icons.ACCOUNT_TREE_ROUNDED,
                label="Notion",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.TUNE_OUTLINED,
                selected_icon=ft.icons.TUNE_ROUNDED,
                label="Config",
            ),
            ft.NavigationRailDestination(
                icon=ft.icons.HELP_OUTLINE_ROUNDED,
                selected_icon=ft.icons.HELP_ROUNDED,
                label="Ajuda",
            ),
        ],
        on_change=on_nav,
    )

    sidebar = ft.Container(
        content=rail,
        border=ft.border.only(right=ft.BorderSide(1, C_BORDER)),
    )

    page.add(
        ft.Row([sidebar, content], expand=True, spacing=0)
    )
    page.update()


ft.app(target=main)
