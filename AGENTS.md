# AGENTS.md — Guia completo para agentes de IA

Leia este arquivo antes de qualquer manipulação do projeto.
Ele mapeia cada arquivo, cada função relevante e o fluxo de dados completo.

---

## Objetivo do sistema

Pipeline: **Notion → IA → Anki**

```
Usuário escreve anotações no Notion
    ↓
App lê os databases configurados
    ↓
IA (Claude ou Gemini) gera flashcards a partir do conteúdo
    ↓
AnkiConnect insere os flashcards no Anki desktop
    ↓
Notion é atualizado: itens marcados como sincronizados
```

---

## Mapa de arquivos

```
anke-notion/
├── start.bat              ← Entrada Windows (duplo clique)
├── start.sh               ← Entrada Linux
├── start.command          ← Entrada macOS (duplo clique no Finder)
├── launcher.py            ← Instalador + launcher cross-platform
├── app.py                 ← Interface Streamlit (UI principal)
├── notion_anki_sync.py    ← Motor de sincronização (core)
├── requirements.txt       ← Dependências Python
├── requirements-dev.txt   ← Dependências de desenvolvimento/testes
├── .env                   ← Credenciais runtime (NÃO versionado)
├── env.example            ← Template do .env
├── notion_config.json     ← Config da estrutura Notion (NÃO versionado, gerado pelo app)
├── icon.svg               ← Ícone fonte (versionado)
├── icon.png               ← Ícone gerado pelo launcher (NÃO versionado)
├── sync.log               ← Log da última sincronização (NÃO versionado)
├── streamlit_server.log   ← Log do servidor Streamlit (NÃO versionado)
└── tests/
    ├── test_sync_helpers.py   ← Testes do motor de sync
    ├── test_app_helpers.py    ← Testes dos helpers do app
    └── test_launcher.py       ← Testes do launcher
```

---

## Fluxo de execução completo

### Inicialização (launcher.py)

```
start.bat / start.sh / start.command
    ↓
launcher.py  ← roda com Python do SISTEMA (sem venv ainda)
    │
    ├─ VENV não existe?
    │   └─ subprocess: python -m venv .venv
    │
    ├─ customtkinter ausente no venv?
    │   └─ subprocess: pip install customtkinter  (terminal com barra de progresso)
    │
    ├─ re-exec: subprocess.run([venv/python, launcher.py])  ← reinicia do venv
    │
    └─ (agora rodando do venv Python)
        ├─ .installed ausente OU requirements.txt mais novo?
        │   └─ InstallerWindow (customtkinter GUI)
        │       └─ pip install -r requirements.txt  (GUI progress bar)
        │
        ├─ _generate_icon()  → cria icon.png via Pillow
        │
        └─ _launch_streamlit()
            ├─ cria ~/.streamlit/credentials.toml (evita prompt de email)
            ├─ Popen: streamlit run app.py --headless=true
            ├─ _wait_for_server()  ← polling http://localhost:8501
            └─ _launch_webview()  → janela nativa (pywebview)
                └─ fallback: webbrowser.open()
```

### Sincronização (notion_anki_sync.py via app.py)

```
app.py  →  subprocess.Popen([venv/python, notion_anki_sync.py])
    ↓
notion_anki_sync.py
    ├─ load_dotenv()
    ├─ load_notion_config()  ← notion_config.json
    │
    ├─ MODO "hierarchical":
    │   ├─ query_database_all(parent_db_id)  ← lista categorias
    │   └─ para cada categoria:
    │       ├─ find_child_database(page_id, keyword)
    │       ├─ query_database_all(child_db_id, filter)
    │       └─ para cada item pendente:
    │           ├─ get_page_content() + get_rich_text_value()
    │           ├─ gerar_flashcards()  → Claude ou Gemini
    │           ├─ adicionar_nota()  → AnkiConnect
    │           └─ marcar_sincronizado()  → Notion
    │
    ├─ MODO "flat":
    │   └─ query_database_all(parent_db_id, filter)
    │       └─ mesma pipeline por item
    │
    └─ save_notion_config()  ← atualiza last_sync_time
```

---

## Arquivo: `notion_anki_sync.py`

Motor de sincronização. **Pode rodar standalone:** `python notion_anki_sync.py`

### Seções (em ordem no arquivo)

