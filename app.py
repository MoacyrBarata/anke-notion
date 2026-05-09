#!/usr/bin/env python3
"""
app.py — Interface Notion → Anki Sync
Rodar: streamlit run app.py
"""

import os
import sys
import json
import subprocess
import requests
import streamlit as st
from pathlib import Path
from dotenv import dotenv_values, set_key

try:
    from notion_client import Client as NotionClient
    NOTION_CLIENT_AVAILABLE = True
except ImportError:
    NOTION_CLIENT_AVAILABLE = False

ROOT             = Path(__file__).parent
ENV_FILE         = ROOT / ".env"
NOTION_CFG_FILE  = ROOT / "notion_config.json"
ICON_PNG         = ROOT / "icon.png"


def _load_page_icon():
    if ICON_PNG.exists():
        try:
            from PIL import Image
            return Image.open(ICON_PNG)
        except Exception:
            pass
    return "📚"


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def load_cfg() -> dict:
    return dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}


def save_cfg(updates: dict):
    ENV_FILE.touch(exist_ok=True)
    for k, v in updates.items():
        set_key(str(ENV_FILE), k, v or "")


def load_notion_config() -> dict | None:
    if NOTION_CFG_FILE.exists():
        with open(NOTION_CFG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_notion_config(data: dict):
    with open(NOTION_CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_notion(token: str) -> tuple[bool, str]:
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


def check_anki(host: str) -> tuple[bool, str]:
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


def check_ai_key(provider: str, key: str) -> tuple[bool, str]:
    if not key:
        return False, "Chave não informada"
    if provider == "claude":
        ok = key.startswith("sk-ant-")
        return ok, "Formato válido" if ok else "Esperado: sk-ant-..."
    ok = key.startswith("AIza") and len(key) > 20
    return ok, "Formato válido" if ok else "Esperado: AIza..."


def list_notion_databases(token: str) -> list[dict]:
    """Retorna todos os databases acessíveis pela integração."""
    if not NOTION_CLIENT_AVAILABLE or not token:
        return []
    try:
        client  = NotionClient(auth=token)
        results = []
        cursor  = None
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
    except Exception as e:
        st.error(f"Erro ao listar databases: {e}")
        return []


def get_database_properties(token: str, db_id: str) -> dict:
    """Retorna as propriedades de um database."""
    if not NOTION_CLIENT_AVAILABLE or not token:
        return {}
    try:
        client = NotionClient(auth=token)
        db     = client.databases.retrieve(db_id)
        return db.get("properties", {})
    except Exception as e:
        st.error(f"Erro ao buscar propriedades: {e}")
        return {}


def get_db_title(db: dict) -> str:
    try:
        return db["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return db.get("id", "Sem título")[:8]


def props_by_type(props: dict, *types: str) -> list[str]:
    return [k for k, v in props.items() if v["type"] in types]


def run_sync(env_override: dict) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({k: v for k, v in env_override.items() if v})
    return subprocess.Popen(
        [sys.executable, str(ROOT / "notion_anki_sync.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
        env=env,
    )


def parse_stats(lines: list[str]) -> dict:
    stats   = {"disciplinas": 0, "itens": 0, "gerados": 0, "enviados": 0, "erros": 0}
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


# ──────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────

st.set_page_config(
    page_title="Notion → Anki",
    page_icon=_load_page_icon(),
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Liquid Glass CSS ──────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* ── Background ── */
.stApp {
    background: linear-gradient(135deg, #0d0d1a 0%, #1a0533 35%, #0d1a33 65%, #0d0d1a 100%);
    background-attachment: fixed;
}

/* ── Animated background orbs ── */
.stApp::before {
    content: '';
    position: fixed;
    top: -50%;
    left: -50%;
    width: 200%;
    height: 200%;
    background:
        radial-gradient(ellipse 600px 400px at 20% 30%, rgba(99, 102, 241, 0.12) 0%, transparent 60%),
        radial-gradient(ellipse 500px 300px at 80% 70%, rgba(139, 92, 246, 0.1) 0%, transparent 60%),
        radial-gradient(ellipse 400px 400px at 50% 50%, rgba(6, 182, 212, 0.06) 0%, transparent 60%);
    pointer-events: none;
    z-index: 0;
}

/* ── Hide Streamlit chrome ── */
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu,
footer {
    display: none !important;
    height: 0 !important;
}

/* ── Block container ── */
.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 3rem !important;
    position: relative;
    z-index: 1;
    max-width: 1100px;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: rgba(15, 10, 30, 0.7) !important;
    backdrop-filter: blur(24px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(24px) saturate(180%) !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08) !important;
}

[data-testid="stSidebar"] > div {
    background: transparent !important;
}

/* ── Text colors ── */
h1, h2, h3, h4, h5, h6 {
    color: rgba(255, 255, 255, 0.95) !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em;
}

p, li, span, label, .stMarkdown {
    color: rgba(255, 255, 255, 0.75) !important;
}

.stCaption, small {
    color: rgba(255, 255, 255, 0.45) !important;
}

/* ── Inputs ── */
.stTextInput > div > div > input,
.stSelectbox > div > div,
.stTextArea > div > div > textarea {
    background: rgba(255, 255, 255, 0.06) !important;
    border: 1px solid rgba(255, 255, 255, 0.12) !important;
    border-radius: 12px !important;
    color: rgba(255, 255, 255, 0.9) !important;
    backdrop-filter: blur(8px) !important;
    transition: all 0.2s ease !important;
}

.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: rgba(139, 92, 246, 0.6) !important;
    box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.15), 0 0 20px rgba(139, 92, 246, 0.1) !important;
    background: rgba(255, 255, 255, 0.08) !important;
}

/* Placeholder */
.stTextInput > div > div > input::placeholder {
    color: rgba(255, 255, 255, 0.3) !important;
}

/* ── Selectbox ── */
.stSelectbox > div > div > div {
    color: rgba(255, 255, 255, 0.85) !important;
}

/* ── Buttons ── */
.stButton > button {
    background: rgba(255, 255, 255, 0.08) !important;
    backdrop-filter: blur(12px) !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    border-radius: 12px !important;
    color: rgba(255, 255, 255, 0.9) !important;
    font-weight: 500 !important;
    letter-spacing: 0.01em !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

.stButton > button:hover {
    background: rgba(255, 255, 255, 0.14) !important;
    border-color: rgba(255, 255, 255, 0.25) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
}

.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.7), rgba(139, 92, 246, 0.7)) !important;
    border-color: rgba(139, 92, 246, 0.5) !important;
    box-shadow: 0 4px 16px rgba(99, 102, 241, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.2) !important;
}

.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.85), rgba(139, 92, 246, 0.85)) !important;
    box-shadow: 0 8px 32px rgba(99, 102, 241, 0.4), 0 0 60px rgba(139, 92, 246, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.25) !important;
}

