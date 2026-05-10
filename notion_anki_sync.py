#!/usr/bin/env python3
"""
notion_anki_sync.py
====================
Automação: Notion → IA (Claude ou Gemini) → Anki

Fluxo:
1. Lê configuração de notion_config.json (gerado pelo app.py)
2. Para cada DB configurado, busca páginas não sincronizadas
3. Envia conteúdo para IA gerar flashcards
4. Cria notas no Anki via AnkiConnect
5. Marca páginas como sincronizadas e salva timestamp

Requisitos (Claude):
    pip install notion-client anthropic requests python-dotenv

Requisitos (Gemini):
    pip install notion-client google-genai requests python-dotenv
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client as NotionClient

try:
    import anthropic as _anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from google import genai as _google_genai
    from google.genai import types as _genai_types
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False

# ──────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

ROOT              = Path(__file__).parent
NOTION_CONFIG_FILE = ROOT / "notion_config.json"

NOTION_TOKEN      = os.getenv("NOTION_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
AI_PROVIDER       = os.getenv("AI_PROVIDER", "claude").lower()
ANKI_HOST         = os.getenv("ANKI_HOST", "http://localhost:8765")

MAX_FLASHCARDS_POR_AULA = int(os.getenv("MAX_FLASHCARDS_POR_AULA", "10"))


# ──────────────────────────────────────────────
# Config Notion (notion_config.json)
# ──────────────────────────────────────────────

def load_notion_config() -> dict | None:
    if NOTION_CONFIG_FILE.exists():
        with open(NOTION_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_notion_config(cfg: dict):
    with open(NOTION_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ──────────────────────────────────────────────
# Clientes
# ──────────────────────────────────────────────
notion = NotionClient(auth=NOTION_TOKEN)

claude_client = None
gemini_client = None

if AI_PROVIDER == "gemini":
    if GOOGLE_GENAI_AVAILABLE and GEMINI_API_KEY:
        gemini_client = _google_genai.Client(api_key=GEMINI_API_KEY)
else:
    if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
        claude_client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ──────────────────────────────────────────────
# Helpers — Notion (genérico)
# ──────────────────────────────────────────────

def query_database_all(db_id: str, filter_obj: dict | None = None) -> list[dict]:
    """Pagina todas as results de um database."""
    results = []
    cursor  = None
    while True:
        kwargs = {"start_cursor": cursor} if cursor else {}
        if filter_obj:
            kwargs["filter"] = filter_obj
        resp = notion.databases.query(db_id, **kwargs)
        results.extend(resp["results"])
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
    return results


def get_title_value(page: dict, prop_name: str) -> str:
    """Extrai texto de propriedade title ou rich_text."""
    try:
        prop = page["properties"][prop_name]
        if prop["type"] == "title":
            items = prop["title"]
        elif prop["type"] == "rich_text":
            items = prop["rich_text"]
        else:
            return ""
        return "".join(r["plain_text"] for r in items).strip()
    except (KeyError, IndexError):
        return ""


def get_select_value(page: dict, prop_name: str) -> str:
    try:
        return page["properties"][prop_name]["select"]["name"] or ""
    except (KeyError, TypeError):
        return ""


def get_date_value(page: dict, prop_name: str) -> str:
    try:
        return page["properties"][prop_name]["date"]["start"] or ""
    except (KeyError, TypeError):
        return ""


def get_rich_text_value(page: dict, prop_name: str) -> str:
    try:
        items = page["properties"][prop_name]["rich_text"]
        return "".join(r["plain_text"] for r in items).strip()
    except (KeyError, TypeError):
        return ""


def extrair_texto_blocos(blocos: list, nivel: int = 0) -> str:
    linhas = []
    indent = "  " * nivel
    for bloco in blocos:
        tipo  = bloco["type"]
        texto = ""
        if tipo in ("paragraph", "bulleted_list_item", "numbered_list_item",
                    "toggle", "quote", "callout"):
            rich  = bloco[tipo].get("rich_text", [])
            texto = "".join(r["plain_text"] for r in rich)
        elif tipo in ("heading_1", "heading_2", "heading_3"):
            rich  = bloco[tipo].get("rich_text", [])
            texto = "## " + "".join(r["plain_text"] for r in rich)
        elif tipo == "code":
            rich  = bloco["code"].get("rich_text", [])
            texto = "```\n" + "".join(r["plain_text"] for r in rich) + "\n```"
        elif tipo == "divider":
            texto = "---"
        if texto.strip():
            linhas.append(f"{indent}{texto}")
        if bloco.get("has_children"):
            try:
                filhos = notion.blocks.children.list(block_id=bloco["id"])
                linhas.append(extrair_texto_blocos(filhos["results"], nivel + 1))
            except Exception:
                pass
    return "\n".join(linhas)


def get_page_content(page_id: str) -> str:
    """Extrai conteúdo textual de blocos internos de uma página."""
    try:
        blocos = notion.blocks.children.list(block_id=page_id)
        texto  = extrair_texto_blocos(blocos["results"])
        return texto.strip()
    except Exception as e:
        log.warning(f"Erro ao ler blocos de {page_id}: {e}")
        return ""


def find_child_database(page_id: str, keyword: str) -> str | None:
    """Busca child_database cujo título contenha keyword."""
    try:
        children = notion.blocks.children.list(block_id=page_id)
        for block in children["results"]:
            if block["type"] == "child_database":
                title = block["child_database"].get("title", "")
                if keyword.lower() in title.lower():
                    return block["id"]
    except Exception as e:
        log.warning(f"Erro ao buscar child DB em {page_id}: {e}")
    return None


def marcar_sincronizado(page_id: str, prop_name: str, valor: str):
    notion.pages.update(
        page_id=page_id,
        properties={prop_name: {"select": {"name": valor}}},
    )


def is_newer_than(page: dict, iso_timestamp: str | None) -> bool:
    """Retorna True se page foi editada após iso_timestamp."""
    if not iso_timestamp:
        return True
    try:
        page_time = datetime.fromisoformat(
            page["last_edited_time"].replace("Z", "+00:00")
        )
        ref_time = datetime.fromisoformat(iso_timestamp)
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=timezone.utc)
        return page_time > ref_time
    except Exception:
        return True


# ──────────────────────────────────────────────
# Helpers — IA
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """Você é um especialista em criação de flashcards para estudos.
Seu objetivo é transformar anotações em flashcards eficientes para revisão no Anki.