| Seção | Conteúdo |
|---|---|
| Imports e SDK detection | imports condicionais `anthropic` / `google.genai`; flags `ANTHROPIC_AVAILABLE` / `GOOGLE_GENAI_AVAILABLE` |
| Config | `load_dotenv()`, constantes, `load_notion_config()` / `save_notion_config()` |
| Clientes | `claude_client`, `gemini_client` — inicializados conforme `AI_PROVIDER` |
| Helpers Notion genéricos | funções de leitura de propriedades e blocos |
| Helpers IA | `SYSTEM_PROMPT` + `gerar_flashcards()` |
| Helpers AnkiConnect | criação de decks, notas, modelos |
| Pipeline hierárquico | `processar_hierarquico()` |
| Pipeline plano | `processar_plano()` |
| Main | validação de conexões → despacha para o pipeline correto → salva config |

### Funções-chave

| Função | Assinatura | Responsabilidade |
|---|---|---|
| `load_notion_config` | `() → dict \| None` | Lê `notion_config.json`; retorna None se não existir |
| `save_notion_config` | `(cfg: dict)` | Serializa `notion_config.json` |
| `query_database_all` | `(db_id, filter_obj?) → list` | Pagina todos os resultados de um database Notion |
| `get_title_value` | `(page, prop_name) → str` | Extrai texto de propriedade `title` ou `rich_text` |
| `get_select_value` | `(page, prop_name) → str` | Extrai valor de propriedade `select` |
| `get_date_value` | `(page, prop_name) → str` | Extrai data de propriedade `date` |
| `get_rich_text_value` | `(page, prop_name) → str` | Extrai texto de propriedade `rich_text` |
| `get_page_content` | `(page_id) → str` | Busca e extrai texto dos blocos internos da página |
| `extrair_texto_blocos` | `(blocos, nivel?) → str` | Recursão sobre blocos Notion → string plana |
| `find_child_database` | `(page_id, keyword) → str \| None` | Busca child_database cujo título contenha keyword |
| `marcar_sincronizado` | `(page_id, prop_name, valor)` | Atualiza campo select no Notion |
| `is_newer_than` | `(page, iso_timestamp?) → bool` | Compara `last_edited_time` da página com timestamp |
| `gerar_flashcards` | `(categoria, titulo, data, conteudo, max_cards?) → list` | Chama IA; retorna `[{"front": ..., "back": ...}]` |
| `adicionar_nota` | `(deck, flashcard, categoria, titulo, data) → bool` | Envia nota ao Anki; retorna False em duplicata |
| `anki_disponivel` | `() → bool` | Verifica se AnkiConnect está respondendo |
| `criar_modelo_basico` | `() → str` | Cria modelo `Notion-Flashcard` no Anki se ausente |
| `processar_hierarquico` | `(cfg) → dict` | Pipeline modo hierárquico; retorna stats |
| `processar_plano` | `(cfg) → dict` | Pipeline modo plano; retorna stats |
| `main` | `()` | Valida conexões, carrega config, despacha pipeline, salva timestamp |

### Formato de `notion_config.json`

```json
{
  "version": 2,
  "mode": "hierarchical",          // "hierarchical" | "flat"
  "parent_db_id": "uuid",          // ID do database principal
  "parent_db_name": "string",      // Nome (display only)
  "parent_name_prop": "string",    // Propriedade title do DB pai
  "child_db_keyword": "string",    // Keyword para encontrar child DB (modo hierarchical)
  "child_title_prop": "string",    // Propriedade title dos itens filhos
  "child_content_prop": "string|null",  // Propriedade rich_text com resumo
  "child_date_prop": "string|null",     // Propriedade date
  "use_sync_field": true,          // Se false, usa last_sync_time para filtrar
  "child_sync_prop": "string|null",     // Campo select de sincronização
  "child_sync_done": "string",     // Valor = "sincronizado"
  "child_status_prop": "string|null",   // Campo select de status (filtro de prontos)
  "child_status_complete": "string|null", // Valor = "pronto para processar"
  "anki_deck_root": "string",      // Deck raiz no Anki
  "last_sync_time": "ISO8601|null" // Atualizado após cada sync bem-sucedido
}
```

---

## Arquivo: `app.py`

