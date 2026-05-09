#!/usr/bin/env python3
"""
notion_anki_sync.py
====================
Automação: Notion (Aulas) → IA (Claude ou Gemini) → Anki

Fluxo:
1. Lê o 📚 Banco de Disciplinas no Notion
2. Para cada disciplina, busca o banco "Aulas — Anotações por Dia"
3. Filtra aulas com conteúdo e ainda não sincronizadas
4. Envia o conteúdo para a IA configurada gerar flashcards inteligentes
5. Cria as notas no Anki via AnkiConnect
6. Atualiza o campo "Sincronização" da aula no Notion

Requisitos (Claude):
    pip install notion-client anthropic requests python-dotenv

Requisitos (Gemini):
    pip install notion-client google-genai requests python-dotenv

Configuração (.env):
    NOTION_TOKEN=secret_...
    AI_PROVIDER=claude          # ou gemini
    ANTHROPIC_API_KEY=sk-ant-...
    GEMINI_API_KEY=AIza...
    GEMINI_MODEL=gemini-2.0-flash
    ANKI_HOST=http://localhost:8765   (padrão)
"""

import os
import json
import time
import logging
import requests
from datetime import date
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

NOTION_TOKEN      = os.getenv("NOTION_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
AI_PROVIDER       = os.getenv("AI_PROVIDER", "claude").lower()
ANKI_HOST         = os.getenv("ANKI_HOST", "http://localhost:8765")

# IDs do seu Notion (já mapeados automaticamente)
BANCO_DISCIPLINAS_DB = "ceab689d-5373-49f8-8edd-99836cd3aee6"   # 📚 Banco de Disciplinas
AULAS_DB_PROPERTY    = "Aulas — Anotações por Dia"               # nome do banco interno

# Quantos flashcards gerar por aula (a IA pode gerar menos se o conteúdo for curto)
MAX_FLASHCARDS_POR_AULA = int(os.getenv("MAX_FLASHCARDS_POR_AULA", "10"))

# Deck raiz no Anki — subdecks serão criados por disciplina
ANKI_DECK_RAIZ = "Estudo::Concurso"


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
# Helpers — Notion
# ──────────────────────────────────────────────

def get_all_disciplinas() -> list[dict]:
    """Retorna todas as páginas do 📚 Banco de Disciplinas."""
    results = []
    cursor  = None
    while True:
        resp = notion.data_sources.query(
            BANCO_DISCIPLINAS_DB,
            start_cursor=cursor,
        )
        results.extend(resp["results"])
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
    log.info(f"Disciplinas encontradas: {len(results)}")
    return results


def get_nome_disciplina(page: dict) -> str:
    """Extrai o nome da disciplina da página."""
    try:
        return page["properties"]["Disciplina"]["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return "Disciplina Desconhecida"


def find_aulas_db_id(disciplina_page_id: str) -> str | None:
    """
    Busca o banco 'Aulas — Anotações por Dia' dentro da página da disciplina.
    Retorna o database_id ou None se não encontrar.
    """
    try:
        children = notion.blocks.children.list(block_id=disciplina_page_id)
        for block in children["results"]:
            if block["type"] == "child_database":
                title = block["child_database"].get("title", "")
                if "Aulas" in title or "Anotações" in title:
                    return block["id"]
    except Exception as e:
        log.warning(f"Erro ao buscar banco de aulas em {disciplina_page_id}: {e}")
    return None


def get_aulas_pendentes(aulas_db_id: str) -> list[dict]:
    """
    Retorna aulas com:
    - Conteúdo Resumido preenchido OU conteúdo interno (verificado depois)
    - Sincronização != "✅ Sincronizado"
    - Status == "✅ Completa" (anotação finalizada)
    """
    results = []
    cursor  = None
    while True:
        resp = notion.data_sources.query(
            aulas_db_id,
            filter={
                "and": [
                    {
                        "property": "Sincronização",
                        "select": {"does_not_equal": "✅ Sincronizado"},
                    },
                    {
                        "property": "Status",
                        "select": {"equals": "✅ Completa"},
                    },
                ]
            },
            start_cursor=cursor,
        )
        results.extend(resp["results"])
        if not resp["has_more"]:
            break
        cursor = resp["next_cursor"]
    return results


def get_conteudo_aula(aula_page: dict) -> str:
    """
    Monta o conteúdo textual completo da aula:
    1. Campo "Conteúdo Resumido" (propriedade)
    2. Blocos de texto dentro da página
    """
    partes = []

    # 1. Propriedade "Conteúdo Resumido"
    try:
        resumo = aula_page["properties"]["Conteúdo Resumido"]["rich_text"]
        if resumo:
            texto = "".join(r["plain_text"] for r in resumo)
            if texto.strip():
                partes.append(f"[Resumo]\n{texto.strip()}")
    except KeyError:
        pass

    # 2. Conteúdo dos blocos internos da página
    try:
        blocos = notion.blocks.children.list(block_id=aula_page["id"])
        textos_blocos = extrair_texto_blocos(blocos["results"])
        if textos_blocos.strip():
            partes.append(f"[Anotações]\n{textos_blocos.strip()}")
    except Exception as e:
        log.warning(f"Erro ao ler blocos da aula {aula_page['id']}: {e}")

    return "\n\n".join(partes)


def extrair_texto_blocos(blocos: list, nivel: int = 0) -> str:
    """Extrai texto recursivamente de blocos Notion."""
    linhas = []
    indent = "  " * nivel

    for bloco in blocos:
        tipo = bloco["type"]
        texto = ""

        # Tipos com rich_text
        if tipo in ("paragraph", "bulleted_list_item", "numbered_list_item",
                    "toggle", "quote", "callout"):
            rich = bloco[tipo].get("rich_text", [])
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

        # Filhos recursivos (toggles, listas aninhadas)
        if bloco.get("has_children"):
            try:
                filhos = notion.blocks.children.list(block_id=bloco["id"])
                linhas.append(extrair_texto_blocos(filhos["results"], nivel + 1))
            except Exception:
                pass

    return "\n".join(linhas)


def get_titulo_aula(aula_page: dict) -> str:
    try:
        return aula_page["properties"]["Aula"]["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return "Aula sem título"


def get_data_aula(aula_page: dict) -> str:
    try:
        return aula_page["properties"]["Data"]["date"]["start"] or ""
    except (KeyError, TypeError):
        return ""


def marcar_sincronizado(aula_page_id: str, status: str = "✅ Sincronizado"):
    """Atualiza o campo Sincronização da aula no Notion."""
    notion.pages.update(
        page_id=aula_page_id,
        properties={
            "Sincronização": {
                "select": {"name": status}
            }
        }
    )


# ──────────────────────────────────────────────
# Helpers — Claude AI
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """Você é um especialista em criação de flashcards para estudos de concurso público.
Seu objetivo é transformar anotações de aula em flashcards eficientes para revisão no Anki.

Regras para gerar bons flashcards:
- Cada flashcard deve testar UM conceito específico
- A frente (front) deve ser uma pergunta direta ou afirmação incompleta
- O verso (back) deve ser conciso, claro e completo
- Priorize: definições, leis/normas, fórmulas, comparações, exceções, exemplos-chave
- Evite flashcards muito genéricos ou com respostas longas demais
- Para conteúdo contábil: foque em lançamentos, demonstrações, critérios de avaliação
- Para conteúdo jurídico: foque em artigos, princípios, conceitos, distinções
- Para conteúdo de gestão: foque em conceitos, autores, teorias, funções

Retorne SOMENTE um array JSON válido, sem markdown, sem explicações, apenas o JSON:
[
  {"front": "pergunta ou afirmação incompleta", "back": "resposta completa"},
  ...
]"""


def gerar_flashcards(
    disciplina: str,
    titulo_aula: str,
    data_aula: str,
    conteudo: str,
    max_cards: int = MAX_FLASHCARDS_POR_AULA
) -> list[dict]:
    """Envia o conteúdo da aula para a IA configurada e retorna lista de flashcards."""

    prompt = f"""Disciplina: {disciplina}
Aula: {titulo_aula}
Data: {data_aula}

Conteúdo das anotações:
---
{conteudo}
---

Gere até {max_cards} flashcards com base nesse conteúdo.
Lembre: retorne SOMENTE o array JSON, sem nenhum texto adicional."""

    provider_label = "Gemini" if AI_PROVIDER == "gemini" else "Claude"
    log.info(f"  → Enviando para {provider_label}: {len(conteudo)} caracteres de conteúdo")

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

        # Remove possíveis cercas de markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        flashcards = json.loads(raw)
        log.info(f"  → {len(flashcards)} flashcards gerados")
        return flashcards

    except json.JSONDecodeError as e:
        log.error(f"  ✗ Erro ao parsear JSON da IA: {e}\nResposta: {raw[:300]}")
        return []
    except Exception as e:
        log.error(f"  ✗ Erro na API {provider_label}: {e}")
        return []


# ──────────────────────────────────────────────
# Helpers — AnkiConnect
# ──────────────────────────────────────────────

def anki_request(action: str, **params) -> dict:
    """Faz uma requisição para o AnkiConnect."""
    payload = {"action": action, "version": 6, "params": params}
    resp = requests.post(ANKI_HOST, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise Exception(f"AnkiConnect error: {data['error']}")
    return data.get("result")


def garantir_deck(deck_name: str):
    """Cria o deck no Anki se não existir."""
    anki_request("createDeck", deck=deck_name)


def modelo_existe(model_name: str) -> bool:
    modelos = anki_request("modelNames")
    return model_name in modelos


def criar_modelo_basico():
    """Cria o modelo 'Notion-Flashcard' se não existir."""
    model_name = "Notion-Flashcard"
    if modelo_existe(model_name):
        return model_name

    anki_request(
        "createModel",
        modelName=model_name,
        inOrderFields=["Frente", "Verso", "Disciplina", "Aula", "Data"],
        css="""
            .card { font-family: 'Segoe UI', Arial, sans-serif; font-size: 18px;
                    text-align: center; color: #1a1a1a; background: #fafafa; padding: 20px; }
            .tag { font-size: 12px; color: #888; margin-bottom: 8px; }
            hr { border-color: #ddd; }
        """,
        cardTemplates=[{
            "Name": "Card 1",
            "Front": """<div class="tag">{{Disciplina}} · {{Aula}}</div>
<div>{{Frente}}</div>""",
            "Back": """{{FrontSide}}
<hr>
<div>{{Verso}}</div>
<div class="tag" style="margin-top:12px">📅 {{Data}}</div>""",
        }],
    )
    log.info("  → Modelo 'Notion-Flashcard' criado no Anki")
    return model_name


def adicionar_nota(deck: str, flashcard: dict, disciplina: str, aula: str, data: str) -> bool:
    """Adiciona uma nota no Anki. Retorna True se criada com sucesso."""
    model_name = "Notion-Flashcard"
    try:
        anki_request(
            "addNote",
            note={
                "deckName": deck,
                "modelName": model_name,
                "fields": {
                    "Frente":      flashcard["front"],
                    "Verso":       flashcard["back"],
                    "Disciplina":  disciplina,
                    "Aula":        aula,
                    "Data":        data,
                },
                "options": {
                    "allowDuplicate": False,
                    "duplicateScope": "deck",
                },
                "tags": [
                    disciplina.replace(" ", "_"),
                    f"aula:{data}" if data else "sem_data",
                    "notion-sync",
                ],
            },
        )
        return True
    except Exception as e:
        if "duplicate" in str(e).lower():
            log.debug(f"    (duplicado ignorado)")
        else:
            log.warning(f"    ✗ Erro ao adicionar nota: {e}")
        return False


def anki_disponivel() -> bool:
    """Verifica se o Anki está aberto e o AnkiConnect está ativo."""
    try:
        anki_request("version")
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# Pipeline principal
# ──────────────────────────────────────────────

def processar_disciplina(disciplina_page: dict) -> dict:
    """Processa uma disciplina: busca aulas, gera flashcards, envia ao Anki."""
    nome   = get_nome_disciplina(disciplina_page)
    page_id = disciplina_page["id"]

    log.info(f"\n{'='*50}")
    log.info(f"📚 Disciplina: {nome}")

    stats = {"disciplina": nome, "aulas": 0, "flashcards_gerados": 0,
             "flashcards_enviados": 0, "erros": 0}

    # 1. Encontra o banco "Aulas — Anotações por Dia"
    aulas_db_id = find_aulas_db_id(page_id)
    if not aulas_db_id:
        log.info("  → Sem banco de aulas. Pulando.")
        return stats

    # 2. Busca aulas pendentes (Completas e não sincronizadas)
    aulas = get_aulas_pendentes(aulas_db_id)
    if not aulas:
        log.info("  → Nenhuma aula pendente de sincronização.")
        return stats

    log.info(f"  → {len(aulas)} aula(s) para processar")

    # 3. Deck no Anki: "Estudo::Concurso::NomeDisciplina"
    deck_name = f"{ANKI_DECK_RAIZ}::{nome}"
    garantir_deck(deck_name)

    for aula in aulas:
        titulo = get_titulo_aula(aula)
        data   = get_data_aula(aula)
        log.info(f"\n  📄 Aula: {titulo} ({data})")

        # 4. Monta conteúdo
        conteudo = get_conteudo_aula(aula)
        if not conteudo.strip():
            log.info("    → Sem conteúdo. Pulando.")
            continue

        stats["aulas"] += 1

        # 5. Gera flashcards com IA
        flashcards = gerar_flashcards(nome, titulo, data, conteudo)
        stats["flashcards_gerados"] += len(flashcards)

        if not flashcards:
            marcar_sincronizado(aula["id"], "❌ Erro")
            stats["erros"] += 1
            continue

        # 6. Envia ao Anki
        enviados = 0
        for card in flashcards:
            if adicionar_nota(deck_name, card, nome, titulo, data):
                enviados += 1

        stats["flashcards_enviados"] += enviados
        log.info(f"    ✓ {enviados}/{len(flashcards)} notas adicionadas ao Anki")

        # 7. Marca como sincronizado no Notion
        marcar_sincronizado(aula["id"], "✅ Sincronizado")
        log.info(f"    ✓ Marcado como sincronizado no Notion")

        # Pequena pausa para não sobrecarregar a API
        time.sleep(1)

    return stats


def main():
    log.info("🚀 Iniciando sincronização Notion → Anki")
    log.info(f"Data: {date.today()}")

    # Verifica conexões
    if not NOTION_TOKEN:
        log.error("❌ NOTION_TOKEN não configurado no .env")
        return

    if AI_PROVIDER == "gemini":
        if not GOOGLE_GENAI_AVAILABLE:
            log.error("❌ google-genai não instalado. Execute: pip install google-genai")
            return
        if not GEMINI_API_KEY:
            log.error("❌ GEMINI_API_KEY não configurado no .env")
            return
        if gemini_client is None:
            log.error("❌ Falha ao inicializar cliente Gemini")
            return
    else:
        if not ANTHROPIC_AVAILABLE:
            log.error("❌ anthropic não instalado. Execute: pip install anthropic")
            return
        if not ANTHROPIC_API_KEY:
            log.error("❌ ANTHROPIC_API_KEY não configurado no .env")
            return
        if claude_client is None:
            log.error("❌ Falha ao inicializar cliente Claude")
            return

    if not anki_disponivel():
        log.error(f"❌ Anki não está disponível em {ANKI_HOST}")
        log.error("   Certifique-se que o Anki está aberto e o plugin AnkiConnect está instalado.")
        return

    provider_label = "Gemini" if AI_PROVIDER == "gemini" else "Claude"
    log.info(f"✓ Conexões verificadas (Notion, {provider_label}, Anki)")

    # Garante que o modelo de flashcard existe no Anki
    criar_modelo_basico()

    # Busca todas as disciplinas
    disciplinas = get_all_disciplinas()

    # Processa cada uma
    total_stats = {
        "disciplinas_processadas": 0,
        "aulas_processadas": 0,
        "flashcards_gerados": 0,
        "flashcards_enviados": 0,
        "erros": 0,
    }

    for disc_page in disciplinas:
        stats = processar_disciplina(disc_page)
        if stats["aulas"] > 0:
            total_stats["disciplinas_processadas"] += 1
        total_stats["aulas_processadas"]    += stats["aulas"]
        total_stats["flashcards_gerados"]   += stats["flashcards_gerados"]
        total_stats["flashcards_enviados"]  += stats["flashcards_enviados"]
        total_stats["erros"]                += stats["erros"]

    # Relatório final
    log.info(f"\n{'='*50}")
    log.info("📊 RELATÓRIO FINAL")
    log.info(f"  Disciplinas processadas : {total_stats['disciplinas_processadas']}")
    log.info(f"  Aulas processadas       : {total_stats['aulas_processadas']}")
    log.info(f"  Flashcards gerados      : {total_stats['flashcards_gerados']}")
    log.info(f"  Flashcards no Anki      : {total_stats['flashcards_enviados']}")
    log.info(f"  Erros                   : {total_stats['erros']}")
    log.info("✅ Sincronização concluída!")


if __name__ == "__main__":
    main()
