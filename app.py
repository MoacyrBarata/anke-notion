#!/usr/bin/env python3
"""
app.py — Interface visual para o Notion → Anki Sync
Rodar: streamlit run app.py
"""
import os
import sys
import subprocess
import requests
import streamlit as st
from pathlib import Path
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).parent
ENV_FILE = ROOT / ".env"


# ── helpers ───────────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    return dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}


def save_cfg(updates: dict):
    ENV_FILE.touch(exist_ok=True)
    for k, v in updates.items():
        set_key(str(ENV_FILE), k, v or "")


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
            return True, "Autenticado"
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
    stats = {"disciplinas": 0, "aulas": 0, "gerados": 0, "enviados": 0, "erros": 0}
    mapping = {
        "Disciplinas processadas": "disciplinas",
        "Aulas processadas":       "aulas",
        "Flashcards gerados":      "gerados",
        "Flashcards no Anki":      "enviados",
        "Erros":                   "erros",
    }
    for line in lines:
        for label, key in mapping.items():
            if label in line:
                try:
                    stats[key] = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
    return stats


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Notion → Anki Sync",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    [data-testid="stMetric"] {
        background: #f1f5f9;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        border: 1px solid #e2e8f0;
    }
    [data-testid="stMetricValue"] { font-size: 2rem !important; }
    .stButton > button[kind="primary"] {
        background: #2563eb;
        border: none;
        font-weight: 600;
        font-size: 1rem;
        padding: 0.6rem 1.2rem;
        transition: background 0.2s;
    }
    .stButton > button[kind="primary"]:hover { background: #1d4ed8; }
    .stButton > button[kind="primary"]:disabled { background: #94a3b8; }
    .stRadio > div { gap: 0.5rem; }
    div[data-testid="stStatusWidget"] { display: none; }
</style>
""", unsafe_allow_html=True)

# ── session state ─────────────────────────────────────────────────────────────

defaults = {
    "conn_status": None,
    "log_lines":   [],
    "last_stats":  None,
    "sync_result": None,
    "running":     False,
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ── sidebar ───────────────────────────────────────────────────────────────────

cfg = load_cfg()

with st.sidebar:
    st.title("⚙️ Configurações")
    st.caption("Dados salvos no arquivo `.env` local.")

    notion_token = st.text_input(
        "Notion Token",
        value=cfg.get("NOTION_TOKEN", ""),
        type="password",
        placeholder="secret_...",
        help="notion.so/my-integrations",
    )

    st.divider()
    st.caption("**Provedor de IA**")

    provider = st.radio(
        "Provedor",
        ["Claude", "Gemini"],
        index=0 if cfg.get("AI_PROVIDER", "claude").lower() != "gemini" else 1,
        horizontal=True,
        label_visibility="collapsed",
    )

    if provider == "Claude":
        ai_key = st.text_input(
            "Anthropic API Key",
            value=cfg.get("ANTHROPIC_API_KEY", ""),
            type="password",
            placeholder="sk-ant-...",
            help="console.anthropic.com",
        )
        gemini_key   = cfg.get("GEMINI_API_KEY", "")
        gemini_model = cfg.get("GEMINI_MODEL", "gemini-2.0-flash")
    else:
        ai_key = cfg.get("ANTHROPIC_API_KEY", "")
        gemini_key = st.text_input(
            "Gemini API Key",
            value=cfg.get("GEMINI_API_KEY", ""),
            type="password",
            placeholder="AIza...",
            help="aistudio.google.com/app/apikey",
        )
        gemini_model = st.selectbox(
            "Modelo",
            ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"],
            index=["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"].index(
                cfg.get("GEMINI_MODEL", "gemini-2.0-flash")
            ) if cfg.get("GEMINI_MODEL") in ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash"] else 0,
        )

    st.divider()

    anki_host = st.text_input(
        "Anki Host",
        value=cfg.get("ANKI_HOST", "http://localhost:8765"),
    )

    max_cards = st.slider(
        "Máx. flashcards por aula",
        min_value=5,
        max_value=30,
        value=int(cfg.get("MAX_FLASHCARDS_POR_AULA", "10")),
        step=1,
    )

    st.divider()

    if st.button("💾 Salvar configurações", use_container_width=True):
        save_cfg({
            "NOTION_TOKEN":           notion_token,
            "AI_PROVIDER":            provider.lower(),
            "ANTHROPIC_API_KEY":      ai_key,
            "GEMINI_API_KEY":         gemini_key,
            "GEMINI_MODEL":           gemini_model,
            "ANKI_HOST":              anki_host,
            "MAX_FLASHCARDS_POR_AULA": str(max_cards),
        })
        st.success("Configurações salvas!")

# ── main ──────────────────────────────────────────────────────────────────────

st.title("📚 Notion → Anki Sync")
st.caption("Transforme suas anotações de aula em flashcards prontos para revisão no Anki.")

st.divider()

# ── conexões ──────────────────────────────────────────────────────────────────

col_title, col_test_btn = st.columns([5, 1])
with col_title:
    st.subheader("Conexões")
with col_test_btn:
    test_btn = st.button("🔍 Testar", use_container_width=True)

if test_btn:
    active_key = ai_key if provider == "Claude" else gemini_key
    with st.spinner("Verificando conexões..."):
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
    st.info("Clique em **Testar** para verificar as conexões antes de sincronizar.")

st.divider()

# ── sincronização ─────────────────────────────────────────────────────────────

st.subheader("Sincronização")

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
    if st.button("🗑 Limpar", use_container_width=True, disabled=st.session_state.running):
        st.session_state.log_lines  = []
        st.session_state.last_stats = None
        st.session_state.sync_result = None
        st.rerun()

if run_btn:
    st.session_state.running     = True
    st.session_state.log_lines   = []
    st.session_state.last_stats  = None
    st.session_state.sync_result = None

    env_override = {
        "NOTION_TOKEN":            notion_token,
        "AI_PROVIDER":             provider.lower(),
        "ANTHROPIC_API_KEY":       ai_key,
        "GEMINI_API_KEY":          gemini_key,
        "GEMINI_MODEL":            gemini_model,
        "ANKI_HOST":               anki_host,
        "MAX_FLASHCARDS_POR_AULA": str(max_cards),
    }

    log_placeholder = st.empty()

    with st.spinner("Sincronizando... aguarde a conclusão."):
        proc = run_sync(env_override)
        for line in proc.stdout:
            st.session_state.log_lines.append(line)
            log_placeholder.code(
                "".join(st.session_state.log_lines[-60:]),
                language="text",
            )
        proc.wait()

    st.session_state.running     = False
    st.session_state.last_stats  = parse_stats(st.session_state.log_lines)
    st.session_state.sync_result = "success" if proc.returncode == 0 else "error"
    st.rerun()

# ── log ───────────────────────────────────────────────────────────────────────

if st.session_state.log_lines:
    with st.expander("📋 Log completo", expanded=True):
        st.code("".join(st.session_state.log_lines), language="text")

# ── estatísticas ──────────────────────────────────────────────────────────────

if st.session_state.last_stats:
    st.divider()
    st.subheader("Resultado")
    s = st.session_state.last_stats
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Disciplinas",    s["disciplinas"])
    c2.metric("Aulas",          s["aulas"])
    c3.metric("Gerados",        s["gerados"])
    c4.metric("Enviados",       s["enviados"])
    c5.metric(
        "Erros",
        s["erros"],
        delta=s["erros"] if s["erros"] > 0 else None,
        delta_color="inverse",
    )