Regras para gerar bons flashcards:
- Cada flashcard deve testar UM conceito específico
- A frente (front) deve ser uma pergunta direta ou afirmação incompleta
- O verso (back) deve ser conciso, claro e completo
- Priorize: definições, leis/normas, fórmulas, comparações, exceções, exemplos-chave
- Evite flashcards muito genéricos ou com respostas longas demais

Retorne SOMENTE um array JSON válido, sem markdown, sem explicações, apenas o JSON:
[
  {"front": "pergunta ou afirmação incompleta", "back": "resposta completa"},
  ...
]"""


def gerar_flashcards(
    categoria: str,
    titulo: str,
    data: str,
    conteudo: str,
    max_cards: int = MAX_FLASHCARDS_POR_AULA,
) -> list[dict]:
    prompt = f"""Categoria: {categoria}
Título: {titulo}
Data: {data}

Conteúdo:
---
{conteudo}
---

Gere até {max_cards} flashcards. Retorne SOMENTE o array JSON."""

    provider_label = "Gemini" if AI_PROVIDER == "gemini" else "Claude"
    log.info(f"  → Enviando para {provider_label}: {len(conteudo)} caracteres")

    raw = ""
    try:
        if AI_PROVIDER == "gemini":
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                config=_genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=2048,
                ),
                contents=prompt,
            )
            raw = resp.text.strip()
        else:
            resp = claude_client.messages.create(
                model="claude-opus-4-5",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        flashcards = json.loads(raw)
        log.info(f"  → {len(flashcards)} flashcards gerados")
        return flashcards

    except json.JSONDecodeError as e:
        log.error(f"  ✗ Erro ao parsear JSON: {e}\nResposta: {raw[:300]}")
        return []
    except Exception as e:
        log.error(f"  ✗ Erro na API {provider_label}: {e}")
        return []


# ──────────────────────────────────────────────
# Helpers — AnkiConnect
# ──────────────────────────────────────────────

def anki_request(action: str, **params) -> dict:
    payload = {"action": action, "version": 6, "params": params}
    resp    = requests.post(ANKI_HOST, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise Exception(f"AnkiConnect error: {data['error']}")
    return data.get("result")


def garantir_deck(deck_name: str):
    anki_request("createDeck", deck=deck_name)


def modelo_existe(model_name: str) -> bool:
    return model_name in anki_request("modelNames")


def criar_modelo_basico():
    model_name = "Notion-Flashcard"
    if modelo_existe(model_name):
        return model_name
    anki_request(
        "createModel",
        modelName=model_name,
        inOrderFields=["Frente", "Verso", "Categoria", "Titulo", "Data"],
        css="""
            .card { font-family: 'Segoe UI', Arial, sans-serif; font-size: 18px;
                    text-align: center; color: #1a1a1a; background: #fafafa; padding: 20px; }
            .tag { font-size: 12px; color: #888; margin-bottom: 8px; }
            hr { border-color: #ddd; }
        """,
        cardTemplates=[{
            "Name": "Card 1",
            "Front": """<div class="tag">{{Categoria}} · {{Titulo}}</div>
<div>{{Frente}}</div>""",
            "Back": """{{FrontSide}}
<hr>
<div>{{Verso}}</div>
<div class="tag" style="margin-top:12px">📅 {{Data}}</div>""",
        }],
    )
    log.info("  → Modelo 'Notion-Flashcard' criado no Anki")
    return model_name


def adicionar_nota(deck: str, flashcard: dict, categoria: str, titulo: str, data: str) -> bool:
    try:
        anki_request(
            "addNote",
            note={
                "deckName":  deck,
                "modelName": "Notion-Flashcard",
                "fields": {
                    "Frente":    flashcard["front"],
                    "Verso":     flashcard["back"],
                    "Categoria": categoria,
                    "Titulo":    titulo,
                    "Data":      data,
                },
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                "tags": [
                    categoria.replace(" ", "_"),
                    f"data:{data}" if data else "sem_data",
                    "notion-sync",
                ],
            },
        )
        return True
    except Exception as e:
        if "duplicate" not in str(e).lower():
            log.warning(f"    ✗ Erro ao adicionar nota: {e}")
        return False


def anki_disponivel() -> bool:
    try:
        anki_request("version")
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# Pipeline — modo hierárquico
# (DB pai contém páginas, cada página tem child DB)
# ──────────────────────────────────────────────

def processar_hierarquico(cfg: dict) -> dict:
    """
    Estrutura: DB_PAI → páginas (disciplinas/categorias)
               cada página → child DB → itens (aulas/conteúdos)
    """
    parent_db_id        = cfg["parent_db_id"]
    parent_name_prop    = cfg["parent_name_prop"]
    child_db_keyword    = cfg["child_db_keyword"]
    child_title_prop    = cfg["child_title_prop"]
    child_content_prop  = cfg.get("child_content_prop")
    child_date_prop     = cfg.get("child_date_prop")
    use_sync_field      = cfg.get("use_sync_field", True)
    child_status_prop   = cfg.get("child_status_prop")
    child_status_val    = cfg.get("child_status_complete")
    child_sync_prop     = cfg.get("child_sync_prop")
    child_sync_done     = cfg.get("child_sync_done", "✅ Sincronizado")
    anki_deck_root      = cfg.get("anki_deck_root", "Notion::Sync")
    last_sync_time      = cfg.get("last_sync_time")

    total = {"disciplinas": 0, "itens": 0, "gerados": 0, "enviados": 0, "erros": 0}

    # 1. Busca páginas pai
    paginas_pai = query_database_all(parent_db_id)
    log.info(f"Categorias encontradas: {len(paginas_pai)}")

    for pai in paginas_pai:
        nome_categoria = get_title_value(pai, parent_name_prop) or "Sem nome"
        log.info(f"\n{'='*50}\n📚 {nome_categoria}")

        # 2. Acha child DB
        child_db_id = find_child_database(pai["id"], child_db_keyword)
        if not child_db_id:
            log.info("  → Child DB não encontrado. Pulando.")
            continue

        # 3. Monta filtro de busca
        filtros = []
        if use_sync_field and child_sync_prop:
            filtros.append({
                "property": child_sync_prop,
                "select": {"does_not_equal": child_sync_done},
            })
        if child_status_prop and child_status_val:
            filtros.append({
                "property": child_status_prop,
                "select": {"equals": child_status_val},
            })

        filter_obj = None
        if len(filtros) == 1:
            filter_obj = filtros[0]
        elif len(filtros) > 1:
            filter_obj = {"and": filtros}

        itens = query_database_all(child_db_id, filter_obj)

        # 4. Se não há sync field, filtra por last_edited_time
        if not use_sync_field and last_sync_time:
            itens = [i for i in itens if is_newer_than(i, last_sync_time)]

        if not itens:
            log.info("  → Nenhum item pendente.")
            continue

        log.info(f"  → {len(itens)} item(s) para processar")

        deck_name = f"{anki_deck_root}::{nome_categoria}"
        garantir_deck(deck_name)
        total["disciplinas"] += 1

        for item in itens:
            titulo   = get_title_value(item, child_title_prop) or "Sem título"
            data     = get_date_value(item, child_date_prop) if child_date_prop else ""
            log.info(f"\n  📄 {titulo} ({data})")

            # Monta conteúdo
            partes = []
            if child_content_prop:
                resumo = get_rich_text_value(item, child_content_prop)
                if resumo:
                    partes.append(f"[Resumo]\n{resumo}")
            conteudo_blocos = get_page_content(item["id"])
            if conteudo_blocos:
                partes.append(f"[Anotações]\n{conteudo_blocos}")
            conteudo = "\n\n".join(partes)

            if not conteudo.strip():
                log.info("    → Sem conteúdo. Pulando.")
                continue

            total["itens"] += 1

            # Gera flashcards
            cards = gerar_flashcards(nome_categoria, titulo, data, conteudo)
            total["gerados"] += len(cards)

            if not cards:
                if use_sync_field and child_sync_prop:
                    marcar_sincronizado(item["id"], child_sync_prop, "❌ Erro")
                total["erros"] += 1
                continue

            # Envia ao Anki
            enviados = sum(
                adicionar_nota(deck_name, c, nome_categoria, titulo, data)
                for c in cards
            )
            total["enviados"] += enviados
            log.info(f"    ✓ {enviados}/{len(cards)} notas no Anki")

            if use_sync_field and child_sync_prop:
                marcar_sincronizado(item["id"], child_sync_prop, child_sync_done)
                log.info("    ✓ Marcado como sincronizado")

            time.sleep(1)

    return total


# ──────────────────────────────────────────────
# Pipeline — modo plano
# (DB único, cada página vira flashcards)
# ──────────────────────────────────────────────

def _processar_db_plano(db_id: str, db_name: str, cfg: dict, total: dict):
    """Processa um único database no modo plano."""
    title_prop     = cfg["parent_name_prop"]
    content_prop   = cfg.get("child_content_prop")
    date_prop      = cfg.get("child_date_prop")
    use_sync_field = cfg.get("use_sync_field", True)
    sync_prop      = cfg.get("child_sync_prop")
    sync_done      = cfg.get("child_sync_done", "✅ Sincronizado")
    status_prop    = cfg.get("child_status_prop")
    status_val     = cfg.get("child_status_complete")
    anki_deck_root = cfg.get("anki_deck_root", "Notion::Sync")
    last_sync_time = cfg.get("last_sync_time")

    deck_name = f"{anki_deck_root}::{db_name}"

    filtros = []
    if use_sync_field and sync_prop:
        filtros.append({"property": sync_prop, "select": {"does_not_equal": sync_done}})
    if status_prop and status_val:
        filtros.append({"property": status_prop, "select": {"equals": status_val}})

    filter_obj = None
    if len(filtros) == 1:
        filter_obj = filtros[0]
    elif len(filtros) > 1:
        filter_obj = {"and": filtros}

    itens = query_database_all(db_id, filter_obj)

    if not use_sync_field and last_sync_time:
        itens = [i for i in itens if is_newer_than(i, last_sync_time)]

    if not itens:
        log.info(f"  → Nenhum item pendente em '{db_name}'.")
        return

    log.info(f"  → {len(itens)} item(s) para processar em '{db_name}'")
    garantir_deck(deck_name)
    total["disciplinas"] += 1

    for item in itens:
        titulo = get_title_value(item, title_prop) or "Sem título"
        data   = get_date_value(item, date_prop) if date_prop else ""
        log.info(f"\n  📄 {titulo}")

        partes = []
        if content_prop:
            resumo = get_rich_text_value(item, content_prop)
            if resumo:
                partes.append(resumo)
        conteudo_blocos = get_page_content(item["id"])
        if conteudo_blocos:
            partes.append(conteudo_blocos)
        conteudo = "\n\n".join(partes)

        if not conteudo.strip():
            log.info("    → Sem conteúdo. Pulando.")
            continue

        total["itens"] += 1
        cards = gerar_flashcards(db_name, titulo, data, conteudo)
        total["gerados"] += len(cards)

        if not cards:
            if use_sync_field and sync_prop:
                marcar_sincronizado(item["id"], sync_prop, "❌ Erro")
            total["erros"] += 1
            continue

        enviados = sum(adicionar_nota(deck_name, c, db_name, titulo, data) for c in cards)
        total["enviados"] += enviados
        log.info(f"    ✓ {enviados}/{len(cards)} notas no Anki")

        if use_sync_field and sync_prop:
            marcar_sincronizado(item["id"], sync_prop, sync_done)

        time.sleep(1)


def processar_plano(cfg: dict) -> dict:
    """
    Modo plano: itera por todos os databases selecionados.
    Cada database vira um subdeck separado no Anki.
    """
    total = {"disciplinas": 0, "itens": 0, "gerados": 0, "enviados": 0, "erros": 0}

    selected_dbs = cfg.get("selected_dbs")
    if not selected_dbs:
        # backwards compat: single DB
        selected_dbs = [{"id": cfg["parent_db_id"], "name": cfg.get("parent_db_name", "Sync")}]

    log.info(f"Databases selecionados: {len(selected_dbs)}")
    for entry in selected_dbs:
        db_id   = entry["id"]
        db_name = entry["name"]
        log.info(f"\n{'='*50}\n📋 {db_name}")
        _processar_db_plano(db_id, db_name, cfg, total)

    return total


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    from datetime import date
    log.info("🚀 Iniciando sincronização Notion → Anki")
    log.info(f"Data: {date.today()}")

    if not NOTION_TOKEN:
        log.error("❌ NOTION_TOKEN não configurado no .env")
        return

    if AI_PROVIDER == "gemini":
        if not GOOGLE_GENAI_AVAILABLE:
            log.error("❌ google-genai não instalado.")
            return
        if not gemini_client:
            log.error("❌ Falha ao inicializar Gemini.")
            return
    else:
        if not ANTHROPIC_AVAILABLE:
            log.error("❌ anthropic não instalado.")
            return
        if not claude_client:
            log.error("❌ Falha ao inicializar Claude.")
            return

    if not anki_disponivel():
        log.error(f"❌ Anki não disponível em {ANKI_HOST}")
        return

    cfg = load_notion_config()
    if not cfg:
        log.error("❌ Estrutura Notion não configurada.")
        log.error("   Execute o app.py e configure na aba 'Configurar Notion'.")
        return

    log.info("✓ Conexões verificadas")
    criar_modelo_basico()

    modo = cfg.get("mode", "hierarchical")
    if modo == "hierarchical":
        total = processar_hierarquico(cfg)
    else:
        total = processar_plano(cfg)

    # Salva timestamp de sync
    cfg["last_sync_time"] = datetime.now(timezone.utc).isoformat()
    save_notion_config(cfg)

    log.info(f"\n{'='*50}")
    log.info("📊 RELATÓRIO FINAL")
    log.info(f"  Categorias processadas : {total['disciplinas']}")
    log.info(f"  Itens processados      : {total['itens']}")
    log.info(f"  Flashcards gerados     : {total['gerados']}")
    log.info(f"  Flashcards no Anki     : {total['enviados']}")
    log.info(f"  Erros                  : {total['erros']}")
    log.info("✅ Sincronização concluída!")


if __name__ == "__main__":
    main()