.stButton > button:disabled {
    opacity: 0.4 !important;
    transform: none !important;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.06) !important;
    backdrop-filter: blur(16px) saturate(150%) !important;
    -webkit-backdrop-filter: blur(16px) saturate(150%) !important;
    border-radius: 16px !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    padding: 1.2rem 1.4rem !important;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
    transition: transform 0.2s ease, box-shadow 0.2s ease !important;
}

[data-testid="stMetric"]:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
}

[data-testid="stMetricValue"] {
    font-size: 2rem !important;
    font-weight: 700 !important;
    color: rgba(255, 255, 255, 0.95) !important;
}

[data-testid="stMetricLabel"] {
    color: rgba(255, 255, 255, 0.55) !important;
    font-size: 0.8rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
}

[data-testid="stMetricDelta"] {
    color: rgba(248, 113, 113, 0.9) !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(255, 255, 255, 0.04) !important;
    border-radius: 14px !important;
    padding: 4px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    gap: 2px !important;
}

.stTabs [data-baseweb="tab"] {
    background: transparent !important;
    border-radius: 10px !important;
    color: rgba(255, 255, 255, 0.5) !important;
    font-weight: 500 !important;
    border: none !important;
    transition: all 0.2s ease !important;
}

.stTabs [aria-selected="true"] {
    background: rgba(139, 92, 246, 0.25) !important;
    color: rgba(255, 255, 255, 0.95) !important;
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.2) !important;
}

.stTabs [data-baseweb="tab"]:hover {
    background: rgba(255, 255, 255, 0.08) !important;
    color: rgba(255, 255, 255, 0.8) !important;
}

/* ── Expander ── */
.streamlit-expanderHeader {
    background: rgba(255, 255, 255, 0.05) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    color: rgba(255, 255, 255, 0.8) !important;
}

.streamlit-expanderContent {
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid rgba(255, 255, 255, 0.06) !important;
    border-top: none !important;
    border-radius: 0 0 12px 12px !important;
}

/* ── Alerts / info boxes ── */
.stAlert, .stInfo, .stSuccess, .stWarning, .stError {
    border-radius: 12px !important;
    backdrop-filter: blur(8px) !important;
    border: 1px solid !important;
}

.stSuccess {
    background: rgba(16, 185, 129, 0.12) !important;
    border-color: rgba(16, 185, 129, 0.3) !important;
    color: rgba(110, 231, 183, 0.95) !important;
}