Interface Streamlit. **Não importa `notion_anki_sync`** — usa subprocess para evitar
efeitos colaterais de inicialização de clientes no top-level.

### Estrutura

| Seção | Conteúdo |
|---|---|
| Imports + helpers | `load_cfg`, `save_cfg`, `load_notion_config`, `save_notion_config` |
| HTTP checkers | `check_notion`, `check_anki`, `check_ai_key` |
| Notion discovery | `list_notion_databases`, `get_database_properties`, `get_db_title`, `props_by_type` |
| Subprocess | `run_sync` (spawna `notion_anki_sync.py`), `parse_stats` (parseia log) |
| Page config | `st.set_page_config` com ícone customizado |
| CSS | Liquid Glass (glassmorphism + dark gradient + ANSI animations) |
| Sidebar | Token Notion, provedor IA, chaves, Anki host, max cards, status da config |
| Tab Sincronizar | Teste de conexões + botão sync + live log + métricas |
| Tab Configurar Notion | Wizard 4 passos: modo → DB → campos → Anki |
| Tab Ajuda | Documentação inline |

### Funções-chave

| Função | Responsabilidade |
|---|---|
| `load_cfg() → dict` | Lê `.env` via `dotenv_values` |
| `save_cfg(updates)` | Escreve no `.env` via `set_key` |
| `check_notion(token) → (bool, str)` | GET `/v1/users/me` — retorna (ok, mensagem) |
| `check_anki(host) → (bool, str)` | POST AnkiConnect `version` — retorna (ok, mensagem) |
| `check_ai_key(provider, key) → (bool, str)` | Valida formato da chave (sem chamada de rede) |
| `list_notion_databases(token) → list` | Search Notion por todos databases acessíveis |
| `get_database_properties(token, db_id) → dict` | Retorna propriedades de um database |
| `props_by_type(props, *types) → list[str]` | Filtra nomes de propriedades por tipo |
| `run_sync(env_override) → Popen` | Spawna `notion_anki_sync.py` com env vars |
| `parse_stats(lines) → dict` | Extrai métricas do log de saída do sync |

### Session state keys

| Key | Tipo | Uso |
|---|---|---|
| `conn_status` | `dict \| None` | Resultado do último teste de conexão |
| `log_lines` | `list[str]` | Linhas do log de sync |
| `last_stats` | `dict \| None` | Métricas do último sync |
| `sync_result` | `"success" \| "error" \| None` | Status do último sync |
| `running` | `bool` | Sync em andamento (desativa botão) |
| `notion_dbs` | `list \| None` | Cache da lista de databases Notion |
| `setup_step` | `int (1-4)` | Passo atual do wizard de configuração |
| `setup_mode` | `"hierarchical" \| "flat"` | Modo escolhido no wizard |
| `setup_parent_db_id` | `str \| None` | DB escolhido no wizard |
| `setup_parent_db_name` | `str \| None` | Nome do DB escolhido |
| `setup_child_props` | `dict \| None` | Campos mapeados no wizard |

---

## Arquivo: `launcher.py`

Instala dependências e inicia o app. Dois estágios de execução.

### Estágio 1 — Python do sistema (stdlib only)

Detecta: `sys.executable != venv_python` → roda este estágio.

| Função | Responsabilidade |
|---|---|
| `_create_venv()` | `python -m venv .venv` com spinner terminal |
| `_bootstrap_customtkinter()` | `pip install customtkinter` com barra terminal |
| `_reexec_from_venv()` | `subprocess.run([venv/python, launcher.py])` e `sys.exit` |

### Estágio 2 — Python do venv (tem todas as deps)

| Função | Responsabilidade |
|---|---|
| `InstallerWindow` | Janela customtkinter: ícone, barra progress, status, log scrollável |
| `_gui_install_worker(ui)` | Thread de worker: `pip install -r requirements.txt` com feedback GUI |
| `_term_fallback_install()` | Instalação terminal (fallback se tkinter ausente) |
| `_skip_streamlit_email_prompt()` | Cria `~/.streamlit/credentials.toml` para pular prompt de email |
| `_wait_for_server(timeout)` | Polling `http://localhost:{PORT}` até responder |
| `_start_streamlit_headless()` | `Popen` do Streamlit com `--headless=true`; log em `streamlit_server.log` |
| `_launch_webview(proc)` | Abre janela nativa pywebview; retorna `False` se indisponível |
| `_launch_streamlit()` | Orquestra: headless → wait → webview (ou browser fallback) |
| `_generate_icon()` | Cria `icon.png` via Pillow (PIL) se não existir |

