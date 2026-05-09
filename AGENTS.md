# AGENTS.md — Guia para Agentes de IA

## Objetivo do projeto

Automação pessoal de estudos para concurso público.

**Pipeline:** Notion (anotações de aula) → IA generativa (Claude ou Gemini) → Anki (flashcards)

O script lê aulas finalizadas no Notion, gera flashcards via IA e os insere no Anki via AnkiConnect. Após inserção, marca a aula como sincronizada no Notion para evitar reprocessamento.

---

## Arquivos

| Arquivo | Papel |
|---|---|
| `notion_anki_sync.py` | Script principal. Toda a lógica de sync. Pode rodar standalone via CLI. |
| `app.py` | Interface visual Streamlit. Chama `notion_anki_sync.py` via subprocess com live log. |
| `.env` | Credenciais e configuração de runtime (não versionado) |
| `env.example` | Template do `.env` com todas as variáveis documentadas |
| `requirements.txt` | Dependências Python diretas |
| `sync.log` | Log gerado automaticamente ao rodar (não versionado) |

### Rodar a interface

```bash
streamlit run app.py
```

### Rodar sem interface (CLI)

```bash
python notion_anki_sync.py
```

---

## Estrutura do código (`notion_anki_sync.py`)

### Seções em ordem

1. **Imports e detecção de SDKs** — imports condicionais de `anthropic` e `google.genai`; flags `ANTHROPIC_AVAILABLE` / `GOOGLE_GENAI_AVAILABLE`
2. **Configuração** — leitura de env vars; constantes `BANCO_DISCIPLINAS_DB`, `MAX_FLASHCARDS_POR_AULA`, `ANKI_DECK_RAIZ`
3. **Clientes** — `claude_client` e `gemini_client` inicializados conforme `AI_PROVIDER`
4. **Helpers Notion** — funções de leitura/escrita no Notion
5. **Helpers IA** — `SYSTEM_PROMPT` + `gerar_flashcards()`
6. **Helpers AnkiConnect** — funções de leitura/escrita no Anki
7. **Pipeline principal** — `processar_disciplina()` + `main()`

### Funções-chave

| Função | Responsabilidade |
|---|---|
| `get_all_disciplinas()` | Pagina o banco `BANCO_DISCIPLINAS_DB` no Notion |
| `find_aulas_db_id(page_id)` | Encontra o sub-banco "Aulas — Anotações por Dia" dentro de uma disciplina |
| `get_aulas_pendentes(db_id)` | Filtra aulas `Status=✅ Completa` e `Sincronização≠✅ Sincronizado` |
| `get_conteudo_aula(page)` | Extrai texto: campo "Conteúdo Resumido" + blocos internos da página |
| `extrair_texto_blocos(blocos)` | Recursão sobre blocos Notion → string plana |
| `gerar_flashcards(...)` | Chama a IA configurada (`AI_PROVIDER`); retorna `[{"front": ..., "back": ...}]` |
| `adicionar_nota(...)` | Envia flashcard ao Anki via AnkiConnect; ignora duplicatas |
| `marcar_sincronizado(page_id)` | Atualiza `Sincronização` da aula no Notion |
| `processar_disciplina(page)` | Orquestra o pipeline completo para uma disciplina |
| `main()` | Valida conexões, itera disciplinas, imprime relatório final |

---

## Estrutura Notion esperada

```
📚 Banco de Disciplinas  (database_id fixo em BANCO_DISCIPLINAS_DB)
└── Página: <Nome da Disciplina>
    └── child_database: "Aulas — Anotações por Dia"
        └── Linha (aula) com propriedades:
            - Aula         (title)
            - Data         (date)
            - Status       (select) — "✅ Completa" para processar
            - Sincronização (select) — "✅ Sincronizado" quando feito, "❌ Erro" se falhar
            - Conteúdo Resumido (rich_text) — opcional
```

O conteúdo principal da aula pode estar nos blocos internos da página (parágrafos, listas, headings, toggles, code blocks).

---

## Provedores de IA

`AI_PROVIDER` (env var) controla qual SDK é usado em `gerar_flashcards()`.

| Provider | SDK | Env vars necessárias |
|---|---|---|
| `claude` (padrão) | `anthropic` | `ANTHROPIC_API_KEY` |
| `gemini` | `google-genai` | `GEMINI_API_KEY`, opcionalmente `GEMINI_MODEL` |

Modelo Claude padrão: `claude-opus-4-5` (hardcoded em `gerar_flashcards`).  
Modelo Gemini padrão: `gemini-2.0-flash` (configurável via `GEMINI_MODEL`).

O `SYSTEM_PROMPT` é o mesmo para ambos os provedores.

---

## Estrutura de decks no Anki

```
Estudo::Concurso          ← ANKI_DECK_RAIZ
└── <Nome da Disciplina>  ← criado automaticamente por processar_disciplina()
```

Modelo de nota Anki: `Notion-Flashcard` (criado automaticamente se não existir).  
Campos: `Frente`, `Verso`, `Disciplina`, `Aula`, `Data`.  
Tags automáticas: `<Disciplina>`, `aula:<data>`, `notion-sync`.

---

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `NOTION_TOKEN` | Sim | — | Token da integração Notion |
| `AI_PROVIDER` | Não | `claude` | Provedor de IA: `claude` ou `gemini` |
| `ANTHROPIC_API_KEY` | Se claude | — | Chave API Anthropic |
| `GEMINI_API_KEY` | Se gemini | — | Chave API Google AI Studio |
| `GEMINI_MODEL` | Não | `gemini-2.0-flash` | Modelo Gemini |
| `ANKI_HOST` | Não | `http://localhost:8765` | URL do AnkiConnect |

---

## Dependências Python

```
# Claude
notion-client anthropic requests python-dotenv

# Gemini
notion-client google-genai requests python-dotenv
```

Runtime: Python 3.10+. Anki deve estar aberto com plugin AnkiConnect (código `2055492159`).

---

## Comportamento de erro e idempotência

- Flashcard duplicado → ignorado silenciosamente (AnkiConnect `allowDuplicate: false`)
- Falha na IA → aula marcada como `❌ Erro` no Notion; script continua para próxima aula
- Aula sem conteúdo → pulada sem marcação
- Script é idempotente: reprocessa apenas aulas com `Sincronização≠✅ Sincronizado`

---

## Interface visual (`app.py`)

Streamlit app. Roda localmente no browser.

**Sidebar:** configuração de todas as env vars — salva no `.env` via `python-dotenv.set_key`.

**Área principal:**
- Teste de conexões (Notion, IA, Anki) com resultado persistido em `st.session_state`
- Botão "Iniciar Sincronização" — spawna `notion_anki_sync.py` via `subprocess.Popen`, streama stdout linha a linha para um `st.empty()` placeholder (live log)
- Log completo em `st.expander` após conclusão
- Métricas finais parseadas do log (disciplinas, aulas, gerados, enviados, erros)

**Ponto importante:** `app.py` não importa `notion_anki_sync` — usa subprocess para evitar efeitos colaterais de importação (clientes inicializados no top-level). Env vars são passadas via `env` do `Popen`.

---

## O que NÃO existe (escopo intencional)

- Sem CLI de argumentos — configuração apenas via `.env`
- Sem scheduler/cron embutido — rodar manualmente ou via cron externo
- Sem suporte a múltiplos bancos raiz de disciplinas — `BANCO_DISCIPLINAS_DB` é fixo
- Sem testes automatizados
- Sem interface web ou API HTTP
