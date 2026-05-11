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

import db as sync_db

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

try:
    import openai as _openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import groq as _groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

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
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL      = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-opus-4-5")
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
openai_client = None
groq_client   = None

if AI_PROVIDER == "gemini":
    if GOOGLE_GENAI_AVAILABLE and GEMINI_API_KEY:
        gemini_client = _google_genai.Client(api_key=GEMINI_API_KEY)
elif AI_PROVIDER == "openai":
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        openai_client = _openai.OpenAI(api_key=OPENAI_API_KEY)
elif AI_PROVIDER == "groq":
    if GROQ_AVAILABLE and GROQ_API_KEY:
        groq_client = _groq.Groq(api_key=GROQ_API_KEY)
else:
    if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
        claude_client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ──────────────────────────────────────────────
# Helpers — Notion (genérico)
# ──────────────────────────────────────────────

def query_database_all(db_id: str, filter_obj: dict | None = None) -> list[dict]:
    """Pagina todas as results de uma data_source.

    Compatível com Notion API 2025-09-03 (notion-client v3+):
    - db_id agora é tratado como data_source_id.
    - Se um database_id legacy for passado, resolve para o primeiro
      data_source associado e tenta novamente.
    """
    target_id = db_id
    results = []
    cursor  = None
    while True:
        kwargs = {"start_cursor": cursor} if cursor else {}
        if filter_obj:
            kwargs["filter"] = filter_obj
        try:
            resp = notion.data_sources.query(data_source_id=target_id, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if results or cursor or "data_source" in msg or "not_found" not in msg:
                raise
            # Fallback: db_id pode ser um database_id; resolver data_source.
            try:
                db = notion.databases.retrieve(database_id=db_id)
                ds_list = db.get("data_sources") or []
                if not ds_list:
                    raise
                target_id = ds_list[0]["id"]
                continue
            except Exception:
                raise exc
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


def _list_all_block_children(block_id: str) -> list[dict]:
    """Paginate notion.blocks.children.list — default page size is 100."""
    results = []
    cursor = None
    while True:
        kwargs = {"block_id": block_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return results


def _rich(bloco: dict, tipo: str) -> str:
    rich = (bloco.get(tipo) or {}).get("rich_text", [])
    return "".join(r.get("plain_text", "") for r in rich)


def extrair_texto_blocos(blocos: list, nivel: int = 0) -> str:
    linhas = []
    indent = "  " * nivel
    for bloco in blocos:
        tipo  = bloco["type"]
        texto = ""
        recurse_children = True

        if tipo in ("paragraph", "bulleted_list_item", "numbered_list_item",
                    "toggle", "quote", "callout"):
            texto = _rich(bloco, tipo)
        elif tipo == "heading_1":
            texto = "# " + _rich(bloco, tipo)
        elif tipo == "heading_2":
            texto = "## " + _rich(bloco, tipo)
        elif tipo == "heading_3":
            texto = "### " + _rich(bloco, tipo)
        elif tipo == "code":
            lang = (bloco.get("code") or {}).get("language", "") or ""
            texto = f"```{lang}\n" + _rich(bloco, "code") + "\n```"
        elif tipo == "divider":
            texto = "---"
        elif tipo == "to_do":
            checked = "[x]" if (bloco.get("to_do") or {}).get("checked") else "[ ]"
            texto = f"{checked} " + _rich(bloco, "to_do")
        elif tipo == "child_page":
            title = (bloco.get("child_page") or {}).get("title", "")
            if title:
                texto = "## " + title
        elif tipo == "child_database":
            title = (bloco.get("child_database") or {}).get("title", "")
            if title:
                texto = f"[Tabela: {title}]"
        elif tipo == "equation":
            expr = (bloco.get("equation") or {}).get("expression", "")
            if expr:
                texto = f"$$ {expr} $$"
        elif tipo == "bookmark":
            url = (bloco.get("bookmark") or {}).get("url", "")
            cap = "".join(r.get("plain_text", "")
                          for r in (bloco.get("bookmark") or {}).get("caption", []))
            if cap and url:
                texto = f"[{cap}]({url})"
            elif url:
                texto = url
        elif tipo in ("image", "video", "file", "pdf", "audio"):
            cap = "".join(r.get("plain_text", "")
                          for r in (bloco.get(tipo) or {}).get("caption", []))
            if cap:
                texto = f"[{tipo}: {cap}]"
        elif tipo == "table_row":
            cells = (bloco.get("table_row") or {}).get("cells", [])
            row = " | ".join(
                "".join(r.get("plain_text", "") for r in (cell or []))
                for cell in cells
            )
            if row.strip():
                texto = "| " + row + " |"
            recurse_children = False  # table_row has no further children
        elif tipo in ("table", "column_list", "column", "synced_block"):
            pass  # render through children only

        if texto.strip():
            linhas.append(f"{indent}{texto}")
        if recurse_children and bloco.get("has_children"):
            try:
                filhos = _list_all_block_children(bloco["id"])
                child_text = extrair_texto_blocos(filhos, nivel + 1)
                if child_text.strip():
                    linhas.append(child_text)
            except Exception:
                pass
    return "\n".join(linhas)


def get_page_content(page_id: str) -> str:
    """Extrai conteúdo textual de blocos internos de uma página.

    Pagina blocks.children.list para cobrir páginas longas (>100 blocos)
    e descende recursivamente em blocos com filhos.
    """
    try:
        blocos = _list_all_block_children(page_id)
        return extrair_texto_blocos(blocos).strip()
    except Exception as e:
        log.warning(f"Erro ao ler blocos de {page_id}: {e}")
        return ""


def find_child_database(page_id: str, keyword: str) -> str | None:
    """Busca child_database cujo título contenha keyword.

    Retorna o data_source_id (não o database_id) para uso direto em
    data_sources.query. Em databases multi-source, escolhe a data_source
    cujo nome contenha a keyword; se nenhuma casar, usa a primeira.
    """
    try:
        children = notion.blocks.children.list(block_id=page_id)
        for block in children["results"]:
            if block["type"] != "child_database":
                continue
            title = block["child_database"].get("title", "")
            if keyword.lower() not in title.lower():
                continue
            db_block_id = block["id"]
            try:
                db = notion.databases.retrieve(database_id=db_block_id)
                sources = db.get("data_sources") or []
                if not sources:
                    return db_block_id  # fallback legacy
                for src in sources:
                    if keyword.lower() in (src.get("name") or "").lower():
                        return src["id"]
                return sources[0]["id"]
            except Exception:
                return db_block_id
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

SYSTEM_PROMPT = """Você é um especialista em criar flashcards para revisão
espaçada no Anki. Sua tarefa é converter anotações de estudo em flashcards
de alta qualidade que aproveitem a *active recall*.

═══ PRINCÍPIOS (regras de SuperMemo, adaptadas) ═══

1. ATOMICIDADE — cada flashcard testa UM único fato/conceito. Se a anotação
   tem 3 ideias, crie 3 cards separados — nunca um card composto.

2. INFORMAÇÃO MÍNIMA — formule a pergunta da forma mais simples possível.
   Se o verso tem mais que 2 frases curtas, divida em mais cards.

3. ACTIVE RECALL — a frente DEVE ser uma pergunta direta, lacuna a preencher
   ou afirmação a completar. Nunca uma simples afirmação informativa.

4. EVITE ENUMERAÇÕES NO VERSO — se a resposta é "1) X, 2) Y, 3) Z", crie
   três cards (um por item) ou use cloze deletions.

5. CONTEXTO MÍNIMO NA FRENTE — inclua só o necessário para tornar a
   pergunta inequívoca. Não copie parágrafos inteiros.

6. PRIORIZE: definições, leis/normas, fórmulas, datas-chave, comparações
   "X vs Y", causa↔efeito, exceções, exemplos canônicos, vocabulário.

7. EVITE: perguntas sim/não, perguntas vagas ("o que é importante?"),
   reproduzir frases longas literalmente, repetir o mesmo conceito em
   cards quase iguais.

8. LINGUAGEM — use o MESMO idioma da anotação (português se PT, inglês se
   EN). Tom direto, vocabulário técnico preservado.

9. NOMES PRÓPRIOS — sempre incluídos quando relevantes (autores, leis,
   datas históricas, casos específicos).

10. SEM META-COMENTÁRIOS — nunca escreva "de acordo com a anotação...",
    "no texto consta...". Trate o card como conhecimento autônomo.

═══ FORMATO DE SAÍDA ═══

Retorne EXCLUSIVAMENTE um array JSON válido, sem markdown, sem
comentários, sem texto adicional:

[
  {"front": "...", "back": "..."},
  ...
]

Cada objeto tem APENAS as chaves "front" e "back" (strings não vazias).
Se a anotação for vazia ou trivial, retorne [].
"""


def _strip_code_fence(raw: str) -> str:
    """Remove cercas markdown ```json ... ``` se IA insistir em retornar."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Remove primeira linha (```json ou ```) e última cerca
        lines = raw.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _validate_and_clean(cards: list, max_cards: int) -> list[dict]:
    """Filtra cards malformados, deduplica por frente normalizada, aplica teto."""
    if max_cards <= 0:
        return []
    out = []
    seen = set()
    for c in cards:
        if not isinstance(c, dict):
            continue
        front = (c.get("front") or "").strip()
        back  = (c.get("back")  or "").strip()
        if not front or not back:
            continue
        key = front.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"front": front, "back": back})
        if len(out) >= max_cards:
            break
    return out


def gerar_flashcards(
    categoria: str,
    titulo: str,
    data: str,
    conteudo: str,
    max_cards: int = MAX_FLASHCARDS_POR_AULA,
) -> list[dict]:
    """Gera ATÉ `max_cards` flashcards para UM item de conteúdo.

    Limite é por chamada — cada item (aula/dia) tem seu próprio orçamento.
    O loop em `processar_*` chama esta função uma vez por item, então o
    teto se aplica naturalmente por aula → matéria → todo o pipeline.
    """
    prompt = f"""Crie flashcards a partir das anotações abaixo.

CONTEXTO
- Matéria/Categoria: {categoria}
- Título do conteúdo: {titulo}
- Data: {data or "(não informada)"}
- Tamanho: {len(conteudo)} caracteres

ORÇAMENTO
- Gere NO MÁXIMO {max_cards} flashcards.
- É melhor entregar POUCOS cards excelentes do que muitos medianos.
- Se a anotação for curta/trivial, retorne menos cards (ou [] se vazia).

ANOTAÇÃO
\"\"\"
{conteudo}
\"\"\"

Responda APENAS com o array JSON conforme as regras do sistema."""

    provider_label = {
        "gemini": "Gemini",
        "openai": "ChatGPT (OpenAI)",
        "groq":   "Groq",
        "claude": "Claude",
    }.get(AI_PROVIDER, "Claude")
    log.info(f"  → Enviando para {provider_label}: {len(conteudo)} caracteres "
             f"(orçamento: até {max_cards} cards)")

    raw = ""
    try:
        if AI_PROVIDER == "gemini":
            cfg_kwargs = {
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens":  4096,
                "temperature":        0.3,
                "top_p":              0.9,
                "response_mime_type": "application/json",
            }
            try:
                gen_cfg = _genai_types.GenerateContentConfig(**cfg_kwargs)
            except TypeError:
                gen_cfg = _genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=4096,
                )
            resp = gemini_client.models.generate_content(
                model=GEMINI_MODEL, config=gen_cfg, contents=prompt,
            )
            raw = (resp.text or "").strip()
        elif AI_PROVIDER == "openai":
            resp = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system",
                     "content": SYSTEM_PROMPT + "\n\nResponda com um objeto JSON "
                                "que tenha a chave \"flashcards\" contendo o array."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
        elif AI_PROVIDER == "groq":
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system",
                     "content": SYSTEM_PROMPT + "\n\nResponda com um objeto JSON "
                                "que tenha a chave \"flashcards\" contendo o array."},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
        else:
            resp = claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                temperature=0.3,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()

        raw = _strip_code_fence(raw)
        parsed = json.loads(raw)
        # OpenAI/Groq with response_format=json_object return {"flashcards": [...]}
        # — unwrap the array if present.
        if isinstance(parsed, dict):
            for key in ("flashcards", "cards", "data", "items"):
                val = parsed.get(key)
                if isinstance(val, list):
                    parsed = val
                    break
        if not isinstance(parsed, list):
            log.error(f"  ✗ Resposta não é array JSON: {type(parsed).__name__}")
            return []

        cards = _validate_and_clean(parsed, max_cards)
        dropped = len(parsed) - len(cards)
        if dropped > 0:
            log.info(f"  → {len(cards)}/{max_cards} flashcards válidos "
                     f"({dropped} descartados: malformados ou duplicados)")
        else:
            log.info(f"  → {len(cards)}/{max_cards} flashcards gerados")
        return cards

    except json.JSONDecodeError as e:
        log.error(f"  ✗ Erro ao parsear JSON: {e}\n     Resposta: {raw[:300]}")
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
# Helpers — Local DB (sync_history)
# ──────────────────────────────────────────────

# Set by main() at the start of each run; consumed by _record() helpers.
_CURRENT_RUN_ID: int | None = None


def _record(page: dict, db_id: str, db_name: str, title: str,
            category: str | None, status: str,
            cards_generated: int = 0, cards_inserted: int = 0,
            error_msg: str | None = None) -> None:
    try:
        sync_db.record_sync(
            page_id=page["id"], db_id=db_id, db_name=db_name,
            title=title, category=category,
            page_last_edited_at=page.get("last_edited_time"),
            cards_generated=cards_generated, cards_inserted=cards_inserted,
            status=status, error_msg=error_msg, run_id=_CURRENT_RUN_ID,
        )
    except Exception as exc:
        log.warning(f"Falha ao gravar histórico local: {exc}")


def _filter_locally_pending(itens: list[dict]) -> tuple[list[dict], int]:
    """Remove pages already synced (locally) and unedited since.
    Returns (pending, skipped_count)."""
    pending = sync_db.filter_pending(itens)
    return pending, len(itens) - len(pending)


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

        # 5. Filtro local — descarta páginas já sincronizadas localmente e
        #    inalteradas desde então (independente do estado no Notion).
        itens, skipped = _filter_locally_pending(itens)
        if skipped:
            log.info(f"  → {skipped} item(s) pulado(s) (já sincronizados localmente)")

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
                _record(item, child_db_id, nome_categoria, titulo, nome_categoria,
                        "skipped", error_msg="sem conteúdo")
                continue

            total["itens"] += 1

            # Gera flashcards
            cards = gerar_flashcards(nome_categoria, titulo, data, conteudo)
            total["gerados"] += len(cards)

            if not cards:
                if use_sync_field and child_sync_prop:
                    marcar_sincronizado(item["id"], child_sync_prop, "❌ Erro")
                total["erros"] += 1
                _record(item, child_db_id, nome_categoria, titulo, nome_categoria,
                        "error", cards_generated=0, cards_inserted=0,
                        error_msg="IA não retornou flashcards válidos")
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

            _record(item, child_db_id, nome_categoria, titulo, nome_categoria,
                    "success", cards_generated=len(cards), cards_inserted=enviados)
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

    itens, skipped = _filter_locally_pending(itens)
    if skipped:
        log.info(f"  → {skipped} item(s) pulado(s) (já sincronizados localmente)")

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
            _record(item, db_id, db_name, titulo, db_name,
                    "skipped", error_msg="sem conteúdo")
            continue

        total["itens"] += 1
        cards = gerar_flashcards(db_name, titulo, data, conteudo)
        total["gerados"] += len(cards)

        if not cards:
            if use_sync_field and sync_prop:
                marcar_sincronizado(item["id"], sync_prop, "❌ Erro")
            total["erros"] += 1
            _record(item, db_id, db_name, titulo, db_name,
                    "error", cards_generated=0, cards_inserted=0,
                    error_msg="IA não retornou flashcards válidos")
            continue

        enviados = sum(adicionar_nota(deck_name, c, db_name, titulo, data) for c in cards)
        total["enviados"] += enviados
        log.info(f"    ✓ {enviados}/{len(cards)} notas no Anki")

        if use_sync_field and sync_prop:
            marcar_sincronizado(item["id"], sync_prop, sync_done)

        _record(item, db_id, db_name, titulo, db_name,
                "success", cards_generated=len(cards), cards_inserted=enviados)
        time.sleep(1)


_PER_DB_OVERRIDE_KEYS = (
    "parent_name_prop", "child_db_keyword", "child_title_prop",
    "child_content_prop", "child_date_prop", "use_sync_field",
    "child_sync_prop", "child_sync_done",
    "child_status_prop", "child_status_complete",
)


def processar_plano(cfg: dict) -> dict:
    """
    Modo plano: itera por todos os databases selecionados.
    Cada database vira um subdeck separado no Anki.

    Se a entrada de `selected_dbs` tem chave `props` (mapeamento por tabela
    salvo pela UI multi-DB), seus valores sobrescrevem os do `cfg` para
    AQUELA tabela. Mantém back-compat: entradas sem `props` usam o cfg
    global (comportamento antigo).
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
        sub_cfg = dict(cfg)
        per_db_props = entry.get("props") or {}
        if per_db_props:
            for k in _PER_DB_OVERRIDE_KEYS:
                if k in per_db_props:
                    sub_cfg[k] = per_db_props[k]
        _processar_db_plano(db_id, db_name, sub_cfg, total)

    return total


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    global _CURRENT_RUN_ID
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
    elif AI_PROVIDER == "openai":
        if not OPENAI_AVAILABLE:
            log.error("❌ openai não instalado. Rode: pip install openai")
            return
        if not openai_client:
            log.error("❌ Falha ao inicializar OpenAI (ChatGPT). "
                      "Verifique OPENAI_API_KEY.")
            return
    elif AI_PROVIDER == "groq":
        if not GROQ_AVAILABLE:
            log.error("❌ groq não instalado. Rode: pip install groq")
            return
        if not groq_client:
            log.error("❌ Falha ao inicializar Groq. Verifique GROQ_API_KEY.")
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

    # Telemetria local — banco SQLite.
    try:
        sync_db.init_db()
        _CURRENT_RUN_ID = sync_db.start_run(
            mode=cfg.get("mode"),
            provider=AI_PROVIDER,
            model={
                "gemini": GEMINI_MODEL,
                "openai": OPENAI_MODEL,
                "groq":   GROQ_MODEL,
            }.get(AI_PROVIDER, CLAUDE_MODEL),
        )
    except Exception as exc:
        log.warning(f"DB local indisponível ({exc}); continuando sem telemetria.")
        _CURRENT_RUN_ID = None

    modo = cfg.get("mode", "hierarchical")
    run_status = "success"
    try:
        if modo == "hierarchical":
            total = processar_hierarquico(cfg)
        else:
            total = processar_plano(cfg)
    except Exception:
        run_status = "error"
        raise
    finally:
        if _CURRENT_RUN_ID is not None:
            try:
                stats = locals().get("total") or {"itens": 0, "gerados": 0,
                                                  "enviados": 0, "erros": 0}
                sync_db.finish_run(
                    _CURRENT_RUN_ID,
                    status=run_status if stats.get("erros", 0) == 0 else (
                        run_status if run_status == "error" else "success"
                    ),
                    items_processed=stats.get("itens", 0),
                    cards_generated=stats.get("gerados", 0),
                    cards_inserted=stats.get("enviados", 0),
                    errors=stats.get("erros", 0),
                )
            except Exception as exc:
                log.warning(f"Falha ao finalizar run no DB local: {exc}")
            _CURRENT_RUN_ID = None

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