.stError {
    background: rgba(239, 68, 68, 0.12) !important;
    border-color: rgba(239, 68, 68, 0.3) !important;
    color: rgba(252, 165, 165, 0.95) !important;
}

.stInfo {
    background: rgba(59, 130, 246, 0.12) !important;
    border-color: rgba(59, 130, 246, 0.3) !important;
    color: rgba(147, 197, 253, 0.95) !important;
}

.stWarning {
    background: rgba(245, 158, 11, 0.12) !important;
    border-color: rgba(245, 158, 11, 0.3) !important;
    color: rgba(252, 211, 77, 0.95) !important;
}

/* ── Code blocks / log ── */
.stCodeBlock, pre {
    background: rgba(0, 0, 0, 0.4) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
    backdrop-filter: blur(8px) !important;
    color: rgba(167, 243, 208, 0.9) !important;
}

/* ── Divider ── */
hr {
    border-color: rgba(255, 255, 255, 0.08) !important;
}

/* ── Radio ── */
.stRadio > div {
    gap: 0.5rem !important;
}

.stRadio [data-testid="stMarkdownContainer"] p {
    color: rgba(255, 255, 255, 0.8) !important;
}

/* ── Slider ── */
.stSlider [data-testid="stThumbValue"] {
    color: rgba(255, 255, 255, 0.9) !important;
}

/* ── Checkbox ── */
.stCheckbox label {
    color: rgba(255, 255, 255, 0.75) !important;
}

/* ── Multiselect ── */
[data-baseweb="tag"] {
    background: rgba(139, 92, 246, 0.3) !important;
    border: 1px solid rgba(139, 92, 246, 0.5) !important;
    border-radius: 8px !important;
    color: rgba(255, 255, 255, 0.9) !important;
}

/* ── Spinner ── */
.stSpinner > div {
    border-top-color: rgba(139, 92, 246, 0.8) !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: rgba(255,255,255,0.03); border-radius: 4px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.25); }

/* ── Glass card (custom HTML) ── */
.glass-card {
    background: rgba(255, 255, 255, 0.06);
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    border-radius: 20px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.1);
    padding: 1.5rem 1.8rem;
    margin-bottom: 1rem;
}

.glass-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
}

.glass-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: rgba(139, 92, 246, 0.2);
    border: 1px solid rgba(139, 92, 246, 0.35);
    border-radius: 20px;
    padding: 0.2rem 0.7rem;
    font-size: 0.75rem;
    color: rgba(196, 181, 253, 0.9);
    font-weight: 500;
}

.glass-badge.green {
    background: rgba(16, 185, 129, 0.2);
    border-color: rgba(16, 185, 129, 0.35);
    color: rgba(110, 231, 183, 0.9);
}

.glass-badge.red {
    background: rgba(239, 68, 68, 0.2);
    border-color: rgba(239, 68, 68, 0.35);
    color: rgba(252, 165, 165, 0.9);
}

.glass-badge.yellow {
    background: rgba(245, 158, 11, 0.2);
    border-color: rgba(245, 158, 11, 0.35);
    color: rgba(252, 211, 77, 0.9);
}

.glass-title {
    font-size: 1rem;
    font-weight: 600;
    color: rgba(255,255,255,0.9);
    margin: 0;
}

.glass-subtitle {
    font-size: 0.8rem;
    color: rgba(255,255,255,0.45);
    margin: 0;
}

.step-indicator {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
}

.step-dot {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 600;
}

.step-dot.active {
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.8), rgba(139, 92, 246, 0.8));
    color: white;
    box-shadow: 0 0 12px rgba(139, 92, 246, 0.4);
}

.step-dot.done {
    background: rgba(16, 185, 129, 0.3);
    border: 1px solid rgba(16, 185, 129, 0.5);
    color: rgba(110, 231, 183, 0.9);
}

.step-dot.pending {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.3);
}

.step-line {
    flex: 1;
    height: 1px;
    background: rgba(255,255,255,0.1);
}

