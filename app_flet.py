#!/usr/bin/env python3
"""
app_flet.py — Interface Notion → Anki (Flet / Liquid Glass)
Rodar: python app_flet.py
"""

import os
import sys
import json
import time
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
    NotionClient = None
    NOTION_CLIENT_AVAILABLE = False

from ui_components import (
    C_BG, C_GLASS, C_GLASS_HVR, C_BORDER,
    C_ACCENT, C_ACCENT2, C_SUCCESS, C_WARNING, C_ERROR,
    C_TEXT, C_DIM, C_MUTED,
    _ball, _bonly,
    glass, h, dim, badge, btn, ghost_btn,
    field, dropdown, hint, field_with_hint,
)

ROOT            = Path(__file__).parent
ENV_FILE        = ROOT / ".env"
NOTION_CFG_FILE = ROOT / "notion_config.json"
_NOTION_VERSION = "2022-06-28"


# ── Config helpers ─────────────────────────────────────────────────────────────

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


# ── Connection checks ──────────────────────────────────────────────────────────

def check_notion(token: str):
    if not token:
        return False, "Token não informado"
    try:
        r = requests.get(
            "https://api.notion.com/v1/users/me",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION},
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


# ── Notion API ─────────────────────────────────────────────────────────────────

def _notion_get(token: str, path: str) -> dict:
    r = requests.get(
        f"https://api.notion.com/v1{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Notion-Version": _NOTION_VERSION},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def list_notion_databases(token: str, on_progress=None):
    """Returns (list_of_dbs, error_msg). error_msg is None on success."""
    def prog(msg):
        if on_progress:
            on_progress(msg)

    if not NOTION_CLIENT_AVAILABLE:
        return [], "notion-client não instalado"
    if not token:
        return [], "Token não informado"
    try:
        client = NotionClient(auth=token)

        prog("🔍 Buscando tabelas conectadas à integração...")
        db_map, cursor = {}, None
        while True:
            kwargs = {"filter": {"property": "object", "value": "data_source"},
                      "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.search(**kwargs)
            for db in resp.get("results", []):
                title = (db.get("title") or [{}])[0].get("plain_text", db["id"][:8])
                prog(f"📋 Tabela encontrada: {title}")
                db_map[db["id"]] = db
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        # Fallback: Notion bug — child pages don't always index the parent DB in /search,
        # but direct retrieval works.
        prog("📄 Verificando páginas para descobrir tabelas adicionais...")
        page_cursor, page_count = None, 0
        while True:
            kwargs = {"filter": {"property": "object", "value": "page"},
                      "page_size": 100}
            if page_cursor:
                kwargs["start_cursor"] = page_cursor
            resp = client.search(**kwargs)
            results = resp.get("results", [])
            page_count += len(results)
            if results:
                prog(f"📄 {page_count} página(s) analisada(s)...")
            for page in results:
                p_title = ""
                try:
                    p_title = (page.get("properties", {}).get(
                        next(iter(page.get("properties", {})), ""), {}
                    ).get("title") or [{}])[0].get("plain_text", "")
                except Exception:
                    pass
                parent = page.get("parent", {})
                db_id  = parent.get("database_id") or parent.get("data_source_id")
                if parent.get("type") in ("database_id", "data_source_id") and db_id:
                    if db_id not in db_map:
                        if p_title:
                            prog(f"🔎 Encontrado em \"{p_title}\", buscando tabela...")
                        try:
                            db = _notion_get(token, f"/databases/{db_id}")
                            db_title = (db.get("title") or [{}])[0].get("plain_text", db_id[:8])
                            prog(f"✅ Tabela descoberta: {db_title}")
                            db_map[db_id] = db
                        except Exception:
                            pass
            if not resp.get("has_more"):
                break
            page_cursor = resp.get("next_cursor")

        prog(f"✔ Busca concluída — {len(db_map)} tabela(s) encontrada(s)")
        return list(db_map.values()), None
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "unauthorized" in msg.lower():
            return [], "Token inválido ou expirado (401)"
        if "403" in msg or "forbidden" in msg.lower():
            return [], "Sem permissão (403) — verifique o token"
        return [], f"Erro: {msg[:120]}"


def get_database_properties(token: str, db_id: str) -> dict:
    if not token or not db_id:
        return {}
    try:
        return _notion_get(token, f"/databases/{db_id}").get("properties", {})
    except Exception:
        return {}


def get_db_title(db: dict) -> str:
    try:
        return db["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return db.get("id", "—")[:8]


def props_by_type(props: dict, *types: str) -> list:
    return [k for k, v in props.items() if v["type"] in types]


def suggest_fields(props: dict) -> dict:
    """Keyword + type heuristics to pre-fill field dropdowns."""
    _CONTENT_KW = {"conteúdo", "content", "resumo", "summary", "notas", "notes",
                   "anotações", "texto", "descrição", "description", "corpo", "body"}
    _DATE_KW    = {"data", "date", "dia", "quando", "when", "criado", "created"}
    _SYNC_KW    = {"sync", "sincronizado", "anki", "sincronizar", "exportado"}
    _STATUS_KW  = {"status", "estado", "pronto", "completo", "done", "situação"}

    def score(name, keywords):
        n = name.lower()
        return any(k in n for k in keywords)

    hints = {}

    for name, meta in props.items():
        if meta.get("type") == "title":
            hints["title"] = name
            break

    for name, meta in props.items():
        if meta.get("type") == "rich_text" and score(name, _CONTENT_KW):
            hints["content"] = name
            break
    if "content" not in hints:
        for name, meta in props.items():
            if meta.get("type") == "rich_text":
                hints["content"] = name
                break

    for name, meta in props.items():
        if meta.get("type") in ("date", "created_time", "last_edited_time") and score(name, _DATE_KW):
            hints["date"] = name
            break
    if "date" not in hints:
        for name, meta in props.items():
            if meta.get("type") in ("date", "created_time", "last_edited_time"):
                hints["date"] = name
                break

    for name, meta in props.items():
        if meta.get("type") in ("select", "status") and score(name, _SYNC_KW):
            hints["sync"] = name
            break

    for name, meta in props.items():
        if meta.get("type") in ("select", "status") and score(name, _STATUS_KW):
            if name != hints.get("sync"):
                hints["status"] = name
                break

    return hints


def get_sample_page(token: str, db_id: str) -> dict | None:
    if not token or not db_id:
        return None
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers={"Authorization": f"Bearer {token}",
                     "Notion-Version": _NOTION_VERSION},
            json={"page_size": 1},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return results[0] if results else None
    except Exception:
        return None


def extract_prop_text(page: dict, prop_name: str) -> str:
    if not page or not prop_name:
        return ""
    prop  = page.get("properties", {}).get(prop_name, {})
    ptype = prop.get("type", "")
    try:
        if ptype in ("title", "rich_text"):
            return "".join(r["plain_text"] for r in prop.get(ptype, []))
        if ptype == "select":
            return prop["select"]["name"] if prop.get("select") else ""
        if ptype == "status":
            return prop["status"]["name"] if prop.get("status") else ""
        if ptype == "multi_select":
            return ", ".join(o["name"] for o in prop.get("multi_select", []))
        if ptype == "date":
            return prop["date"]["start"] if prop.get("date") else ""
        if ptype == "created_time":
            return prop.get("created_time", "")[:10]
        if ptype == "last_edited_time":
            return prop.get("last_edited_time", "")[:10]
        if ptype == "number":
            return str(prop.get("number", ""))
    except Exception:
        pass
    return ""


# ── Sync helpers ───────────────────────────────────────────────────────────────

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


# ── Main ───────────────────────────────────────────────────────────────────────

def main(page: ft.Page):
    page.title      = "Notion → Anki"
    page.bgcolor    = C_BG
    page.theme_mode = ft.ThemeMode.DARK

    _cs = ft.ColorScheme(
        primary=C_ACCENT,
        primary_container=f"{C_ACCENT},0.2",
        on_primary=C_TEXT,
        on_primary_container=C_TEXT,
        secondary=C_ACCENT2,
        secondary_container=f"{C_ACCENT2},0.133",
        on_secondary=C_TEXT,
        on_secondary_container=C_TEXT,
        tertiary=C_ACCENT,
        tertiary_container="#0f0f20",
        on_tertiary=C_TEXT,
        on_tertiary_container=C_TEXT,
        surface=C_BG,
        surface_tint=C_BG,
        surface_dim=C_BG,
        surface_bright="#0f0f1f",
        surface_container_lowest=C_BG,
        surface_container_low="#0a0a18",
        surface_container="#0d0d1e",
        surface_container_high="#111125",
        surface_container_highest="#181830",
        on_surface=C_TEXT,
        on_surface_variant=C_DIM,
        outline="#ffffff,0.133",
        outline_variant="#ffffff,0.067",
        error=C_ERROR,
        on_error=C_TEXT,
        shadow="#000000",
        scrim="#000000",
    )
    _theme = ft.Theme(
        color_scheme=_cs,
        use_material3=False,
        scaffold_bgcolor=C_BG,
    )
    page.theme      = _theme
    page.dark_theme = _theme
    page.padding    = 0
    page.window.width      = 1120
    page.window.height     = 860
    page.window.min_width  = 820
    page.window.min_height = 680

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
        notion_dbs_err=None,
        notion_loading=False,
        notion_db_checked=None,
        notion_db_expanded=None,
        setup_step=1,
        setup_mode="hierarchical",
        setup_parent_db_id=None,
        setup_parent_db_name=None,
        setup_selected_dbs=[],
        setup_child_props=None,
    )

    # ── Shared setting fields ──────────────────────────────────────────────────
    f_token   = field("Notion Token",      cfg.get("NOTION_TOKEN", ""),          password=True, hint="secret_...")
    f_ant_key = field("Anthropic API Key", cfg.get("ANTHROPIC_API_KEY", ""),     password=True, hint="sk-ant-...")
    f_gem_key = field("Gemini API Key",    cfg.get("GEMINI_API_KEY", ""),        password=True, hint="AIza...")
    f_host    = field("Anki Host",         cfg.get("ANKI_HOST", "http://localhost:8765"))
    f_cards   = field("Máx. flashcards",   cfg.get("MAX_FLASHCARDS_POR_AULA", "10"), width=140)

    gem_model_dd = dropdown(
        "Modelo Gemini",
        ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
        value=cfg.get("GEMINI_MODEL", "gemini-2.0-flash"),
    )

    _init_provider = cfg.get("AI_PROVIDER", "claude")
    ant_key_wrap = ft.Container(content=f_ant_key, visible=(_init_provider == "claude"))
    gem_key_wrap = ft.Container(
        content=ft.Column([f_gem_key, ft.Container(height=8), gem_model_dd], spacing=8),
        visible=(_init_provider == "gemini"),
    )

    def _on_provider_change(e):
        is_gemini = e.control.value == "gemini"
        ant_key_wrap.visible = not is_gemini
        gem_key_wrap.visible = is_gemini
        page.update()

    prov_radio = ft.RadioGroup(
        value=_init_provider,
        on_change=_on_provider_change,
        content=ft.Row([
            ft.Radio(value="claude", label="Claude", fill_color=C_ACCENT,
                     label_style=ft.TextStyle(color=C_DIM, size=13)),
            ft.Radio(value="gemini", label="Gemini", fill_color=C_ACCENT,
                     label_style=ft.TextStyle(color=C_DIM, size=13)),
        ], spacing=24),
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

    # ── Connection cards (mutable, updated in-place from threads) ──────────────
    def _mk_conn_card(icon_name, label):
        ring_ctrl = ft.ProgressRing(width=18, height=18, stroke_width=2, color=C_ACCENT)
        icon_ctrl = ft.Icon(icon_name, color=C_MUTED, size=20)
        ring_wrap = ft.Container(content=ring_ctrl, width=22, height=22,
                                  alignment=ft.Alignment(0, 0), visible=False)
        icon_wrap = ft.Container(content=icon_ctrl, width=22, height=22,
                                  alignment=ft.Alignment(0, 0), visible=True)
        msg_ctrl  = ft.Text("Aguardando", color=C_MUTED, size=11,
                             text_align=ft.TextAlign.CENTER, max_lines=2)
        card = ft.Container(
            content=ft.Column([
                ft.Stack([ring_wrap, icon_wrap], width=22, height=22),
                ft.Container(height=6),
                ft.Text(label, color=C_DIM, size=11, weight=ft.FontWeight.W_600,
                        text_align=ft.TextAlign.CENTER),
                msg_ctrl,
            ], spacing=3, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=C_GLASS, border=_ball(1, C_BORDER),
            border_radius=14,
            padding=ft.padding.Padding(left=14, right=14, top=12, bottom=12),
            expand=True,
        )
        return card, ring_wrap, icon_wrap, icon_ctrl, msg_ctrl

    def _card_testing(rw, iw, msg, card):
        rw.visible = True;  iw.visible = False
        msg.value = "Testando..."; msg.color = C_DIM
        card.bgcolor = C_GLASS; card.border = _ball(1, f"{C_ACCENT},0.35")

    def _card_ok(rw, iw, ic, msg, card, text):
        rw.visible = False; iw.visible = True
        ic.name = ft.Icons.CHECK_CIRCLE_ROUNDED; ic.color = C_SUCCESS
        msg.value = text[:30]; msg.color = C_SUCCESS
        card.bgcolor = f"{C_SUCCESS},0.07"; card.border = _ball(1, f"{C_SUCCESS},0.35")

    def _card_err(rw, iw, ic, msg, card, text):
        rw.visible = False; iw.visible = True
        ic.name = ft.Icons.CANCEL_ROUNDED; ic.color = C_ERROR
        msg.value = text[:30]; msg.color = C_ERROR
        card.bgcolor = f"{C_ERROR},0.07"; card.border = _ball(1, f"{C_ERROR},0.35")

    nc_card, nc_rw, nc_iw, nc_ic, nc_msg = _mk_conn_card(ft.Icons.CLOUD_OUTLINED,     "Notion")
    ai_card, ai_rw, ai_iw, ai_ic, ai_msg = _mk_conn_card(ft.Icons.SMART_TOY_OUTLINED, "IA")
    ak_card, ak_rw, ak_iw, ak_ic, ak_msg = _mk_conn_card(ft.Icons.STYLE_OUTLINED,     "Anki")

    # ── Flashcard workshop animation ───────────────────────────────────────────
    _fc_shadow2 = ft.Container(
        bgcolor="#0b0b22", border=_ball(1, f"{C_ACCENT},0.07"),
        border_radius=12, width=224, height=72, top=16, left=16,
    )
    _fc_shadow1 = ft.Container(
        bgcolor="#0f0f2a", border=_ball(1, f"{C_ACCENT},0.12"),
        border_radius=12, width=224, height=72, top=8, left=8,
    )
    _fc_icon  = ft.Icon(ft.Icons.STYLE_OUTLINED, color=C_ACCENT, size=16)
    _fc_title = ft.Text("Iniciando...", size=12, color=C_TEXT, weight=ft.FontWeight.W_600,
                        no_wrap=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
    _fc_sub   = ft.Text("", size=10, color=C_MUTED)
    _fc_front = ft.Container(
        content=ft.Column([
            ft.Row([_fc_icon, _fc_title], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _fc_sub,
        ], spacing=5),
        bgcolor="#18183a", border=_ball(1, f"{C_ACCENT},0.35"),
        border_radius=12,
        padding=ft.padding.Padding(left=14, right=14, top=14, bottom=14),
        width=224, height=72, top=0, left=0,
    )
    _fc_stack = ft.Stack([_fc_shadow2, _fc_shadow1, _fc_front], width=240, height=88)
    _fc_count = ft.Text("", size=12, color=C_DIM, text_align=ft.TextAlign.CENTER)
    _fc_panel = ft.Container(
        content=ft.Column([
            _fc_stack,
            ft.Container(height=10),
            _fc_count,
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
        visible=False,
        padding=ft.padding.Padding(left=0, right=0, top=16, bottom=4),
    )

    log_field    = ft.Text("", size=11, color=C_DIM, selectable=True,
                           font_family="monospace", no_wrap=False)
    result_text  = ft.Text("", size=13)
    progress_bar = ft.ProgressBar(visible=False, color=C_ACCENT,
                                  bgcolor=C_BORDER, height=3, border_radius=2)
    stats_row    = ft.Row(controls=[], spacing=10)
    sync_btn_ref     = ft.Ref[ft.Button]()
    test_btn_ref     = ft.Ref[ft.Button]()
    save_cfg_btn_ref = ft.Ref[ft.Button]()

    def get_key_and_prov():
        p = prov_radio.value or "claude"
        k = f_ant_key.value if p == "claude" else f_gem_key.value
        return k, p

    def on_test(e):
        if test_btn_ref.current:
            test_btn_ref.current.disabled = True
            test_btn_ref.current.content  = "Testando..."
            test_btn_ref.current.icon     = ft.Icons.REFRESH_ROUNDED
        _card_testing(nc_rw, nc_iw, nc_msg, nc_card)
        _card_testing(ai_rw, ai_iw, ai_msg, ai_card)
        _card_testing(ak_rw, ak_iw, ak_msg, ak_card)
        page.update()

        def work():
            token = f_token.value or ""
            host  = f_host.value  or "http://localhost:8765"
            key, prov = get_key_and_prov()
            n_ok, n_txt = check_notion(token)
            a_ok, a_txt = check_ai_key(prov, key)
            k_ok, k_txt = check_anki(host)

            if n_ok: _card_ok(nc_rw, nc_iw, nc_ic, nc_msg, nc_card, n_txt)
            else:    _card_err(nc_rw, nc_iw, nc_ic, nc_msg, nc_card, n_txt)

            ai_label = f"{prov.capitalize()}: {a_txt}" if a_ok else a_txt
            if a_ok: _card_ok(ai_rw, ai_iw, ai_ic, ai_msg, ai_card, ai_label)
            else:    _card_err(ai_rw, ai_iw, ai_ic, ai_msg, ai_card, ai_label)

            if k_ok: _card_ok(ak_rw, ak_iw, ak_ic, ak_msg, ak_card, k_txt)
            else:    _card_err(ak_rw, ak_iw, ak_ic, ak_msg, ak_card, k_txt)

            if test_btn_ref.current:
                test_btn_ref.current.disabled = False
                test_btn_ref.current.content  = "Testar"
                test_btn_ref.current.icon     = ft.Icons.WIFI_ROUNDED
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
            sync_btn_ref.current.content  = "⏳  Sincronizando..."
            sync_btn_ref.current.icon     = None
        progress_bar.visible = True
        log_field.value      = ""
        result_text.value    = ""
        stats_row.controls   = []
        # Reset flashcard workshop
        _fc_panel.visible   = True
        _fc_icon.color      = C_ACCENT
        _fc_title.value     = "Iniciando..."
        _fc_sub.value       = ""
        _fc_count.value     = "Preparando..."
        _fc_count.color     = C_DIM
        _fc_front.bgcolor   = "#18183a"
        _fc_front.border    = _ball(1, f"{C_ACCENT},0.35")
        _fc_shadow1.bgcolor = "#0f0f2a"
        _fc_shadow2.bgcolor = "#0b0b22"
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
            total_fc = 0
            for line in proc.stdout:
                state["log_lines"].append(line)
                log_field.value = "".join(state["log_lines"][-100:])
                stripped = line.strip()
                # Show current log line on the flashcard front
                if stripped and not stripped.startswith(("---", "===", "Flashcards")):
                    _fc_title.value = stripped[:46]
                # Update running flashcard counter
                if "Flashcards gerados" in line:
                    try:
                        total_fc = int(line.split(":")[-1].strip())
                        _fc_count.value = f"{total_fc} flashcard(s) criado(s)"
                    except ValueError:
                        pass
                page.update()
            proc.wait()
            state["sync_running"] = False
            state["last_stats"]   = parse_stats(state["log_lines"])
            state["sync_result"]  = "success" if proc.returncode == 0 else "error"
            if sync_btn_ref.current:
                sync_btn_ref.current.disabled = False
                sync_btn_ref.current.content  = "▶   Iniciar Sincronização"
                sync_btn_ref.current.icon     = None
            progress_bar.visible = False
            s = state["last_stats"]

            if state["sync_result"] == "success":
                result_text.value = "✅  Sincronização concluída!"
                result_text.color = C_SUCCESS
                # Shine the card stack
                _fc_front.bgcolor   = f"{C_SUCCESS},0.18"
                _fc_front.border    = _ball(1, C_SUCCESS)
                _fc_shadow1.bgcolor = f"{C_SUCCESS},0.08"
                _fc_shadow2.bgcolor = f"{C_SUCCESS},0.04"
                _fc_icon.color      = C_SUCCESS
                _fc_title.value     = "Flashcards enviados ao Anki!"
                _fc_count.value     = f"✅ {s.get('enviados', total_fc)} cartões sincronizados"
                _fc_count.color     = C_SUCCESS
                page.update()
                # Pleasant two-tone completion beep (Windows only, silent on other OS)
                try:
                    import winsound
                    winsound.Beep(880, 120)
                    time.sleep(0.08)
                    winsound.Beep(1108, 200)
                except Exception:
                    pass
            else:
                result_text.value   = "❌  Erro na sincronização. Veja o log."
                result_text.color   = C_ERROR
                _fc_front.border    = _ball(1, f"{C_ERROR},0.4")
                _fc_count.value     = "Erro durante a sincronização"
                _fc_count.color     = C_ERROR

            def stat_card(label, val, warn=False):
                c = C_ERROR if warn and val > 0 else C_ACCENT2
                return ft.Container(
                    content=ft.Column([
                        ft.Text(str(val), size=26, weight=ft.FontWeight.W_700, color=c),
                        dim(label, size=11),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
                    bgcolor=C_GLASS,
                    border=_ball(1, C_BORDER),
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
        state["log_lines"]  = []
        log_field.value     = ""
        result_text.value   = ""
        stats_row.controls  = []
        _fc_panel.visible   = False
        page.update()

    view_sync = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.Icons.SYNC_ROUNDED, color=C_ACCENT, size=20),
                    h("Sincronizar", size=16)], spacing=8),
            dim("Busca itens do Notion, gera flashcards com IA e envia ao Anki.", size=12),
        ], spacing=4), padding=14),

        ft.Container(height=8),

        # ── Conexões — 3 status cards ──────────────────────────────────────────
        glass(ft.Column([
            ft.Row([
                h("Conexões", size=13),
                ft.Container(expand=True),
                btn("Testar", on_test, icon=ft.Icons.WIFI_ROUNDED, ref=test_btn_ref),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=6),
            ft.Row([nc_card, ai_card, ak_card], spacing=10),
        ], spacing=0), padding=14),

        ft.Container(height=8),

        # ── Sincronização ──────────────────────────────────────────────────────
        glass(ft.Column([
            ft.Row([
                h("Sincronização", size=13),
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=C_MUTED, tooltip="Limpar log",
                    on_click=on_clear,
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            dim("Processa apenas itens novos ou modificados.", size=12),
            ft.Container(height=6),
            progress_bar,
            ft.Container(height=2),
            ft.Button(
                "▶   Iniciar Sincronização",
                ref=sync_btn_ref,
                on_click=on_sync,
                style=ft.ButtonStyle(
                    bgcolor=C_ACCENT,
                    color=C_TEXT,
                    shape=ft.RoundedRectangleBorder(radius=12),
                    padding=ft.padding.Padding(left=28, right=28, top=11, bottom=11),
                    elevation=0,
                    overlay_color="#ffffff,0.094",
                ),
            ),
            # Flashcard workshop (visible during/after sync)
            _fc_panel,
            result_text,
            ft.Container(height=4),
            ft.Container(
                content=ft.Column([log_field], scroll=ft.ScrollMode.AUTO),
                bgcolor="#ffffff,0.031",
                border=_ball(1, C_BORDER),
                border_radius=12,
                padding=10,
                height=150,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            ),
        ], spacing=6), padding=14),

        ft.Container(height=8),
        stats_row,
        ft.Container(height=8),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 1 — CONFIGURAR NOTION (wizard)
    # ══════════════════════════════════════════════════════════════════════════

    setup_col = ft.Column(controls=[], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    def dot(n):
        done   = n < state["setup_step"]
        active = n == state["setup_step"]
        c   = C_SUCCESS if done else (C_ACCENT if active else C_MUTED)
        bg  = f"{C_SUCCESS},0.133" if done else (f"{C_ACCENT},0.133" if active else C_GLASS)
        lbl = "✓" if done else str(n)
        return ft.Container(
            content=ft.Text(lbl, color=c, size=11, weight=ft.FontWeight.W_700,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=bg, border=_ball(2, c),
            border_radius=20, width=32, height=32,
            alignment=ft.Alignment(0, 0),
        )

    def stepper_line(active):
        return ft.Container(bgcolor=C_ACCENT if active else C_BORDER,
                            height=2, expand=True, border_radius=1)

    def rebuild():
        step  = state["setup_step"]
        token = f_token.value or load_cfg().get("NOTION_TOKEN", "")
        ctrls = []

        ctrls.append(glass(ft.Column([
            ft.Row([dot(1), stepper_line(step > 1), dot(2), stepper_line(step > 2),
                    dot(3), stepper_line(step > 3), dot(4)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            ft.Container(height=6),
            ft.Row([dim(l, size=10) for l in ["Modo", "Database", "Campos", "Anki"]],
                   alignment=ft.MainAxisAlignment.SPACE_AROUND),
        ], spacing=0), padding=ft.padding.Padding(left=20, right=20, top=14, bottom=14)))
        ctrls.append(ft.Container(height=12))

        if step == 1:
            _build_step1(ctrls)
        elif step == 2:
            _build_step2(ctrls, token)
        elif step == 3:
            _build_step3(ctrls, token)
        elif step == 4:
            _build_step4(ctrls)

        setup_col.controls = ctrls
        page.update()

    # ── Step 1: Mode selection ─────────────────────────────────────────────────
    def _build_step1(ctrls):
        m_radio = ft.RadioGroup(
            value=state["setup_mode"],
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Radio(value="hierarchical", fill_color=C_ACCENT),
                        ft.Column([
                            ft.Text("🗂  Hierárquico", color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                            dim("Você tem duas tabelas: uma com matérias/categorias e outra "
                                "com as aulas/conteúdos dentro de cada categoria.", size=12),
                            ft.Container(height=4),
                            ft.Text("Exemplo: Tabela \"Disciplinas\" → linhas \"Biologia\", "
                                    "\"História\" → cada uma linkada a uma tabela de Aulas com "
                                    "o conteúdo real.",
                                    color=C_MUTED, size=11, italic=True),
                        ], spacing=2, expand=True),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                    bgcolor=C_GLASS, border=_ball(1, C_BORDER),
                    border_radius=12, padding=14,
                    margin=ft.margin.Margin(left=0, right=0, top=0, bottom=8),
                ),
                ft.Container(
                    content=ft.Row([
                        ft.Radio(value="flat", fill_color=C_ACCENT),
                        ft.Column([
                            ft.Text("📋  Plano", color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                            dim("Você tem uma única tabela onde cada linha é um item "
                                "que vira flashcard diretamente.", size=12),
                            ft.Container(height=4),
                            ft.Text("Exemplo: Tabela \"Ideias de jogos\" onde cada linha tem "
                                    "título, descrição e tags — cada linha gera seus flashcards.",
                                    color=C_MUTED, size=11, italic=True),
                        ], spacing=2, expand=True),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                    bgcolor=C_GLASS, border=_ball(1, C_BORDER),
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
                    ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, color=C_SUCCESS, size=16),
                    ft.Text("Configuração salva", color=C_SUCCESS, size=13, weight=ft.FontWeight.W_600),
                    ft.Container(expand=True),
                    ghost_btn("Reconfigurar", lambda e: (
                        state.update({
                            "setup_step": 1, "notion_dbs": None, "notion_dbs_err": None,
                            "notion_loading": False, "setup_selected_dbs": [],
                            "notion_db_checked": None, "notion_db_expanded": None,
                        }) or rebuild()
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

    # ── Step 2: Database selection ─────────────────────────────────────────────
    def _build_step2(ctrls, token):
        if not token:
            ctrls.append(glass(ft.Row([
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=C_WARNING),
                ft.Text("Insira o Notion Token nas Configurações.", color=C_WARNING, size=13),
            ], spacing=8)))
            return

        if state["notion_dbs"] is None:
            def load_dbs():
                dbs, err = list_notion_databases(token)
                state["notion_dbs"]     = dbs
                state["notion_dbs_err"] = err
                state["notion_loading"] = False
                rebuild()

            if not state["notion_loading"]:
                state["notion_loading"] = True
                threading.Thread(target=load_dbs, daemon=True).start()

            ctrls.append(glass(ft.Column([
                ft.Row([
                    ft.ProgressRing(width=16, height=16, color=C_ACCENT, stroke_width=2),
                    dim("Buscando tabelas no Notion..."),
                ], spacing=10),
            ], spacing=0)))
            return

        dbs = state["notion_dbs"]
        err = state.get("notion_dbs_err")

        if err:
            ctrls.append(glass(ft.Column([
                ft.Row([ft.Icon(ft.Icons.ERROR_ROUNDED, color=C_ERROR),
                        ft.Text(err, color=C_ERROR, size=13)], spacing=8),
                ft.Container(height=6),
                dim("Verifique se o token está correto e se os databases foram "
                    "conectados à integração: abra o database no Notion → ··· → "
                    "Conexões → selecione sua integração.", size=12),
            ], spacing=0)))
            return

        if not dbs:
            ctrls.append(glass(ft.Column([
                ft.Row([ft.Icon(ft.Icons.INFO_ROUNDED, color=C_WARNING),
                        ft.Text("Nenhum database encontrado.", color=C_WARNING, size=13)], spacing=8),
                ft.Container(height=6),
                dim("Os databases precisam ser conectados à integração: abra cada "
                    "database no Notion → ··· → Conexões → selecione sua integração.", size=12),
            ], spacing=0)))
            return

        def back2(e):
            state["setup_step"] = 1; rebuild()

        if state["setup_mode"] == "hierarchical":
            opts  = {get_db_title(d): d["id"] for d in dbs}
            db_dd = dropdown("Database principal", list(opts.keys()))

            def next2(e):
                if db_dd.value:
                    state["setup_parent_db_id"]   = opts[db_dd.value]
                    state["setup_parent_db_name"] = db_dd.value
                    state["setup_selected_dbs"]   = [{"id": opts[db_dd.value], "name": db_dd.value}]
                    state["setup_step"] = 3; rebuild()

            ctrls.append(glass(ft.Column([
                h("Passo 2 — Database principal", size=15),
                hint("Selecione a tabela que contém as categorias/disciplinas."),
                ft.Container(height=12),
                db_dd,
                ft.Container(height=16),
                ft.Row([ghost_btn("← Voltar", back2), btn("Próximo →", next2)],
                       alignment=ft.MainAxisAlignment.END, spacing=10),
            ], spacing=0)))
        else:
            _build_step2_flat(ctrls, dbs, token, back2)

    def _build_step2_flat(ctrls, dbs, token, back2):
        if not state.get("notion_db_checked"):
            state["notion_db_checked"]  = {d["id"] for d in dbs}
        if not state.get("notion_db_expanded"):
            state["notion_db_expanded"] = set()

        _ROLE_ICON  = {"title": "📌", "content": "📝", "date": "📅", "sync": "🔄", "status": "🔖"}
        _ROLE_LABEL = {"title": "título", "content": "conteúdo", "date": "data",
                       "sync": "campo sync", "status": "filtro status"}

        db_cards = []
        for db in dbs:
            db_id_card   = db["id"]
            db_name_card = get_db_title(db)
            is_checked   = db_id_card in state["notion_db_checked"]
            is_expanded  = db_id_card in state["notion_db_expanded"]
            props_card   = db.get("properties") or get_database_properties(token, db_id_card)
            n_cols = len(props_card)

            def on_check_card(e, did=db_id_card):
                if e.control.value:
                    state["notion_db_checked"].add(did)
                else:
                    state["notion_db_checked"].discard(did)

            def on_toggle_card(e, did=db_id_card):
                if did in state["notion_db_expanded"]:
                    state["notion_db_expanded"].discard(did)
                else:
                    state["notion_db_expanded"].add(did)
                rebuild()

            border_c = C_ACCENT if is_checked else C_BORDER

            prop_rows = []
            if is_expanded and props_card:
                hints_card = suggest_fields(props_card)
                hint_rev   = {v: k for k, v in hints_card.items()}
                for pname, pmeta in props_card.items():
                    ptype = pmeta.get("type", "?")
                    role  = hint_rev.get(pname)
                    role_badge = (ft.Container(
                        content=ft.Text(
                            f"{_ROLE_ICON.get(role,'')} {_ROLE_LABEL.get(role,'')}",
                            color=C_BG, size=10, weight=ft.FontWeight.W_600),
                        bgcolor=C_ACCENT, border_radius=6,
                        padding=ft.padding.Padding(left=6, right=6, top=2, bottom=2),
                    ) if role else ft.Text(f"[{ptype}]", color=C_MUTED, size=11))
                    prop_rows.append(ft.Row([
                        ft.Text(f"• {pname}", color=C_TEXT, size=12, expand=True),
                        role_badge,
                    ], spacing=6))

            db_cards.append(ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Checkbox(value=is_checked, fill_color=C_ACCENT,
                                    check_color=C_TEXT, on_change=on_check_card),
                        ft.Icon(ft.Icons.TABLE_CHART_ROUNDED, color=C_ACCENT, size=16),
                        ft.Text(db_name_card, color=C_TEXT, size=13,
                                weight=ft.FontWeight.W_500, expand=True),
                        ft.Text(f"{n_cols} col.", color=C_MUTED, size=11),
                        ft.IconButton(
                            icon=ft.Icons.EXPAND_LESS if is_expanded else ft.Icons.EXPAND_MORE,
                            icon_color=C_DIM, icon_size=20,
                            on_click=on_toggle_card,
                            style=ft.ButtonStyle(
                                padding=ft.padding.Padding(0, 0, 0, 0),
                                overlay_color="#ffffff,0.051",
                            ),
                        ),
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    *([ ft.Divider(color=C_BORDER, height=1),
                        ft.Column(prop_rows, spacing=6) ] if prop_rows else []),
                ], spacing=8),
                bgcolor=f"{C_ACCENT},0.04" if is_checked else C_GLASS,
                border=_ball(1, border_c),
                border_radius=14,
                padding=14,
            ))

        n_sel = len(state["notion_db_checked"])

        def next2_flat(e):
            selected = [{"id": d["id"], "name": get_db_title(d)}
                        for d in dbs if d["id"] in state["notion_db_checked"]]
            if selected:
                state["setup_selected_dbs"]   = selected
                state["setup_parent_db_id"]   = selected[0]["id"]
                state["setup_parent_db_name"] = selected[0]["name"]
                state["setup_step"] = 3; rebuild()

        ctrls.append(glass(ft.Column([
            h("Passo 2 — Selecionar tabelas", size=15),
            dim(f"{len(dbs)} tabela(s) encontrada(s) — {n_sel} selecionada(s). "
                "Expanda para ver as colunas. Cada tabela vira um deck no Anki.", size=12),
            ft.Container(height=12),
            ft.Column(db_cards, spacing=8),
            ft.Container(height=16),
            ft.Row([ghost_btn("← Voltar", back2), btn("Próximo →", next2_flat)],
                   alignment=ft.MainAxisAlignment.END, spacing=10),
        ], spacing=0)))

    # ── Step 3: Field mapping ──────────────────────────────────────────────────
    def _build_step3(ctrls, token):
        db_id   = state["setup_parent_db_id"]
        db_name = state["setup_parent_db_name"]
        mode    = state["setup_mode"]
        props   = get_database_properties(token, db_id)
        sample  = get_sample_page(token, db_id)

        if not props:
            ctrls.append(glass(ft.Row([
                ft.Icon(ft.Icons.ERROR_ROUNDED, color=C_ERROR),
                ft.Text("Não foi possível buscar propriedades. Verifique o token.", color=C_ERROR, size=13),
            ], spacing=8)))
            return

        hints   = suggest_fields(props)
        t_opts  = props_by_type(props, "title") or list(props.keys())
        tx_opts = ["(nenhum)"] + props_by_type(props, "rich_text")
        d_opts  = ["(nenhum)"] + props_by_type(props, "date", "created_time", "last_edited_time")
        s_opts  = ["(nenhum)"] + props_by_type(props, "select", "status")
        tx_all  = ["(nenhum)"] + props_by_type(props, "rich_text", "title")

        dd_title    = dropdown("Campo título", t_opts,   hints.get("title"))
        dd_content  = dropdown("Campo conteúdo (texto/resumo)", tx_opts, hints.get("content"))
        dd_date     = dropdown("Campo data (opcional)", d_opts,  hints.get("date"))
        dd_sync_p   = dropdown("Campo sync (opcional)", s_opts,  hints.get("sync"))
        dd_status_p = dropdown("Campo status (filtro, opcional)", s_opts, hints.get("status"))
        dd_child_t  = dropdown("Campo título do item filho", tx_all) if mode == "hierarchical" else None
        f_kw        = field("Palavra-chave do DB filho", "Aulas")    if mode == "hierarchical" else None
        f_sync_done = field("Valor = sincronizado", "✅ Sincronizado")
        f_status_v  = field("Valor = pronto", "✅ Completa")

        w_title   = field_with_hint(dd_title,
            "Coluna que dá nome ao item — vira o título do flashcard.")
        w_content = field_with_hint(dd_content,
            "Coluna com o texto/resumo principal — usado para gerar as perguntas e respostas.")
        w_date    = field_with_hint(dd_date,
            "Filtra itens a partir de uma data. Deixe em (nenhum) para processar tudo.")
        w_sync_p  = field_with_hint(dd_sync_p,
            "Coluna onde o app marca o item como já sincronizado, evitando duplicatas no Anki.")
        w_sync_v  = field_with_hint(f_sync_done,
            "Valor que será gravado nessa coluna quando o item for sincronizado.")
        w_status_p = field_with_hint(dd_status_p,
            "Filtra: só processa linhas cujo status seja o valor abaixo. Útil para ignorar rascunhos.")
        w_status_v = field_with_hint(f_status_v,
            "Valor da coluna de status que indica 'pronto para sincronizar'.")
        w_child_t = field_with_hint(dd_child_t,
            "No DB filho, qual coluna tem o título de cada aula/item.") if dd_child_t else None
        w_kw      = field_with_hint(f_kw,
            "Palavra que identifica o DB filho linkado. Ex: se a relação se chama 'Aulas', use Aulas.") if f_kw else None
        use_sync  = ft.Switch(value=True, active_color=C_ACCENT, label="Usar campo de sync no Notion",
                              label_text_style=ft.TextStyle(color=C_DIM, size=13))

        # Preview card
        sel_dbs = state.get("setup_selected_dbs") or [{"name": db_name}]
        deck_root_preview = "Deck raiz"
        decks_preview = " · ".join(
            f"{deck_root_preview}::{d['name']}" for d in sel_dbs[:3]
        ) + (" ..." if len(sel_dbs) > 3 else "")

        s_title   = extract_prop_text(sample, hints.get("title", ""))   or "Aula dia 27"
        s_content = extract_prop_text(sample, hints.get("content", "")) or "(conteúdo da coluna selecionada)"
        s_date    = extract_prop_text(sample, hints.get("date", ""))    or ""
        content_preview = (s_content[:90] + "…") if len(s_content) > 90 else s_content

        preview_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.PREVIEW_ROUNDED, color=C_ACCENT, size=14),
                    ft.Text("Prévia — como ficará cada item", color=C_ACCENT,
                            size=12, weight=ft.FontWeight.W_600),
                    ft.Text("✨ campos sugeridos automaticamente", color=C_MUTED,
                            size=10, italic=True),
                ], spacing=6),
                ft.Container(height=8),
                ft.Row([ft.Text("📚 Deck:",    color=C_MUTED, size=11, width=80),
                        ft.Text(decks_preview, color=C_DIM,  size=11)], spacing=4),
                ft.Row([ft.Text("📄 Título:",  color=C_MUTED, size=11, width=80),
                        ft.Text(s_title,       color=C_TEXT,  size=12, weight=ft.FontWeight.W_500)], spacing=4),
                *([ ft.Row([ft.Text("📅 Data:", color=C_MUTED, size=11, width=80),
                            ft.Text(s_date,    color=C_DIM,  size=11)], spacing=4) ] if s_date else []),
                ft.Row([ft.Text("📝 Conteúdo:", color=C_MUTED, size=11, width=80),
                        ft.Text(content_preview, color=C_DIM, size=11, expand=True)], spacing=4),
                ft.Container(height=4),
                ft.Row([
                    ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color=C_SUCCESS, size=12),
                    ft.Text(f"IA gerará até {load_cfg().get('MAX_FLASHCARDS_POR_AULA', '10')} flashcards por item",
                            color=C_SUCCESS, size=11),
                ], spacing=4),
            ], spacing=6),
            bgcolor="#12122a",
            border=_ball(1, C_BORDER),
            border_radius=18,
            padding=14,
        )

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
            dim("🔗  Database filho", size=12, color=C_DIM),
            w_kw, w_child_t,
        ] if mode == "hierarchical" else [])

        ctrls.append(preview_card)
        ctrls.append(ft.Container(height=10))
        ctrls.append(glass(ft.Column([
            h(f"Passo 3 — Mapeamento de campos: {db_name}", size=15),
            dim("Confira os campos sugeridos abaixo e ajuste se necessário.", size=12),
            ft.Container(height=10),
            dim("📌  Identificação", size=12, color=C_DIM),
            w_title,
            *hier_extras,
            ft.Divider(color=C_BORDER, height=1),
            dim("📝  Conteúdo para gerar flashcards", size=12, color=C_DIM),
            w_content, w_date,
            ft.Divider(color=C_BORDER, height=1),
            dim("🔄  Controle de sincronização", size=12, color=C_DIM),
            use_sync,
            w_sync_p, w_sync_v,
            w_status_p, w_status_v,
            ft.Container(height=8),
            ft.Row([ghost_btn("← Voltar", back3), btn("Próximo →", next3)],
                   alignment=ft.MainAxisAlignment.END, spacing=10),
        ], spacing=8)))

    # ── Step 4: Anki deck ──────────────────────────────────────────────────────
    def _build_step4(ctrls):
        existing = load_notion_config()
        f_deck   = field("Deck raiz no Anki",
                         existing.get("anki_deck_root", "Notion::Sync") if existing else "Notion::Sync")
        p    = state.get("setup_child_props") or {}
        mode = state["setup_mode"]

        sel_dbs   = state.get("setup_selected_dbs") or [{"name": state.get("setup_parent_db_name", "")}]
        dbs_label = ", ".join(d["name"] for d in sel_dbs) if sel_dbs else ""
        summary = [
            ("Modo",           "Hierárquico" if mode == "hierarchical" else "Plano"),
            ("Tabela(s)",      dbs_label),
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
                "selected_dbs":          state.get("setup_selected_dbs", []),
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
            state["setup_step"]         = 1
            state["notion_dbs"]         = None
            state["notion_dbs_err"]     = None
            state["notion_loading"]     = False
            state["setup_selected_dbs"] = []
            state["notion_db_checked"]  = None
            state["notion_db_expanded"] = None
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

        b = save_cfg_btn_ref.current
        if b:
            b.content = "✓  Salvo!"
            b.icon    = ft.Icons.CHECK_CIRCLE_ROUNDED
            b.style   = ft.ButtonStyle(
                bgcolor=C_SUCCESS,
                color=C_TEXT,
                shape=ft.RoundedRectangleBorder(radius=12),
                padding=ft.padding.Padding(left=22, right=22, top=13, bottom=13),
                elevation=0,
                overlay_color="#ffffff,0.094",
            )
            page.update()

            def _restore():
                time.sleep(2)
                if save_cfg_btn_ref.current:
                    b.content = "💾  Salvar configurações"
                    b.icon    = ft.Icons.SAVE_ROUNDED
                    b.style   = ft.ButtonStyle(
                        bgcolor=C_ACCENT,
                        color=C_TEXT,
                        shape=ft.RoundedRectangleBorder(radius=12),
                        padding=ft.padding.Padding(left=22, right=22, top=13, bottom=13),
                        elevation=0,
                        overlay_color="#ffffff,0.094",
                    )
                    page.update()
            threading.Thread(target=_restore, daemon=True).start()

    view_settings = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.Icons.SETTINGS_ROUNDED, color=C_ACCENT, size=22),
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
            ant_key_wrap,
            gem_key_wrap,
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
        btn("💾 Salvar configurações", on_save_cfg, icon=ft.Icons.SAVE_ROUNDED, ref=save_cfg_btn_ref),
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
                            bgcolor=f"{C_ACCENT},0.133", border_radius=10,
                            width=22, height=22, alignment=ft.Alignment(0, 0),
                        ),
                        ft.Text(item, color=C_DIM, size=13, expand=True),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)
                    for i, item in enumerate(items)
                ], spacing=8),
            ], spacing=0),
            bgcolor=C_GLASS,
            border=_ball(1, C_BORDER),
            border_radius=16,
            padding=20,
            margin=ft.margin.Margin(left=0, right=0, top=0, bottom=10),
            shadow=ft.BoxShadow(spread_radius=0, blur_radius=20,
                                color="#000000,0.133", offset=ft.Offset(0, 4)),
        )

    view_help = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.Icons.HELP_OUTLINE_ROUNDED, color=C_ACCENT, size=22),
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
        indicator_color=f"{C_ACCENT},0.165",
        indicator_shape=ft.RoundedRectangleBorder(radius=12),
        leading=ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Text("N⚡A", size=15, weight=ft.FontWeight.W_800, color=C_ACCENT),
                    bgcolor=f"{C_ACCENT},0.133",
                    border=_ball(1, f"{C_ACCENT},0.267"),
                    border_radius=14,
                    padding=ft.padding.Padding(left=14, right=14, top=10, bottom=10),
                ),
                dim("Notion Anki", size=9, color=C_MUTED),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=5),
            padding=ft.padding.Padding(left=0, right=0, top=20, bottom=20),
        ),
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.SYNC_OUTLINED,
                selected_icon=ft.Icons.SYNC_ROUNDED,
                label="Sync",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
                selected_icon=ft.Icons.ACCOUNT_TREE_ROUNDED,
                label="Notion",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.TUNE_OUTLINED,
                selected_icon=ft.Icons.TUNE_ROUNDED,
                label="Config",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.HELP_OUTLINE_ROUNDED,
                selected_icon=ft.Icons.HELP_ROUNDED,
                label="Ajuda",
            ),
        ],
        on_change=on_nav,
    )

    sidebar = ft.Container(
        content=rail,
        border=_bonly(right=(1, C_BORDER)),
    )

    page.add(ft.Row([sidebar, content], expand=True, spacing=0))
    page.update()


if __name__ == "__main__":
    ft.run(main)
