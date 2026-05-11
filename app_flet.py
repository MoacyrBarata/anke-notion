#!/usr/bin/env python3
"""
app_flet.py — Interface Notion → Anki (Flet / Liquid Glass)
Rodar: python app_flet.py
"""

import os
import sys
import json
import time
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

from ui_components import (  # noqa: F401  (badge re-exported for tests)
    C_BG, C_GLASS, C_BORDER,
    C_ACCENT, C_ACCENT2, C_SUCCESS, C_WARNING, C_ERROR,
    C_TEXT, C_DIM, C_MUTED,
    _ball, _bonly,
    glass, h, dim, badge, btn, ghost_btn,
    field, dropdown, hint,
)

import db as sync_db
import updater

ROOT            = Path(__file__).parent
ENV_FILE        = ROOT / ".env"
NOTION_CFG_FILE = ROOT / "notion_config.json"
_NOTION_VERSION = "2025-09-03"


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
    if provider == "gemini":
        ok = key.startswith("AIza") and len(key) > 20
        return ok, "Formato válido" if ok else "Esperado: AIza..."
    if provider == "openai":
        # OpenAI keys: "sk-..." ou "sk-proj-..." — never "sk-ant-".
        ok = key.startswith("sk-") and not key.startswith("sk-ant-") and len(key) > 20
        return ok, "Formato válido" if ok else "Esperado: sk-... (OpenAI)"
    if provider == "groq":
        ok = key.startswith("gsk_") and len(key) > 20
        return ok, "Formato válido" if ok else "Esperado: gsk_..."
    return False, f"Provedor desconhecido: {provider}"


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
                ptype  = parent.get("type")
                if ptype in ("database_id", "data_source_id") and db_id:
                    if db_id not in db_map:
                        if p_title:
                            prog(f"🔎 Encontrado em \"{p_title}\", buscando tabela...")
                        try:
                            if ptype == "data_source_id":
                                db = _notion_get(token, f"/data_sources/{db_id}")
                                db_title = (db.get("title") or [{}])[0].get("plain_text", db_id[:8])
                                prog(f"✅ Tabela descoberta: {db_title}")
                                db_map[db_id] = db
                            else:
                                db = _notion_get(token, f"/databases/{db_id}")
                                for src in (db.get("data_sources") or []):
                                    if src["id"] in db_map:
                                        continue
                                    src_full = _notion_get(token, f"/data_sources/{src['id']}")
                                    src_title = (src_full.get("title") or [{}])[0].get("plain_text") or src.get("name") or src["id"][:8]
                                    prog(f"✅ Tabela descoberta: {src_title}")
                                    db_map[src["id"]] = src_full
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
    """db_id é data_source_id (Notion API 2025-09-03)."""
    if not token or not db_id:
        return {}
    try:
        return _notion_get(token, f"/data_sources/{db_id}").get("properties", {})
    except Exception:
        try:
            db = _notion_get(token, f"/databases/{db_id}")
            sources = db.get("data_sources") or []
            if sources:
                return _notion_get(token, f"/data_sources/{sources[0]['id']}").get("properties", {})
        except Exception:
            pass
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
    """db_id é data_source_id."""
    if not token or not db_id:
        return None
    try:
        r = requests.post(
            f"https://api.notion.com/v1/data_sources/{db_id}/query",
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


def parse_max_cards(raw) -> int | None:
    """Parse the per-table 'max flashcards' field. Empty / 0 / non-int → None
    (caller falls back to the global MAX_FLASHCARDS_POR_AULA)."""
    try:
        v = int(str(raw or "").strip())
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def default_content_dropdown_value(saved_value, hint_prop, sample_page) -> str:
    """Pick the initial value for the 'Coluna de conteúdo' dropdown.

    Priority:
    1. Previously saved choice (if any).
    2. The suggested rich_text column, ONLY when the sample page has text
       in it — otherwise the user's pattern is 'content lives in page
       blocks' and we default to '(nenhum)'.
    """
    if saved_value:
        return saved_value
    if hint_prop:
        sample_text = extract_prop_text(sample_page, hint_prop)
        if sample_text.strip():
            return hint_prop
    return "(nenhum)"


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
        tertiary_container="#0a1226",
        on_tertiary=C_TEXT,
        on_tertiary_container=C_TEXT,
        surface=C_BG,
        surface_tint=C_BG,
        surface_dim=C_BG,
        surface_bright="#0d1729",
        surface_container_lowest=C_BG,
        surface_container_low="#08111f",
        surface_container="#0a1426",
        surface_container_high="#0e1a30",
        surface_container_highest="#142441",
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
    page.window.min_width  = 480
    page.window.min_height = 600

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
        # Plano multi-DB: mapeamento por tabela (canonical keys) e tab ativa.
        setup_props_per_db={},
        setup_active_db_id=None,
    )

    # ── Shared setting fields ──────────────────────────────────────────────────
    f_token    = field("Notion Token",       cfg.get("NOTION_TOKEN", ""),          password=True, hint="secret_...")
    f_ant_key  = field("Anthropic API Key",  cfg.get("ANTHROPIC_API_KEY", ""),     password=True, hint="sk-ant-...")
    f_gem_key  = field("Gemini API Key",     cfg.get("GEMINI_API_KEY", ""),        password=True, hint="AIza...")
    f_oai_key  = field("OpenAI API Key",     cfg.get("OPENAI_API_KEY", ""),        password=True, hint="sk-...")
    f_groq_key = field("Groq API Key",       cfg.get("GROQ_API_KEY", ""),          password=True, hint="gsk_...")
    f_host     = field("Anki Host",          cfg.get("ANKI_HOST", "http://localhost:8765"))
    f_cards    = field("Máx. flashcards",    cfg.get("MAX_FLASHCARDS_POR_AULA", "10"), width=140)

    GEMINI_MODELS = [
        ("gemini-2.5-flash",          "gemini-2.5-flash · 🆓 Grátis (recomendado)"),
        ("gemini-2.5-flash-lite",     "gemini-2.5-flash-lite · 🆓 Grátis (mais rápido)"),
        ("gemini-flash-latest",       "gemini-flash-latest · 🆓 Grátis (alias 2.5)"),
        ("gemini-flash-lite-latest",  "gemini-flash-lite-latest · 🆓 Grátis (alias 2.5-lite)"),
        ("gemini-2.5-pro",            "gemini-2.5-pro · 💳 Pago (qualidade alta)"),
        ("gemini-pro-latest",         "gemini-pro-latest · 💳 Pago (alias 2.5-pro)"),
        ("gemini-2.0-flash",          "gemini-2.0-flash · ⚠️ Sem free tier em projetos novos"),
        ("gemini-2.0-flash-lite",     "gemini-2.0-flash-lite · ⚠️ Sem free tier em projetos novos"),
    ]
    gem_model_dd = dropdown(
        "Modelo Gemini",
        GEMINI_MODELS,
        value=cfg.get("GEMINI_MODEL", "gemini-2.5-flash"),
    )
    gem_model_hint = ft.Row([
        ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, color=C_DIM, size=13),
        ft.Text("🆓 = free tier ativo · 💳 = requer billing · ⚠️ = quota=0 em contas novas",
                color=C_DIM, size=11, expand=True),
    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    OPENAI_MODELS = [
        ("gpt-4o-mini",  "gpt-4o-mini · 💳 Pago (barato, recomendado)"),
        ("gpt-4o",       "gpt-4o · 💳 Pago (qualidade alta)"),
        ("gpt-4.1-mini", "gpt-4.1-mini · 💳 Pago"),
        ("gpt-4.1",      "gpt-4.1 · 💳 Pago"),
        ("o3-mini",      "o3-mini · 💳 Pago (raciocínio)"),
    ]
    oai_model_dd = dropdown(
        "Modelo OpenAI",
        OPENAI_MODELS,
        value=cfg.get("OPENAI_MODEL", "gpt-4o-mini"),
    )

    GROQ_MODELS = [
        ("llama-3.3-70b-versatile",        "llama-3.3-70b-versatile · 🆓 Grátis (recomendado)"),
        ("llama-3.1-8b-instant",           "llama-3.1-8b-instant · 🆓 Grátis (rápido)"),
        ("mixtral-8x7b-32768",             "mixtral-8x7b-32768 · 🆓 Grátis (contexto longo)"),
        ("deepseek-r1-distill-llama-70b",  "deepseek-r1-distill-llama-70b · 🆓 Grátis (raciocínio)"),
        ("gemma2-9b-it",                   "gemma2-9b-it · 🆓 Grátis (leve)"),
    ]
    groq_model_dd = dropdown(
        "Modelo Groq",
        GROQ_MODELS,
        value=cfg.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    )

    CLAUDE_MODELS = [
        ("claude-opus-4-5",   "claude-opus-4-5 · 💳 Pago (melhor)"),
        ("claude-sonnet-4-5", "claude-sonnet-4-5 · 💳 Pago (equilibrado)"),
        ("claude-haiku-4-5",  "claude-haiku-4-5 · 💳 Pago (rápido)"),
    ]
    claude_model_dd = dropdown(
        "Modelo Claude",
        CLAUDE_MODELS,
        value=cfg.get("CLAUDE_MODEL", "claude-opus-4-5"),
    )

    _init_provider = cfg.get("AI_PROVIDER", "gemini")

    def _key_wrap(field_widget, hint_text, *extras):
        return ft.Container(
            content=ft.Column([
                field_widget,
                ft.Container(height=4),
                ft.Row([
                    ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, color=C_DIM, size=13),
                    ft.Text(hint_text, color=C_DIM, size=11, expand=True),
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                *extras,
            ], spacing=2, tight=True),
            visible=False,
        )

    ant_key_wrap = _key_wrap(
        f_ant_key,
        "Claude é pago (Anthropic) · Sem free tier",
        ft.Container(height=8), claude_model_dd,
    )
    gem_key_wrap = _key_wrap(
        f_gem_key,
        "Gemini tem free tier nos modelos 2.5 (≈15 req/min, 1500/dia)",
        ft.Container(height=8), gem_model_dd,
        ft.Container(height=4), gem_model_hint,
    )
    oai_key_wrap = _key_wrap(
        f_oai_key,
        "OpenAI / ChatGPT é pago · gpt-4o-mini é barato e suficiente",
        ft.Container(height=8), oai_model_dd,
    )
    groq_key_wrap = _key_wrap(
        f_groq_key,
        "Groq tem free tier generoso · ~30 req/min · Llama 3.3 70B grátis",
        ft.Container(height=8), groq_model_dd,
    )

    _PROVIDER_WRAPS = {
        "claude": ant_key_wrap,
        "gemini": gem_key_wrap,
        "openai": oai_key_wrap,
        "groq":   groq_key_wrap,
    }
    # Show the wrap for the initial provider.
    if _init_provider in _PROVIDER_WRAPS:
        _PROVIDER_WRAPS[_init_provider].visible = True

    def _on_provider_change(e):
        chosen = e.control.value
        for name, wrap in _PROVIDER_WRAPS.items():
            wrap.visible = (name == chosen)
        page.update()

    # Free-only filter: hides paid providers when toggled on.
    _PROVIDER_TIERS = {
        "claude": "paid",
        "openai": "paid",
        "gemini": "free",
        "groq":   "free",
    }
    free_only_switch = ft.Switch(
        value=False,
        active_color=C_SUCCESS,
        label="🆓 Mostrar apenas opções grátis",
        label_text_style=ft.TextStyle(color=C_DIM, size=12),
    )

    def _prov_card(value, label, tag, tag_color, icon=None):
        return ft.Container(
            content=ft.Row([
                ft.Radio(value=value, fill_color=C_ACCENT),
                *( [ft.Icon(icon, color=tag_color, size=18)] if icon else [] ),
                ft.Column([
                    ft.Text(label, color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                    ft.Text(tag, color=tag_color, size=11),
                ], spacing=2, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=C_GLASS, border=_ball(1, C_BORDER),
            border_radius=10, padding=ft.padding.Padding(left=10, right=12, top=10, bottom=10),
            expand=True,
            data=value,  # used by free filter to find the card
        )

    card_claude = _prov_card("claude", "Claude",  "💳 Pago — Anthropic",     C_WARNING,
                             ft.Icons.AUTO_AWESOME_ROUNDED)
    card_gemini = _prov_card("gemini", "Gemini",  "🆓 Free tier — Google",   C_SUCCESS,
                             ft.Icons.STAR_ROUNDED)
    card_openai = _prov_card("openai", "ChatGPT", "💳 Pago — OpenAI",        C_WARNING,
                             ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED)
    card_groq   = _prov_card("groq",   "Groq",    "🆓 Free tier — Llama/Mixtral", C_SUCCESS,
                             ft.Icons.BOLT_ROUNDED)

    _PROVIDER_CARDS = {
        "claude": card_claude,
        "gemini": card_gemini,
        "openai": card_openai,
        "groq":   card_groq,
    }

    prov_radio = ft.RadioGroup(
        value=_init_provider,
        on_change=_on_provider_change,
        content=ft.Column([
            ft.Row([card_gemini, card_groq],   spacing=10),
            ft.Row([card_claude, card_openai], spacing=10),
        ], spacing=10),
    )

    def _on_free_filter_toggle(e=None):
        free_only = bool(free_only_switch.value)
        for name, card in _PROVIDER_CARDS.items():
            is_free = _PROVIDER_TIERS.get(name) == "free"
            card.visible = (is_free or not free_only)
        # If selected provider is hidden by filter, jump to first visible free one.
        if free_only and _PROVIDER_TIERS.get(prov_radio.value) == "paid":
            prov_radio.value = "gemini"
            for n, w in _PROVIDER_WRAPS.items():
                w.visible = (n == "gemini")
        page.update()
    free_only_switch.on_change = _on_free_filter_toggle

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

    # ────────────────────────────────────────────────────────────────────────
    # CRITICAL — Flet threading model
    # ────────────────────────────────────────────────────────────────────────
    # NEVER use `threading.Thread(...).start()` for background work in this
    # file. Flet desktop runs Flutter on its own executor; raw threads bypass
    # it, so control mutations are queued but never flushed until the user
    # generates a UI event (mouse move, click outside the window, resize).
    # Symptom: spinners stay spinning even after work finished.
    #
    # ALWAYS schedule work via `page.run_thread(handler)` — that registers the
    # handler with Flet's executor and dispatches updates correctly. See
    # AGENTS.md → "Armadilhas críticas para LLMs" for the full reasoning.
    # `tests/test_flet_views.py::test_no_raw_threading_thread_in_app_flet`
    # blocks regressions.
    #
    # `_safe_update` is the companion helper: at the end of every worker call
    # `_safe_update(c1, c2, ...)` listing every control whose state changed.
    # `.update()` on the individual control AND `page.update()` at the end
    # forces Flet to flush the message pipeline immediately.
    # ────────────────────────────────────────────────────────────────────────
    def _safe_update(*controls):
        for c in controls:
            if c is None:
                continue
            try:
                c.update()
            except Exception:
                pass
        try:
            page.update()
        except Exception:
            pass

    # ── Flashcard workshop animation ───────────────────────────────────────────
    _fc_shadow2 = ft.Container(
        bgcolor="#081127", border=_ball(1, f"{C_ACCENT},0.07"),
        border_radius=12, width=224, height=72, top=16, left=16,
    )
    _fc_shadow1 = ft.Container(
        bgcolor="#0c1a36", border=_ball(1, f"{C_ACCENT},0.12"),
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
        bgcolor="#142447", border=_ball(1, f"{C_ACCENT},0.35"),
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
        p = prov_radio.value or "gemini"
        key_map = {
            "claude": f_ant_key,
            "gemini": f_gem_key,
            "openai": f_oai_key,
            "groq":   f_groq_key,
        }
        return (key_map.get(p, f_gem_key).value or ""), p

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
            _safe_update(nc_card, ai_card, ak_card,
                         test_btn_ref.current if test_btn_ref.current else None)
        page.run_thread(work)

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
        _fc_front.bgcolor   = "#142447"
        _fc_front.border    = _ball(1, f"{C_ACCENT},0.35")
        _fc_shadow1.bgcolor = "#0c1a36"
        _fc_shadow2.bgcolor = "#081127"
        page.update()

        env = {
            "NOTION_TOKEN":            f_token.value,
            "AI_PROVIDER":             prov_radio.value,
            "ANTHROPIC_API_KEY":       f_ant_key.value,
            "CLAUDE_MODEL":            claude_model_dd.value,
            "GEMINI_API_KEY":          f_gem_key.value,
            "GEMINI_MODEL":            gem_model_dd.value,
            "OPENAI_API_KEY":          f_oai_key.value,
            "OPENAI_MODEL":            oai_model_dd.value,
            "GROQ_API_KEY":            f_groq_key.value,
            "GROQ_MODEL":              groq_model_dd.value,
            "ANKI_HOST":               f_host.value,
            "MAX_FLASHCARDS_POR_AULA": f_cards.value or "10",
        }

        def work():
            proc = run_sync(env)
            total_fc = 0
            line_n   = 0
            for line in proc.stdout:
                state["log_lines"].append(line)
                log_field.value = "".join(state["log_lines"][-100:])
                stripped = line.strip()
                if stripped and not stripped.startswith(("---", "===", "Flashcards")):
                    _fc_title.value = stripped[:46]
                if "Flashcards gerados" in line:
                    try:
                        total_fc = int(line.split(":")[-1].strip())
                        _fc_count.value = f"{total_fc} flashcard(s) criado(s)"
                    except ValueError:
                        pass
                # Throttle repaints: always on key events, else every 3 lines
                line_n += 1
                if "Flashcards" in line or line_n % 3 == 0:
                    _safe_update(log_field, _fc_title, _fc_count)
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
                _safe_update(_fc_front, _fc_shadow1, _fc_shadow2,
                             _fc_icon, _fc_title, _fc_count, result_text)
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
            _safe_update(stats_row, result_text, progress_bar,
                         _fc_front, _fc_shadow1, _fc_shadow2,
                         _fc_icon, _fc_title, _fc_count,
                         sync_btn_ref.current if sync_btn_ref.current else None)
        page.run_thread(work)

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

    def _reset_wizard(e=None):
        """Apaga notion_config.json e zera todo o estado do wizard."""
        try:
            if NOTION_CFG_FILE.exists():
                NOTION_CFG_FILE.unlink()
        except Exception as exc:
            snack(f"Falha ao remover config: {exc}", C_ERROR)
            return
        state.update({
            "setup_step":            1,
            "setup_mode":            "hierarchical",
            "setup_parent_db_id":    None,
            "setup_parent_db_name":  None,
            "setup_selected_dbs":    [],
            "setup_child_props":     None,
            "setup_props_per_db":    {},
            "setup_active_db_id":    None,
            "notion_dbs":            None,
            "notion_dbs_err":        None,
            "notion_loading":        False,
            "notion_db_checked":     None,
            "notion_db_expanded":    None,
        })
        snack("🔄 Configuração apagada. Comece pelo Passo 1.", C_ACCENT)
        rebuild()

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
        _safe_update(setup_col)

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
                            dim("Uma tabela-mãe lista as matérias/categorias e, dentro "
                                "de cada linha, há outra tabela embutida com as aulas. "
                                "Escolha este modo se suas anotações vivem aninhadas.",
                                size=12),
                            ft.Container(height=4),
                            ft.Text("Exemplo: tabela \"Disciplinas\" → linha \"Biologia\" → "
                                    "abre uma página com a tabela \"Aulas\" → cada aula "
                                    "vira flashcards.",
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
                            ft.Text("📋  Plano (várias tabelas)",
                                    color=C_TEXT, size=14, weight=ft.FontWeight.W_500),
                            dim("Você tem uma ou mais tabelas no mesmo nível — sem "
                                "tabela-mãe. Cada tabela representa uma matéria diferente "
                                "e cada linha dentro dela é uma anotação que vira "
                                "flashcards. Você seleciona TODAS as tabelas que quer "
                                "sincronizar de uma vez.",
                                size=12),
                            ft.Container(height=4),
                            ft.Text("Exemplo: tabela \"Matemática I\", tabela "
                                    "\"Direito Empresarial\", tabela \"Economia\" — "
                                    "cada linha de cada tabela é uma aula. Vira o deck "
                                    "Anki: Notion::Sync::Matemática I, Notion::Sync::"
                                    "Direito Empresarial, etc.",
                                    color=C_MUTED, size=11, italic=True),
                            ft.Container(height=4),
                            ft.Row([
                                ft.Icon(ft.Icons.LIGHTBULB_OUTLINE_ROUNDED,
                                        color=C_ACCENT, size=13),
                                ft.Text("No próximo passo você marca quantas tabelas "
                                        "quiser (multi-seleção).",
                                        color=C_ACCENT, size=11,
                                        weight=ft.FontWeight.W_500, expand=True),
                            ], spacing=6,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
                    ghost_btn("Reconfigurar", _reset_wizard),
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
                page.run_thread(load_dbs)

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
            h("Passo 2 — Selecionar tabelas (modo Plano)", size=15),
            dim(f"{len(dbs)} tabela(s) acessível(eis) à integração — "
                f"{n_sel} marcada(s).",
                size=12),
            ft.Container(height=4),
            ft.Row([
                ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, color=C_DIM, size=13),
                ft.Text("Marque uma tabela por matéria. Cada tabela vira um "
                        "subdeck no Anki (Notion::Sync::Nome-da-tabela). "
                        "Cada linha da tabela vira flashcards.",
                        color=C_DIM, size=11, expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Container(height=12),
            ft.Column(db_cards, spacing=8),
            ft.Container(height=16),
            ft.Row([ghost_btn("← Voltar", back2), btn("Próximo →", next2_flat)],
                   alignment=ft.MainAxisAlignment.END, spacing=10),
        ], spacing=0)))

    # ── Helpers Step 3: cards explicativos por campo ───────────────────────────
    def _explained_field(icon, function_label, control, role_tag,
                          explanation, example=None):
        """Field stylized as a card with role badge + explanation of usage."""
        body = [
            ft.Row([
                ft.Icon(icon, color=C_ACCENT, size=16),
                ft.Text(function_label, color=C_TEXT, size=13,
                        weight=ft.FontWeight.W_600, expand=True),
                ft.Container(
                    content=ft.Text(role_tag, color=C_BG, size=10,
                                    weight=ft.FontWeight.W_700),
                    bgcolor=C_ACCENT, border_radius=6,
                    padding=ft.padding.Padding(left=6, right=6, top=2, bottom=2),
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=6),
            control,
            ft.Container(height=6),
            ft.Row([
                ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, color=C_DIM, size=12),
                ft.Text(explanation, color=C_DIM, size=11, expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
        ]
        if example:
            body.append(ft.Row([
                ft.Icon(ft.Icons.LIGHTBULB_OUTLINE_ROUNDED, color=C_MUTED, size=12),
                ft.Text(example, color=C_MUTED, size=11,
                        italic=True, expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START))
        return ft.Container(
            content=ft.Column(body, spacing=0, tight=True),
            bgcolor=C_GLASS, border=_ball(1, C_BORDER),
            border_radius=12, padding=12,
            margin=ft.margin.Margin(left=0, right=0, top=0, bottom=8),
        )

    # ── Step 3: Field mapping ──────────────────────────────────────────────────
    def _build_step3(ctrls, token):
        mode    = state["setup_mode"]
        sel_dbs = state.get("setup_selected_dbs") or [
            {"id": state["setup_parent_db_id"],
             "name": state["setup_parent_db_name"] or ""}
        ]

        # Active-DB tracker (modo plano com várias tabelas).
        per_db = state.setdefault("setup_props_per_db", {})
        sel_ids = [d["id"] for d in sel_dbs]
        active_id = state.get("setup_active_db_id")
        if active_id not in sel_ids:
            active_id = sel_ids[0]
            state["setup_active_db_id"] = active_id
        active = next(d for d in sel_dbs if d["id"] == active_id)
        db_id, db_name = active["id"], active["name"]

        props  = get_database_properties(token, db_id)
        sample = get_sample_page(token, db_id)

        if not props:
            ctrls.append(glass(ft.Row([
                ft.Icon(ft.Icons.ERROR_ROUNDED, color=C_ERROR),
                ft.Text(f"Não foi possível buscar propriedades de \"{db_name}\". "
                        f"Verifique o token e o acesso da integração.",
                        color=C_ERROR, size=13),
            ], spacing=8)))
            return

        hints   = suggest_fields(props)
        saved   = per_db.get(db_id, {})
        t_opts  = props_by_type(props, "title") or list(props.keys())
        tx_opts = ["(nenhum)"] + props_by_type(props, "rich_text")
        d_opts  = ["(nenhum)"] + props_by_type(props, "date",
                                                "created_time", "last_edited_time")
        s_opts  = ["(nenhum)"] + props_by_type(props, "select", "status")
        tx_all  = ["(nenhum)"] + props_by_type(props, "rich_text", "title")

        def _v(saved_key, hint_key=None, default="(nenhum)"):
            v = saved.get(saved_key)
            if v:
                return v
            if hint_key:
                return hints.get(hint_key, default)
            return default

        def _select_options(prop_name: str | None) -> list[str]:
            """Lista de opções de um campo select/status do Notion."""
            if not prop_name or prop_name == "(nenhum)":
                return []
            p = props.get(prop_name) or {}
            ptype = p.get("type", "")
            if ptype not in ("select", "status"):
                return []
            return [o["name"] for o in (p.get(ptype) or {}).get("options", [])]

        dd_title    = dropdown("Campo título",
            t_opts, _v("parent_name_prop", "title"))

        # Content default: prefer the suggested rich_text column ONLY if the
        # sample page actually has text in it. New Notion pattern: cells are
        # empty and content lives in page blocks — default to (nenhum) so the
        # pipeline reads directly from the page.
        _content_default = default_content_dropdown_value(
            saved.get("child_content_prop"), hints.get("content"), sample)
        dd_content  = dropdown("Campo conteúdo (texto/resumo)",
            tx_opts, _content_default)
        dd_date     = dropdown("Campo data (opcional)",
            d_opts, _v("child_date_prop", "date"))
        dd_child_t  = dropdown("Campo título do item filho",
            tx_all, _v("child_title_prop")) if mode == "hierarchical" else None
        f_kw        = field("Palavra-chave do DB filho",
            saved.get("child_db_keyword") or "Aulas") if mode == "hierarchical" else None

        # Sync prop + value — value field is ALWAYS a Dropdown so it reflects
        # the actual options of the selected Notion column. When no prop is
        # picked yet (or the prop has no options), the dropdown still shows
        # the saved/default value as a single fallback entry.
        _initial_sync_p = _v("child_sync_prop", "sync")
        sync_opts_list  = _select_options(_initial_sync_p)
        _sync_default   = saved.get("child_sync_done") or "✅ Sincronizado"
        _sync_display   = sync_opts_list if sync_opts_list else [_sync_default]
        if _sync_default not in _sync_display:
            _sync_default = _sync_display[0]
        f_sync_done = dropdown("Valor = sincronizado",
                               _sync_display, _sync_default)

        # Status prop + value — same treatment as sync.
        _initial_status_p = _v("child_status_prop", "status")
        status_opts_list  = _select_options(_initial_status_p)
        _status_default   = saved.get("child_status_complete") or "✅ Completa"
        _status_display   = status_opts_list if status_opts_list else [_status_default]
        if _status_default not in _status_display:
            _status_default = _status_display[0]
        f_status_v = dropdown("Valor = pronto",
                              _status_display, _status_default)

        # Forward declaration so on_change pode capturar/rebuildar.
        def _refresh_after_prop_change(e=None):
            # Captura tudo ANTES de rebuildar pra não perder edição em curso.
            _persist_active()
            rebuild()

        dd_sync_p   = dropdown("Campo sync (opcional)",
            s_opts, _initial_sync_p)
        dd_status_p = dropdown("Campo status (filtro, opcional)",
            s_opts, _initial_status_p)

        def _refresh_value_options(value_dd, prop_dd, fallback_default: str):
            """Repopulate the value dropdown's options based on the property
            currently selected in `prop_dd`. Runs synchronously before any
            rebuild so the user sees the new options immediately."""
            new_opts = _select_options(prop_dd.value)
            if new_opts:
                if value_dd.value in new_opts:
                    chosen = value_dd.value
                else:
                    chosen = new_opts[0]
                display = new_opts
            else:
                chosen  = value_dd.value or fallback_default
                display = [chosen]
            value_dd.options = [ft.dropdown.Option(key=o, text=o) for o in display]
            value_dd.value   = chosen
            try:
                value_dd.update()
            except Exception:
                pass  # widget not yet attached to page — rebuild will handle it

        def _on_sync_prop_change(e=None):
            _refresh_value_options(f_sync_done, dd_sync_p, "✅ Sincronizado")
            _refresh_after_prop_change()

        def _on_status_prop_change(e=None):
            _refresh_value_options(f_status_v, dd_status_p, "✅ Completa")
            _refresh_after_prop_change()

        dd_sync_p.on_change   = _on_sync_prop_change
        dd_status_p.on_change = _on_status_prop_change

        use_sync    = ft.Switch(
            value=saved.get("use_sync_field", True),
            active_color=C_ACCENT, label="Usar campo de sync no Notion",
            label_text_style=ft.TextStyle(color=C_DIM, size=13),
        )

        _global_max = int(load_cfg().get("MAX_FLASHCARDS_POR_AULA", "10") or 10)
        _max_default = saved.get("max_cards") or _global_max
        f_max_cards = field(
            f"Máximo de flashcards por item (global: {_global_max})",
            value=str(_max_default),
            hint=f"Deixe em branco para usar o global ({_global_max}).",
        )

        # ── Preview card ───────────────────────────────────────────────────────
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
                        ft.Text(s_title,       color=C_TEXT,  size=12,
                                weight=ft.FontWeight.W_500)], spacing=4),
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
            bgcolor="#0c1a2e",
            border=_ball(1, C_BORDER),
            border_radius=18,
            padding=14,
        )

        # ── Save / nav handlers ────────────────────────────────────────────────
        def _capture_active() -> dict:
            """Snapshot dos dropdowns/fields para o DB ativo (canonical keys)."""
            return {
                "parent_name_prop":      dd_title.value,
                "child_db_keyword":      f_kw.value if f_kw else "",
                "child_title_prop":      dd_child_t.value if dd_child_t else dd_title.value,
                "child_content_prop":    None if dd_content.value == "(nenhum)" else dd_content.value,
                "child_date_prop":       None if dd_date.value    == "(nenhum)" else dd_date.value,
                "use_sync_field":        bool(use_sync.value),
                "child_sync_prop":       None if dd_sync_p.value  == "(nenhum)" else dd_sync_p.value,
                "child_sync_done":       f_sync_done.value or "✅ Sincronizado",
                "child_status_prop":     None if dd_status_p.value == "(nenhum)" else dd_status_p.value,
                "child_status_complete": f_status_v.value or None,
                "max_cards":             parse_max_cards(f_max_cards.value),
            }

        def _persist_active():
            per_db[db_id] = _capture_active()

        def _switch_to(new_id):
            _persist_active()
            state["setup_active_db_id"] = new_id
            rebuild()

        def back3(e):
            _persist_active()
            state["setup_step"] = 2; rebuild()

        def next3(e):
            _persist_active()
            # Garante que TODAS as tabelas selecionadas têm mapeamento.
            unconfigured = [d for d in sel_dbs if d["id"] not in per_db]
            if unconfigured and mode == "flat":
                names = ", ".join(d["name"] for d in unconfigured[:3])
                snack(f"Configure também: {names}"
                      f"{'…' if len(unconfigured) > 3 else ''}",
                      C_WARNING)
                state["setup_active_db_id"] = unconfigured[0]["id"]
                rebuild()
                return
            # Mantém setup_child_props (legacy) com props do DB ativo.
            active_props = per_db[db_id]
            state["setup_child_props"] = {
                "parent_title_prop": active_props["parent_name_prop"],
                "child_keyword":     active_props["child_db_keyword"],
                "child_title":       active_props["child_title_prop"],
                "content_prop":      active_props["child_content_prop"],
                "date_prop":         active_props["child_date_prop"],
                "use_sync":          active_props["use_sync_field"],
                "sync_prop":         active_props["child_sync_prop"],
                "sync_done":         active_props["child_sync_done"],
                "status_prop":       active_props["child_status_prop"],
                "status_val":        active_props["child_status_complete"],
                "max_cards":         active_props.get("max_cards"),
            }
            state["setup_step"] = 4; rebuild()

        # ── Badge bar (modo plano com várias tabelas) ──────────────────────────
        ctrls.append(preview_card)
        ctrls.append(ft.Container(height=10))

        if mode == "flat" and len(sel_dbs) > 1:
            done_count = sum(1 for d in sel_dbs if d["id"] in per_db)
            ctrls.append(glass(ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.LAYERS_OUTLINED, color=C_ACCENT, size=18),
                    ft.Text("Mapeie cada tabela individualmente",
                            color=C_TEXT, size=14, weight=ft.FontWeight.W_600,
                            expand=True),
                    ft.Text(f"{done_count}/{len(sel_dbs)} configuradas",
                            color=C_DIM, size=12),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Container(height=4),
                dim("Clique em uma badge para ajustar os campos daquela tabela. "
                    "Mudanças são salvas automaticamente ao trocar de tabela ou "
                    "avançar.", size=11),
                ft.Container(height=10),
                ft.Row([
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE_ROUNDED if d["id"] in per_db
                                else ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED,
                                color=(C_BG if d["id"] == active_id
                                       else (C_SUCCESS if d["id"] in per_db
                                             else C_DIM)),
                                size=14),
                            ft.Text(d["name"],
                                    color=(C_BG if d["id"] == active_id
                                           else (C_TEXT if d["id"] in per_db
                                                 else C_DIM)),
                                    size=12, weight=ft.FontWeight.W_600),
                        ], spacing=6,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=(C_ACCENT if d["id"] == active_id
                                 else (f"{C_SUCCESS},0.08" if d["id"] in per_db
                                       else C_GLASS)),
                        border=_ball(
                            2 if d["id"] == active_id else 1,
                            (C_ACCENT if d["id"] == active_id
                             else (f"{C_SUCCESS},0.4" if d["id"] in per_db
                                   else C_BORDER))),
                        border_radius=20,
                        padding=ft.padding.Padding(left=12, right=14, top=7, bottom=7),
                        on_click=lambda e, did=d["id"]: _switch_to(did),
                    )
                    for d in sel_dbs
                ], spacing=8, wrap=True),
            ], spacing=0)))
            ctrls.append(ft.Container(height=10))

        # ── Cards explicativos por campo ───────────────────────────────────────
        ctrls.append(glass(ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.TABLE_VIEW_ROUNDED, color=C_ACCENT, size=18),
                h(f"Mapeamento — {db_name}", size=15),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            dim("Cada campo abaixo informa qual coluna do Notion alimenta uma "
                "função específica do flashcard ou da sincronização.",
                size=12),
        ], spacing=4)))
        ctrls.append(ft.Container(height=10))

        # Identificação
        ctrls.append(_explained_field(
            ft.Icons.TITLE_ROUNDED, "Coluna de título", dd_title,
            "TÍTULO",
            "Identifica cada linha. Vai aparecer como título do flashcard "
            "no Anki e nas tags. Use a coluna do tipo 'title' do Notion.",
            "Ex: 'Aula', 'Tópico', 'Nome'."
        ))

        if mode == "hierarchical":
            ctrls.append(_explained_field(
                ft.Icons.SEARCH_ROUNDED, "Palavra-chave do DB filho", f_kw,
                "BUSCA",
                "Trecho do nome da tabela aninhada (dentro de cada matéria) "
                "que contém as aulas. O app procura blocos child_database cujo "
                "título contenha essa palavra.",
                "Ex: 'Aulas', 'Anotações', 'Diário'."
            ))
            ctrls.append(_explained_field(
                ft.Icons.SUBJECT_ROUNDED, "Campo título do item filho", dd_child_t,
                "TÍTULO FILHO",
                "Coluna na tabela aninhada que dá nome a cada aula/item — "
                "vira o título do flashcard.",
                None
            ))

        # Conteúdo
        ctrls.append(_explained_field(
            ft.Icons.NOTES_ROUNDED, "Coluna de conteúdo", dd_content,
            "MATÉRIA-PRIMA",
            "Define DE ONDE a IA puxa o texto de cada linha:\n"
            "• (nenhum) → app lê a PÁGINA INTEIRA apontada pela linha "
            "(títulos, parágrafos, listas, tabelas, to-dos, etc.). "
            "Use quando o conteúdo está dentro da página, não numa célula.\n"
            "• <coluna rich_text> → app lê SÓ aquela célula. "
            "Use quando a célula contém o resumo pronto.\n"
            "Se preencher ambos (coluna + página tiver conteúdo), o app "
            "concatena os dois.",
            "Ex: 'Conteúdo', 'Resumo', 'Anotações' — ou (nenhum) para usar a página."
        ))
        ctrls.append(_explained_field(
            ft.Icons.CALENDAR_TODAY_ROUNDED, "Coluna de data (opcional)", dd_date,
            "DATA",
            "Aparece como tag 'data:YYYY-MM-DD' no Anki e no rodapé do "
            "flashcard. Útil para ordenar revisões por data da aula.",
            "Deixe em (nenhum) se sua tabela não tem essa informação."
        ))

        # Controle de sincronização
        ctrls.append(ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SYNC_ALT_ROUNDED, color=C_DIM, size=14),
                ft.Text("Controle de re-sincronização (opcional)",
                        color=C_DIM, size=12, weight=ft.FontWeight.W_500,
                        expand=True),
                use_sync,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.Padding(left=4, right=4, top=8, bottom=4),
        ))
        ctrls.append(_explained_field(
            ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
            "Campo sync (Notion)", dd_sync_p,
            "MARCA",
            "Coluna select onde o app vai gravar o status após sincronizar "
            "com sucesso. Funciona junto com o histórico SQLite local. "
            "Deixe em (nenhum) se prefere usar APENAS o histórico local.",
            "Ex: select 'Sincronização' com opção '✅ Sincronizado'."
        ))
        _sync_value_hint = (
            "Escolha qual opção do select será gravada quando o item for "
            "sincronizado. As opções vêm direto do campo selecionado acima."
            if sync_opts_list
            else "Selecione um campo sync acima para escolher entre as opções "
                 "existentes na sua tabela do Notion."
        )
        ctrls.append(_explained_field(
            ft.Icons.LABEL_OUTLINE_ROUNDED,
            "Valor que indica 'sincronizado'", f_sync_done,
            "VALOR-OK",
            _sync_value_hint,
            None
        ))
        ctrls.append(_explained_field(
            ft.Icons.FILTER_LIST_ROUNDED,
            "Campo status (filtro)", dd_status_p,
            "FILTRO",
            "Restringe o sync a linhas cujo status seja o valor abaixo. "
            "Ideal para ignorar rascunhos. Deixe em (nenhum) para processar "
            "todas as linhas pendentes.",
            "Ex: select 'Status' com opção '✅ Completa'."
        ))
        _status_value_hint = (
            "Apenas linhas cujo status seja igual a esta opção serão "
            "processadas. As opções vêm direto do campo selecionado acima."
            if status_opts_list
            else "Selecione um campo status acima para escolher entre as opções "
                 "existentes na sua tabela do Notion."
        )
        ctrls.append(_explained_field(
            ft.Icons.RULE_ROUNDED,
            "Valor = pronto para sincronizar", f_status_v,
            "VALOR-FILTRO",
            _status_value_hint,
            None
        ))
        ctrls.append(_explained_field(
            ft.Icons.STYLE_ROUNDED,
            "Máx. flashcards por item", f_max_cards,
            "ORÇAMENTO",
            "Teto de cards que a IA pode gerar para cada linha desta tabela. "
            f"Sobrescreve o global ({_global_max}). Útil para matérias densas "
            "(mais cards) ou tabelas de datas-chave (menos cards).",
            "Deixe vazio (ou 0) para herdar o global."
        ))

        ctrls.append(ft.Container(height=8))
        ctrls.append(ft.Row([
            ghost_btn("← Voltar", back3),
            btn("Próximo →", next3),
        ], alignment=ft.MainAxisAlignment.END, spacing=10))

    # ── Step 4: Anki deck ──────────────────────────────────────────────────────
    def _build_step4(ctrls):
        existing = load_notion_config()
        f_deck   = field("Deck raiz no Anki",
                         existing.get("anki_deck_root", "Notion::Sync") if existing else "Notion::Sync")
        p    = state.get("setup_child_props") or {}
        mode = state["setup_mode"]

        sel_dbs   = state.get("setup_selected_dbs") or [{"name": state.get("setup_parent_db_name", "")}]
        dbs_label = ", ".join(d["name"] for d in sel_dbs) if sel_dbs else ""
        _global_max = int(load_cfg().get("MAX_FLASHCARDS_POR_AULA", "10") or 10)
        _max_summary = p.get("max_cards")
        _max_label = (f"{_max_summary} (sobrescreve)" if _max_summary
                      else f"{_global_max} (global)")
        summary = [
            ("Modo",           "Hierárquico" if mode == "hierarchical" else "Plano"),
            ("Tabela(s)",      dbs_label),
            ("Campo título",   p.get("parent_title_prop", "")),
            ("Conteúdo",       p.get("content_prop") or "(blocos da página)"),
            ("Controle sync",  "campo Notion" if p.get("use_sync") else "timestamp"),
            ("Máx. cards",     _max_label),
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
            per_db = state.get("setup_props_per_db") or {}
            # Embute mapeamento por tabela em cada selected_dbs[].props
            sel_dbs_with_props = [
                {**d, "props": per_db[d["id"]]} if d.get("id") in per_db else d
                for d in (state.get("setup_selected_dbs") or [])
            ]
            save_notion_config({
                "version":               2,
                "mode":                  state["setup_mode"],
                "parent_db_id":          state["setup_parent_db_id"],
                "parent_db_name":        state["setup_parent_db_name"],
                "selected_dbs":          sel_dbs_with_props,
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
                "max_cards":             p.get("max_cards"),
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
            "CLAUDE_MODEL":            claude_model_dd.value,
            "GEMINI_API_KEY":          f_gem_key.value,
            "GEMINI_MODEL":            gem_model_dd.value,
            "OPENAI_API_KEY":          f_oai_key.value,
            "OPENAI_MODEL":            oai_model_dd.value,
            "GROQ_API_KEY":            f_groq_key.value,
            "GROQ_MODEL":              groq_model_dd.value,
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
            _safe_update(b)

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
                    _safe_update(b)
            page.run_thread(_restore)

    # ── Update banner (populated by background check) ──────────────────────────
    upd_state = {"checking": False, "info": None, "applying": False}
    upd_card  = ft.Container(visible=False)
    upd_btn_ref = ft.Ref[ft.OutlinedButton]()
    upd_restart_ref = ft.Ref[ft.FilledButton]()
    upd_msg     = ft.Text("", color=C_DIM, size=12, selectable=True)
    upd_status  = ft.Text("", color=C_DIM, size=11, italic=True)

    def _restart_app(e=None):
        try:
            updater.restart_app()
        except Exception as exc:
            snack(f"Falha ao reiniciar: {exc}", C_ERROR)

    def _set_upd_banner(info: dict | None):
        upd_state["info"] = info
        if not info:
            upd_card.visible = False
            _safe_update(upd_card)
            return
        if info.get("error"):
            upd_card.visible = False
            _safe_update(upd_card)
            return
        if not info.get("has_update"):
            upd_card.visible = True
            upd_card.bgcolor = f"{C_SUCCESS},0.08"
            upd_card.border  = _ball(1, f"{C_SUCCESS},0.25")
            upd_msg.value    = f"✓ Você está na versão mais recente ({info['current']})."
            if upd_btn_ref.current:
                upd_btn_ref.current.visible = False
            _safe_update(upd_card, upd_msg, upd_btn_ref.current)
            return
        upd_card.visible = True
        upd_card.bgcolor = f"{C_ACCENT},0.10"
        upd_card.border  = _ball(1, f"{C_ACCENT},0.35")
        notes = (info.get("remote") or {}).get("notes", "") or ""
        if len(notes) > 140:
            notes = notes[:140].rstrip() + "…"
        upd_msg.value = (f"🚀 Nova versão disponível: {info['latest']} "
                         f"(você tem {info['current']}).\n{notes}").strip()
        if upd_btn_ref.current:
            upd_btn_ref.current.visible = True
        _safe_update(upd_card, upd_msg, upd_btn_ref.current)

    def _check_updates_async(e=None):
        if upd_state["checking"]:
            return
        upd_state["checking"] = True
        upd_status.value = "Verificando atualizações..."
        upd_card.visible = True
        upd_card.bgcolor = C_GLASS
        upd_card.border  = _ball(1, C_BORDER)
        upd_msg.value    = ""
        if upd_btn_ref.current:
            upd_btn_ref.current.visible = False
        _safe_update(upd_card, upd_status, upd_msg, upd_btn_ref.current)

        def work():
            try:
                info = updater.check_for_update()
            except Exception as exc:
                info = {"current": updater.get_current_version(), "latest": None,
                        "has_update": False, "error": str(exc)}
            upd_state["checking"] = False
            upd_status.value = ""
            _set_upd_banner(info)
        page.run_thread(work)

    def _apply_update(e=None):
        if upd_state["applying"]:
            return
        upd_state["applying"] = True
        upd_status.value = "Baixando e aplicando atualização..."
        if upd_btn_ref.current:
            upd_btn_ref.current.disabled = True
        _safe_update(upd_status, upd_btn_ref.current)

        def work():
            try:
                ok, msg = updater.apply_update("auto")
            except Exception as exc:
                ok, msg = False, f"Erro inesperado: {exc}"
            upd_state["applying"] = False
            if ok:
                upd_status.value = ""
                upd_card.bgcolor = f"{C_SUCCESS},0.10"
                upd_card.border  = _ball(1, f"{C_SUCCESS},0.35")
                upd_msg.value    = (f"✅ Atualização aplicada ({msg}).\n"
                                    f"Reinicie o app para carregar o novo código.")
                if upd_btn_ref.current:
                    upd_btn_ref.current.visible = False
                if upd_restart_ref.current:
                    upd_restart_ref.current.visible = True
                snack("✅ Atualização aplicada — clique em Reiniciar.", C_SUCCESS)
            else:
                upd_status.value = ""
                upd_card.bgcolor = f"{C_ERROR},0.10"
                upd_card.border  = _ball(1, f"{C_ERROR},0.35")
                upd_msg.value    = f"⚠ Falha: {msg}"
                if upd_btn_ref.current:
                    upd_btn_ref.current.disabled = False
                snack(f"Falha ao atualizar: {msg}", C_ERROR)
            _safe_update(upd_card, upd_msg, upd_status,
                         upd_btn_ref.current, upd_restart_ref.current)
        page.run_thread(work)

    upd_card = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.SYSTEM_UPDATE_ROUNDED, color=C_ACCENT, size=18),
                ft.Text("Atualizações", color=C_TEXT, size=13,
                        weight=ft.FontWeight.W_600),
                ft.Container(expand=True),
                ft.OutlinedButton(
                    "Atualizar agora",
                    icon=ft.Icons.DOWNLOAD_ROUNDED,
                    on_click=_apply_update,
                    ref=upd_btn_ref,
                    visible=False,
                    style=ft.ButtonStyle(
                        color=C_ACCENT,
                        side=ft.BorderSide(1, f"{C_ACCENT},0.5"),
                        shape=ft.RoundedRectangleBorder(radius=10),
                    ),
                ),
                ft.FilledButton(
                    "Reiniciar agora",
                    icon=ft.Icons.RESTART_ALT_ROUNDED,
                    on_click=_restart_app,
                    ref=upd_restart_ref,
                    visible=False,
                    style=ft.ButtonStyle(
                        bgcolor=C_SUCCESS,
                        color=C_TEXT,
                        shape=ft.RoundedRectangleBorder(radius=10),
                    ),
                ),
                ghost_btn("↻ Verificar", _check_updates_async),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=4),
            upd_msg,
            upd_status,
        ], spacing=4),
        bgcolor=C_GLASS, border=_ball(1, C_BORDER),
        border_radius=14, padding=14, visible=False,
    )

    view_settings = ft.Column([
        glass(ft.Column([
            ft.Row([ft.Icon(ft.Icons.SETTINGS_ROUNDED, color=C_ACCENT, size=22),
                    h("Configurações"),
                    ft.Container(expand=True),
                    ft.Text(f"v{updater.get_current_version()}",
                            color=C_DIM, size=11)],
                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            dim("Chaves de API e preferências de sincronização."),
        ], spacing=6)),
        ft.Container(height=12),
        upd_card,
        ft.Container(height=12),
        glass(ft.Column([
            dim("NOTION", size=10, color=C_MUTED),
            ft.Container(height=6),
            f_token,
        ], spacing=4)),
        ft.Container(height=10),
        glass(ft.Column([
            ft.Row([
                ft.Text("INTELIGÊNCIA ARTIFICIAL", size=10, color=C_MUTED,
                        expand=True),
                free_only_switch,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=8),
            prov_radio,
            ft.Container(height=8),
            ant_key_wrap,
            gem_key_wrap,
            oai_key_wrap,
            groq_key_wrap,
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
        help_block("5. Como o app decide o que sincronizar", [
            "Camada 1 — coluna 'Sincronização' no Notion: linhas com valor ✅ "
            "Sincronizado são ignoradas. Esta é a fonte primária.",
            "Camada 2 — banco SQLite local (sync_history.db): registra cada "
            "item processado e bloqueia re-envio de itens já sincronizados "
            "com sucesso. Funciona como segurança extra contra falhas de "
            "rede, duplicatas e perda do estado da coluna no Notion.",
            "AMBAS as camadas precisam liberar para uma linha ser enviada "
            "à IA. Linha nova passa nas duas; linha já sincronizada é "
            "bloqueada por qualquer uma.",
            "Para forçar re-sync de UM item: aba Histórico → botão ↻ Re-sync "
            "(apaga registro local) E mude o valor da coluna Sincronização "
            "no Notion para algo diferente de ✅ Sincronizado.",
            "Para forçar re-sync de TUDO: aba Histórico → 🗑 Apagar "
            "histórico (zera SQLite) E desmarque a coluna Sincronização das "
            "linhas no Notion.",
            "Falha (quota IA, rede etc.): linha vira ❌ Erro no Notion e "
            "fica registrada como 'error' no SQLite — próximo run re-tenta "
            "automaticamente, sem ação manual.",
        ]),
        ft.Container(height=16),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ══════════════════════════════════════════════════════════════════════════
    # VIEW 4 — HISTÓRICO (SQLite)
    # ══════════════════════════════════════════════════════════════════════════

    history_status_filter = {"value": None}  # None | 'success' | 'error' | 'skipped'
    history_list_col      = ft.Column(controls=[], spacing=8)
    history_stats_row     = ft.Row(controls=[], spacing=10)

    def _stat_card(label, value, color=C_TEXT):
        return ft.Container(
            content=ft.Column([
                ft.Text(str(value), color=color, size=22, weight=ft.FontWeight.W_700),
                ft.Text(label, color=C_DIM, size=11),
            ], spacing=2),
            bgcolor=C_GLASS, border=_ball(1, C_BORDER),
            border_radius=12, padding=14, expand=True,
        )

    def _history_row(item):
        st     = item.get("status", "")
        if st == "success":
            color, ic = C_SUCCESS, ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED
        elif st == "error":
            color, ic = C_ERROR, ft.Icons.ERROR_OUTLINE_ROUNDED
        else:
            color, ic = C_WARNING, ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED

        when = (item.get("synced_at") or "")[:19].replace("T", " ")
        title = item.get("title") or item.get("page_id", "")[:8]
        cards = f"{item.get('cards_inserted', 0)}/{item.get('cards_generated', 0)} cards"
        meta_bits = [when, item.get("category") or item.get("db_name") or "", cards]
        if item.get("retry_count"):
            meta_bits.append(f"retry #{item['retry_count']}")
        meta = " · ".join(b for b in meta_bits if b)

        def _resync(e, pid=item["page_id"]):
            n = sync_db.mark_pending(pid)
            snack(f"🔄 {n} registro(s) removido(s). Item será re-sincronizado.", C_ACCENT)
            _refresh_history()

        body = [
            ft.Row([
                ft.Icon(ic, color=color, size=16),
                ft.Text(title, color=C_TEXT, size=13, weight=ft.FontWeight.W_500,
                        expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ghost_btn("↻ Re-sync", _resync),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(meta, color=C_DIM, size=11),
        ]
        if item.get("error_msg"):
            body.append(ft.Text(item["error_msg"], color=C_ERROR, size=11, italic=True,
                                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS))

        return ft.Container(
            content=ft.Column(body, spacing=4),
            bgcolor=C_GLASS, border=_ball(1, C_BORDER),
            border_radius=12, padding=12,
        )

    def _refresh_history(e=None):
        try:
            sync_db.init_db()
            stats = sync_db.get_stats(days=30)
            rows  = sync_db.list_history(limit=80,
                                         status=history_status_filter["value"])
        except Exception as exc:
            history_stats_row.controls = [
                ft.Text(f"⚠ Falha ao ler banco local: {exc}", color=C_ERROR, size=12),
            ]
            history_list_col.controls = []
            page.update()
            return

        history_stats_row.controls = [
            _stat_card("Runs (30d)",     stats["runs"]),
            _stat_card("Itens (30d)",    stats["attempts"]),
            _stat_card("Sucessos (30d)", stats["successes"], C_SUCCESS),
            _stat_card("Erros (30d)",    stats["errors"], C_ERROR if stats["errors"] else C_TEXT),
            _stat_card("Cards no Anki",  stats["cards"], C_ACCENT),
        ]
        if not rows:
            history_list_col.controls = [
                ft.Container(
                    content=ft.Text("Sem histórico ainda. Rode uma sincronização.",
                                    color=C_DIM, size=12, italic=True),
                    padding=20, alignment=ft.Alignment(0, 0),
                ),
            ]
        else:
            history_list_col.controls = [_history_row(r) for r in rows]
        page.update()

    def _set_filter(value):
        def handler(e):
            history_status_filter["value"] = value
            _refresh_history()
        return handler

    def _wipe_history(e=None):
        n = sync_db.mark_all_pending()
        snack(f"🗑 Histórico apagado ({n} registros). Próxima sync re-processa tudo.",
              C_WARNING)
        _refresh_history()

    view_history = ft.Column([
        glass(ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.HISTORY_ROUNDED, color=C_ACCENT, size=22),
                h("Histórico de sincronizações"),
                ft.Container(expand=True),
                ghost_btn("↻ Atualizar", _refresh_history),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
            dim("Banco SQLite local — independente do Notion. "
                "Funciona em Windows, Linux e macOS."),
        ], spacing=6)),
        ft.Container(height=12),
        history_stats_row,
        ft.Container(height=12),
        ft.Row([
            ghost_btn("Todos",     _set_filter(None)),
            ghost_btn("✓ Sucesso", _set_filter("success")),
            ghost_btn("✗ Erro",    _set_filter("error")),
            ghost_btn("⤼ Pulado",  _set_filter("skipped")),
            ft.Container(expand=True),
            ghost_btn("🗑 Apagar histórico", _wipe_history),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(height=12),
        history_list_col,
        ft.Container(height=20),
    ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # Pre-populate before first render so the view is never blank.
    _refresh_history()

    # ══════════════════════════════════════════════════════════════════════════
    # LAYOUT — Rail + Content
    # ══════════════════════════════════════════════════════════════════════════

    views = [view_sync, setup_col, view_history, view_settings, view_help]

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
        elif idx == 2:
            _refresh_history()
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
                icon=ft.Icons.HISTORY_ROUNDED,
                selected_icon=ft.Icons.HISTORY_ROUNDED,
                label="Histórico",
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

    NARROW_BP = 720
    state["layout_mode"] = "wide"

    def _apply_layout(width):
        try:
            w = float(width or 0)
        except (TypeError, ValueError):
            w = 0.0
        narrow = 0 < w < NARROW_BP
        mode   = "narrow" if narrow else "wide"
        if mode == state.get("layout_mode"):
            return
        state["layout_mode"] = mode
        if narrow:
            content.padding   = ft.padding.Padding(left=12, right=12, top=14, bottom=10)
            sidebar.visible   = False
            rail.label_type   = ft.NavigationRailLabelType.NONE
        else:
            content.padding   = ft.padding.Padding(left=28, right=28, top=24, bottom=16)
            sidebar.visible   = True
            rail.label_type   = ft.NavigationRailLabelType.ALL

    def _on_resized(e):
        _apply_layout(getattr(e, "width", None) or page.window.width)
        page.update()

    page.on_resized = _on_resized
    _apply_layout(page.window.width or 1120)

    page.add(ft.Row([sidebar, content], expand=True, spacing=0))
    page.update()

    # Background update check (best-effort, non-blocking).
    def _bg_update_check():
        try:
            info = updater.check_for_update()
        except Exception:
            return
        if info.get("has_update"):
            _set_upd_banner(info)
            try:
                snack(f"🚀 Atualização disponível: {info['latest']} "
                      f"(você tem {info['current']}). Veja em Configurações.",
                      C_ACCENT)
            except Exception:
                pass
    page.run_thread(_bg_update_check)


if __name__ == "__main__":
    ft.run(main)