.app-title {
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(135deg, #e0e7ff 0%, #c4b5fd 40%, #93c5fd 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.03em;
    margin: 0;
    line-height: 1.2;
}

.app-subtitle {
    font-size: 0.9rem;
    color: rgba(255,255,255,0.4);
    margin-top: 0.25rem;
}

div[data-testid="stStatusWidget"] { display: none; }
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────

defaults = {
    "conn_status":    None,
    "log_lines":      [],
    "last_stats":     None,
    "sync_result":    None,
    "running":        False,
    "notion_dbs":     None,
    "setup_step":     1,
    "setup_parent_db_id":   None,
    "setup_parent_db_name": None,
    "setup_child_props":    None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

cfg = load_cfg()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<p class="app-title">📚 Notion Anki</p><p class="app-subtitle">Notion → IA → Anki</p>', unsafe_allow_html=True)
    st.divider()

    st.markdown("#### ⚙️ Configurações")

    notion_token = st.text_input(
        "Notion Token",
        value=cfg.get("NOTION_TOKEN", ""),
        type="password",
        placeholder="secret_...",
        help="notion.so/my-integrations",
    )

    st.divider()
    provider = st.radio(
        "Provedor de IA",
        ["Claude", "Gemini"],
        index=0 if cfg.get("AI_PROVIDER", "claude").lower() != "gemini" else 1,
        horizontal=True,
    )

    if provider == "Claude":
        ai_key       = st.text_input("Anthropic API Key", value=cfg.get("ANTHROPIC_API_KEY", ""), type="password", placeholder="sk-ant-...")
        gemini_key   = cfg.get("GEMINI_API_KEY", "")
        gemini_model = cfg.get("GEMINI_MODEL", "gemini-2.0-flash")
    else:
        ai_key = cfg.get("ANTHROPIC_API_KEY", "")
        gemini_key = st.text_input("Gemini API Key", value=cfg.get("GEMINI_API_KEY", ""), type="password", placeholder="AIza...")
        gemini_model = st.selectbox(
            "Modelo Gemini",
            ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
            index=["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"].index(
                cfg.get("GEMINI_MODEL", "gemini-2.0-flash")
            ) if cfg.get("GEMINI_MODEL") in ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"] else 0,
        )

    st.divider()
    anki_host = st.text_input("Anki Host", value=cfg.get("ANKI_HOST", "http://localhost:8765"))
    max_cards = st.slider("Máx. flashcards/item", 5, 30, int(cfg.get("MAX_FLASHCARDS_POR_AULA", "10")))

    st.divider()
    if st.button("💾 Salvar configurações", use_container_width=True):
        save_cfg({
            "NOTION_TOKEN": notion_token, "AI_PROVIDER": provider.lower(),
            "ANTHROPIC_API_KEY": ai_key, "GEMINI_API_KEY": gemini_key,
            "GEMINI_MODEL": gemini_model, "ANKI_HOST": anki_host,
            "MAX_FLASHCARDS_POR_AULA": str(max_cards),
        })
        st.success("Configurações salvas!")

    # Status da config Notion
    st.divider()
    notion_cfg = load_notion_config()
    if notion_cfg:
        mode_label = "Hierárquico" if notion_cfg.get("mode") == "hierarchical" else "Plano"
        last_sync  = notion_cfg.get("last_sync_time", "Nunca")
        if last_sync and last_sync != "Nunca":
            last_sync = last_sync[:16].replace("T", " ")
        st.markdown(f"""
        <div style="background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.25);
             border-radius:10px;padding:0.75rem 1rem;font-size:0.8rem;">
          <span style="color:rgba(110,231,183,0.9);font-weight:600;">✓ Notion configurado</span><br>
          <span style="color:rgba(255,255,255,0.4);">Modo: {mode_label}</span><br>
          <span style="color:rgba(255,255,255,0.4);">Último sync: {last_sync}</span>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="background:rgba(245,158,11,0.1);border:1px solid rgba(245,158,11,0.25);
             border-radius:10px;padding:0.75rem 1rem;font-size:0.8rem;">
          <span style="color:rgba(252,211,77,0.9);font-weight:600;">⚠ Notion não configurado</span><br>
          <span style="color:rgba(255,255,255,0.4);">Configure na aba "Notion"</span>
        </div>
        """, unsafe_allow_html=True)


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_sync, tab_setup, tab_help = st.tabs(["▶  Sincronizar", "🔧  Configurar Notion", "❓  Ajuda"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB: SINCRONIZAR
# ════════════════════════════════════════════════════════════════════════════════

with tab_sync:
    notion_cfg = load_notion_config()

    st.markdown("""
    <div class="glass-card">
      <div class="glass-header">
        <p class="glass-title">Verificar Conexões</p>
      </div>
      <p class="glass-subtitle">Confirme que Notion, IA e Anki estão acessíveis antes de sincronizar.</p>
    </div>
    """, unsafe_allow_html=True)

    col_t, col_btn = st.columns([5, 1])
    with col_btn:
        test_btn = st.button("🔍 Testar", use_container_width=True)

    if test_btn:
        active_key = ai_key if provider == "Claude" else gemini_key
        with st.spinner("Verificando..."):
            st.session_state.conn_status = {
                "notion": check_notion(notion_token),
                "ai":     (*check_ai_key(provider.lower(), active_key), provider),
                "anki":   check_anki(anki_host),
            }

    if st.session_state.conn_status:
        s = st.session_state.conn_status
        c1, c2, c3 = st.columns(3)
        notion_ok, notion_msg = s["notion"]
        c1.metric("Notion", "✅ Conectado" if notion_ok else "❌ Falhou", notion_msg)
        ai_ok, ai_msg, prov = s["ai"]
        c2.metric(prov, "✅ OK" if ai_ok else "❌ Falhou", ai_msg)
        anki_ok, anki_msg = s["anki"]
        c3.metric("Anki", "✅ Disponível" if anki_ok else "❌ Offline", anki_msg)
    else:
        st.info("Clique em **Testar** para verificar as conexões.")

    st.divider()

    # Avisos de pré-requisito
    if not notion_cfg:
        st.warning("Notion não configurado. Configure na aba **Configurar Notion** antes de sincronizar.")
    else:
        st.markdown("""
        <div class="glass-card">
          <div class="glass-header">
            <p class="glass-title">Sincronização</p>
          </div>
          <p class="glass-subtitle">Busca itens não sincronizados, gera flashcards com IA e envia ao Anki.</p>
        </div>
        """, unsafe_allow_html=True)

        if st.session_state.sync_result == "success":
            st.success("Sincronização concluída com sucesso!")
        elif st.session_state.sync_result == "error":
            st.error("Sincronização encerrada com erro. Confira o log abaixo.")

        col_run, col_clear = st.columns([6, 1])
        with col_run:
            run_btn = st.button(
                "▶  Iniciar Sincronização",
                type="primary",
                use_container_width=True,
                disabled=st.session_state.running,
            )
        with col_clear:
            if st.button("🗑", use_container_width=True, disabled=st.session_state.running):
                st.session_state.log_lines   = []
                st.session_state.last_stats  = None
                st.session_state.sync_result = None
                st.rerun()

        if run_btn:
            st.session_state.running     = True
            st.session_state.log_lines   = []
            st.session_state.last_stats  = None
            st.session_state.sync_result = None

            env_override = {
                "NOTION_TOKEN": notion_token, "AI_PROVIDER": provider.lower(),
                "ANTHROPIC_API_KEY": ai_key, "GEMINI_API_KEY": gemini_key,
                "GEMINI_MODEL": gemini_model, "ANKI_HOST": anki_host,
                "MAX_FLASHCARDS_POR_AULA": str(max_cards),
            }
            log_placeholder = st.empty()
            with st.spinner("Sincronizando..."):
                proc = run_sync(env_override)
                for line in proc.stdout:
                    st.session_state.log_lines.append(line)
                    log_placeholder.code("".join(st.session_state.log_lines[-60:]), language="text")
                proc.wait()

            st.session_state.running     = False
            st.session_state.last_stats  = parse_stats(st.session_state.log_lines)
            st.session_state.sync_result = "success" if proc.returncode == 0 else "error"
            st.rerun()

        if st.session_state.log_lines:
            with st.expander("📋 Log completo", expanded=True):
                st.code("".join(st.session_state.log_lines), language="text")

        if st.session_state.last_stats:
            st.divider()
            st.markdown("#### Resultado")
            s  = st.session_state.last_stats
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Categorias",   s["disciplinas"])
            c2.metric("Itens",        s["itens"])
            c3.metric("Gerados",      s["gerados"])
            c4.metric("No Anki",      s["enviados"])
            c5.metric("Erros", s["erros"],
                      delta=s["erros"] if s["erros"] > 0 else None,
                      delta_color="inverse")


# ════════════════════════════════════════════════════════════════════════════════
# TAB: CONFIGURAR NOTION
# ════════════════════════════════════════════════════════════════════════════════

with tab_setup:
    st.markdown("""
    <div class="glass-card">
      <div class="glass-header">
        <p class="glass-title">Configurar estrutura do Notion</p>
      </div>
      <p class="glass-subtitle">
        Selecione quais databases serão usados para gerar flashcards.
        A configuração é salva localmente e reutilizada nas próximas sincronizações.
      </p>
    </div>
    """, unsafe_allow_html=True)

    if not notion_token:
        st.warning("Insira o **Notion Token** na barra lateral antes de configurar.")
        st.stop()

    if not NOTION_CLIENT_AVAILABLE:
        st.error("Pacote `notion-client` não instalado. Execute: `pip install notion-client`")
        st.stop()

    # ── Step indicator ────────────────────────────────────────────────────────
    step = st.session_state.setup_step

    def step_status(n):
        if n < step: return "done"
        if n == step: return "active"
        return "pending"

    def step_icon(n):
        if n < step: return "✓"
        return str(n)

    st.markdown(f"""
    <div class="step-indicator">
      <div class="step-dot {step_status(1)}">{step_icon(1)}</div>
      <div class="step-line"></div>
      <div class="step-dot {step_status(2)}">{step_icon(2)}</div>
      <div class="step-line"></div>
      <div class="step-dot {step_status(3)}">{step_icon(3)}</div>
      <div class="step-line"></div>
      <div class="step-dot {step_status(4)}">{step_icon(4)}</div>
    </div>
    <div style="display:flex;justify-content:space-between;margin-top:-0.5rem;margin-bottom:1.5rem;">
      <span style="font-size:0.7rem;color:rgba(255,255,255,0.4);text-align:center;width:25%">Modo</span>
      <span style="font-size:0.7rem;color:rgba(255,255,255,0.4);text-align:center;width:25%">Database</span>
      <span style="font-size:0.7rem;color:rgba(255,255,255,0.4);text-align:center;width:25%">Campos</span>
      <span style="font-size:0.7rem;color:rgba(255,255,255,0.4);text-align:center;width:25%">Anki</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Step 1: Modo ──────────────────────────────────────────────────────────
    if step == 1:
        st.markdown("#### Passo 1 — Escolha o modo de estrutura")
        st.markdown("""
        <div class="glass-card" style="margin-bottom:0.5rem">
          <p class="glass-title">🗂 Hierárquico</p>
          <p class="glass-subtitle" style="margin-top:0.4rem">
            Um database principal (ex: "Disciplinas") onde cada linha contém
            um database filho (ex: "Aulas"). Ideal para estruturas com categorias e subcategorias.
          </p>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        <div class="glass-card">
          <p class="glass-title">📋 Plano</p>
          <p class="glass-subtitle" style="margin-top:0.4rem">
            Um único database onde cada linha vira flashcards diretamente.
            Ideal para listas de conteúdo simples.
          </p>
        </div>
        """, unsafe_allow_html=True)

        mode_choice = st.radio(
            "Modo",
            ["🗂 Hierárquico (DB pai → DB filho)", "📋 Plano (DB único)"],
            label_visibility="collapsed",
        )
        if st.button("Próximo →", type="primary"):
            st.session_state.setup_mode = "hierarchical" if "Hierárquico" in mode_choice else "flat"
            st.session_state.setup_step = 2
            st.rerun()

    # ── Step 2: Selecionar database ───────────────────────────────────────────
    elif step == 2:
        st.markdown("#### Passo 2 — Selecione o database principal")

        if st.session_state.notion_dbs is None:
            with st.spinner("Buscando seus databases no Notion..."):
                st.session_state.notion_dbs = list_notion_databases(notion_token)

        dbs = st.session_state.notion_dbs
        if not dbs:
            st.error("Nenhum database encontrado. Verifique o token e se a integração tem acesso às páginas.")
        else:
            db_options = {get_db_title(d): d["id"] for d in dbs}
            db_labels  = list(db_options.keys())

            selected_label = st.selectbox(
                "Database principal" if st.session_state.get("setup_mode") == "hierarchical"
                else "Database de conteúdo",
                db_labels,
            )

            col_prev, col_next = st.columns([1, 4])
            with col_prev:
                if st.button("← Voltar"):
                    st.session_state.setup_step = 1
                    st.rerun()
            with col_next:
                if st.button("Próximo →", type="primary"):
                    st.session_state.setup_parent_db_id   = db_options[selected_label]
                    st.session_state.setup_parent_db_name = selected_label
                    st.session_state.setup_step = 3
                    st.rerun()

    # ── Step 3: Mapear campos ─────────────────────────────────────────────────
    elif step == 3:
        st.markdown("#### Passo 3 — Mapeie os campos")
        db_id   = st.session_state.setup_parent_db_id
        db_name = st.session_state.setup_parent_db_name
        st.markdown(f"Database selecionado: **{db_name}**")

        with st.spinner("Buscando propriedades..."):
            props = get_database_properties(notion_token, db_id)

        if not props:
            st.error("Não foi possível buscar as propriedades do database.")
        else:
            all_prop_names   = ["(nenhum)"] + list(props.keys())
            title_props      = props_by_type(props, "title")
            text_props       = ["(nenhum)"] + props_by_type(props, "rich_text")
            date_props       = ["(nenhum)"] + props_by_type(props, "date", "created_time", "last_edited_time")
            select_props     = ["(nenhum)"] + props_by_type(props, "select", "status")
            text_all_props   = ["(nenhum)"] + props_by_type(props, "rich_text", "title")

            mode = st.session_state.get("setup_mode", "hierarchical")

            # Campo título (obrigatório)
            default_title = title_props[0] if title_props else (list(props.keys())[0] if props else "")
            parent_title_prop = st.selectbox(
                "Campo título/nome" + (" (das categorias)" if mode == "hierarchical" else ""),
                title_props if title_props else list(props.keys()),
                help="Propriedade do tipo 'title' que identifica cada linha."
            )

            if mode == "hierarchical":
                st.divider()
                st.markdown("**Database filho (dentro de cada página):**")
                child_keyword = st.text_input(
                    "Palavra-chave para encontrar o database filho",
                    value="Aulas",
                    help="O app busca child databases cujo título contenha essa palavra."
                )
                child_title = st.selectbox("Campo título do item filho", text_all_props,
                                           help="Propriedade que identifica cada item (aula, nota, etc).")
            else:
                child_keyword = ""
                child_title   = parent_title_prop

            st.divider()
            st.markdown("**Conteúdo para gerar flashcards:**")
            content_prop = st.selectbox("Campo de conteúdo (resumo/texto)", text_props,
                                        help="Deixe '(nenhum)' para usar apenas o conteúdo da página.")

            date_prop = st.selectbox("Campo de data", date_props, help="Opcional.")

            st.divider()
            st.markdown("**Controle de sincronização (opcional):**")

            use_sync = st.checkbox(
                "Usar campo de sincronização no Notion",
                value=True,
                help="Marca itens já sincronizados. Se desmarcado, usa timestamp da última execução."
            )

            sync_prop = status_prop = status_val = sync_done = None
            if use_sync:
                sync_prop  = st.selectbox("Campo de sincronização (ex: 'Sincronização')", select_props)
                sync_done  = st.text_input("Valor = sincronizado", value="✅ Sincronizado")
                status_prop = st.selectbox("Campo de status (filtro de prontos, opcional)", select_props)
                status_val  = st.text_input("Valor = pronto para sincronizar", value="✅ Completa",
                                            help="Deixe em branco para processar todos os itens.")

            col_prev, col_next = st.columns([1, 4])
            with col_prev:
                if st.button("← Voltar"):
                    st.session_state.setup_step = 2
                    st.rerun()
            with col_next:
                if st.button("Próximo →", type="primary"):
                    st.session_state.setup_child_props = {
                        "parent_title_prop": parent_title_prop,
                        "child_keyword":     child_keyword,
                        "child_title":       child_title,
                        "content_prop":      None if content_prop == "(nenhum)" else content_prop,
                        "date_prop":         None if date_prop == "(nenhum)" else date_prop,
                        "use_sync":          use_sync,
                        "sync_prop":         None if (not sync_prop or sync_prop == "(nenhum)") else sync_prop,
                        "sync_done":         sync_done or "✅ Sincronizado",
                        "status_prop":       None if (not status_prop or status_prop == "(nenhum)") else status_prop,
                        "status_val":        status_val or None,
                    }
                    st.session_state.setup_step = 4
                    st.rerun()

    # ── Step 4: Anki + Salvar ─────────────────────────────────────────────────
    elif step == 4:
        st.markdown("#### Passo 4 — Configurações do Anki")

        notion_cfg_existing = load_notion_config()
        default_deck = notion_cfg_existing.get("anki_deck_root", "Notion::Sync") if notion_cfg_existing else "Notion::Sync"

        anki_deck_root = st.text_input(
            "Deck raiz no Anki",
            value=default_deck,
            help="Subdecks serão criados automaticamente (ex: MeuDeck::Categoria)."
        )

        st.divider()

        # Preview do config
        p = st.session_state.setup_child_props or {}
        mode = st.session_state.get("setup_mode", "hierarchical")

        with st.expander("📋 Resumo da configuração", expanded=True):
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**Modo:** {'Hierárquico' if mode == 'hierarchical' else 'Plano'}")
                st.markdown(f"**DB principal:** {st.session_state.setup_parent_db_name}")
                if mode == "hierarchical":
                    st.markdown(f"**Keyword child DB:** `{p.get('child_keyword', '')}`")
                st.markdown(f"**Campo título:** `{p.get('parent_title_prop', '')}`")
            with col_b:
                st.markdown(f"**Campo conteúdo:** `{p.get('content_prop') or '(blocos da página)'}`")
                st.markdown(f"**Campo data:** `{p.get('date_prop') or '(nenhum)'}`")
                st.markdown(f"**Controle sync:** {'campo Notion' if p.get('use_sync') else 'timestamp'}")
                if p.get("use_sync") and p.get("sync_prop"):
                    st.markdown(f"**Campo sync:** `{p.get('sync_prop')}` = `{p.get('sync_done')}`")
                st.markdown(f"**Deck Anki:** `{anki_deck_root}`")

        col_prev, col_save = st.columns([1, 4])
        with col_prev:
            if st.button("← Voltar"):
                st.session_state.setup_step = 3
                st.rerun()
        with col_save:
            if st.button("💾 Salvar configuração", type="primary"):
                p = st.session_state.setup_child_props or {}

                # Preserva last_sync_time se existir
                existing = load_notion_config()
                last_sync = existing.get("last_sync_time") if existing else None

                new_cfg = {
                    "version":            2,
                    "mode":               st.session_state.get("setup_mode", "hierarchical"),
                    "parent_db_id":       st.session_state.setup_parent_db_id,
                    "parent_db_name":     st.session_state.setup_parent_db_name,
                    "parent_name_prop":   p.get("parent_title_prop", ""),
                    "child_db_keyword":   p.get("child_keyword", ""),
                    "child_title_prop":   p.get("child_title", ""),
                    "child_content_prop": p.get("content_prop"),
                    "child_date_prop":    p.get("date_prop"),
                    "use_sync_field":     p.get("use_sync", True),
                    "child_sync_prop":    p.get("sync_prop"),
                    "child_sync_done":    p.get("sync_done", "✅ Sincronizado"),
                    "child_status_prop":  p.get("status_prop"),
                    "child_status_complete": p.get("status_val"),
                    "anki_deck_root":     anki_deck_root,
                    "last_sync_time":     last_sync,
                }
                save_notion_config(new_cfg)
                st.success("Configuração salva! Agora vá para a aba **Sincronizar**.")
                st.session_state.setup_step = 1
                st.rerun()

    # ── Reconfigurar ──────────────────────────────────────────────────────────
    existing_cfg = load_notion_config()
    if existing_cfg and step == 1:
        st.divider()
        st.markdown("**Configuração atual salva:**")
        col1, col2 = st.columns([3, 1])
        with col1:
            mode_lbl = "Hierárquico" if existing_cfg.get("mode") == "hierarchical" else "Plano"
            st.markdown(f"""
            <div class="glass-card">
              <p class="glass-title">{existing_cfg.get('parent_db_name', 'DB configurado')}</p>
              <p class="glass-subtitle">Modo {mode_lbl} · Deck: {existing_cfg.get('anki_deck_root','')}</p>
            </div>
            """, unsafe_allow_html=True)
        with col2:
            if st.button("🔄 Reconfigurar", use_container_width=True):
                st.session_state.notion_dbs = None
                st.session_state.setup_step = 1
                st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# TAB: AJUDA
# ════════════════════════════════════════════════════════════════════════════════

with tab_help:
    st.markdown("""
    <div class="glass-card">
      <p class="glass-title">📖 Como usar</p>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("1. Configurar integração Notion", expanded=False):
        st.markdown("""
1. Acesse **notion.so/my-integrations**
2. Clique em **+ Nova integração**
3. Dê um nome (ex: "Anki Sync") e clique em **Enviar**
4. Copie o **Token interno de integração**
5. Em cada página/database do Notion que deseja usar:
   - Clique em `...` no topo direito
   - Selecione **Conectar** → sua integração
        """)

    with st.expander("2. Instalar AnkiConnect", expanded=False):
        st.markdown("""
1. Abra o Anki
2. **Ferramentas → Complementos → Obter Complementos**
3. Digite o código: **`2055492159`**
4. Reinicie o Anki
5. Deixe o Anki **aberto** durante a sincronização
        """)

    with st.expander("3. Configurar estrutura Notion", expanded=False):
        st.markdown("""
Na aba **Configurar Notion**:

1. Escolha o **modo**:
   - *Hierárquico*: quando você tem um DB de categorias, e dentro de cada página há um DB de conteúdo
   - *Plano*: quando você tem um único DB onde cada linha vira flashcards

2. Selecione o **database** principal

3. **Mapeie os campos**: diga ao app qual propriedade é o título, o conteúdo, a data, etc.

4. Configure o **controle de sincronização**:
   - *Com campo Notion*: o app marca itens como sincronizados usando um campo select
   - *Com timestamp*: itens editados após a última sincronização são processados
        """)

    with st.expander("4. Sincronizar", expanded=False):
        st.markdown("""
1. Na aba **Sincronizar**, clique em **Testar** para verificar conexões
2. Clique em **Iniciar Sincronização**
3. O log mostra o progresso em tempo real
4. Ao final, um relatório mostra flashcards gerados e enviados
        """)

    with st.expander("Requisitos e dependências", expanded=False):
        st.code("""pip install notion-client anthropic requests python-dotenv streamlit
# Para Gemini:
pip install google-genai""", language="bash")