### Backends por plataforma

| OS | Janela nativa (pywebview) | Engine |
|---|---|---|
| Windows 10/11 | Edge WebView2 (incluso no OS) | `edgechromium` |
| macOS | WKWebView nativo | auto |
| Linux | WebKit GTK | precisa `gir1.2-webkit2-4.0` |

### Arquivo de controle de instalação

`.venv/.installed` — arquivo vazio criado após pip bem-sucedido.
Se ausente **ou** mais antigo que `requirements.txt` → reinstala.

---

## Provedores de IA

Controlado por `AI_PROVIDER` no `.env`.

| Provider | SDK | Env vars | Modelo padrão |
|---|---|---|---|
| `claude` (padrão) | `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-4-5` |
| `gemini` | `google-genai` | `GEMINI_API_KEY`, `GEMINI_MODEL` | `gemini-2.0-flash` |

`SYSTEM_PROMPT` é idêntico para ambos. Retorno esperado: array JSON `[{"front":"...","back":"..."}]`.

---

## Variáveis de ambiente (`.env`)

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `NOTION_TOKEN` | Sim | — | Token `secret_...` da integração |
| `AI_PROVIDER` | Não | `claude` | `claude` ou `gemini` |
| `ANTHROPIC_API_KEY` | Se claude | — | Chave `sk-ant-...` |
| `GEMINI_API_KEY` | Se gemini | — | Chave `AIza...` |
| `GEMINI_MODEL` | Não | `gemini-2.0-flash` | Modelo Gemini |
| `ANKI_HOST` | Não | `http://localhost:8765` | URL AnkiConnect |
| `MAX_FLASHCARDS_POR_AULA` | Não | `10` | Limite de flashcards por item |

---

## Decks Anki

```
{anki_deck_root}              ← configurado no wizard
└── {nome da categoria}       ← criado automaticamente
```

Modelo de nota: `Notion-Flashcard`  
Campos: `Frente`, `Verso`, `Categoria`, `Titulo`, `Data`  
Tags automáticas: `{categoria}`, `data:{data}`, `notion-sync`

---

## Comportamento de erro e idempotência

| Situação | Comportamento |
|---|---|
| Flashcard duplicado | Ignorado silenciosamente (`allowDuplicate: false`) |
| IA falha em gerar JSON | Item marcado `❌ Erro` no Notion; próximo item continua |
| Item sem conteúdo | Pulado sem marcação |
| `use_sync_field: false` | Usa `last_sync_time` para filtrar `last_edited_time` das páginas |
| Sync bem-sucedido | `last_sync_time` atualizado em `notion_config.json` |

---

## Testes

Arquivo de configuração: `pytest.ini` na raiz.  
Executar: `pytest tests/ -v`

| Arquivo de teste | O que cobre |
|---|---|
| `tests/test_sync_helpers.py` | Funções puras do motor de sync (extração de props Notion, blocos, timestamps) |
| `tests/test_app_helpers.py` | Helpers do app (parse_stats, check_ai_key, props_by_type, get_db_title) |
| `tests/test_launcher.py` | Utilitários do launcher (detecção de OS, contagem de pacotes, credentials) |

Testes de integração com Notion, IA e Anki **não estão incluídos** — requerem credenciais reais.

---

## O que NÃO alterar sem entender

- `app.py` usa `subprocess` para chamar `notion_anki_sync.py` — **não troque por import**.
  Motivo: `notion_anki_sync.py` inicializa clientes AI no top-level; importar causaria
  erro se as chaves não estiverem configuradas quando o app carrega.

- `launcher.py` re-executa a si mesmo via `subprocess.run` ao trocar de Python.
  O `if Path(sys.executable).resolve() != venv_python.resolve()` é o guard dessa lógica.

- `InstallerWindow` comunica com a thread worker via `_queue` + `_poll()`.
  Não chame widgets tkinter diretamente da thread worker — causará crash no Windows.

- `notion_config.json` tem `"version": 2`. Se adicionar campos novos, incremente a versão
  e adicione migration no `load_notion_config()`.
