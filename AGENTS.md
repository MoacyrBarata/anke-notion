# AGENTS.md — Guia completo para agentes de IA

Leia este arquivo antes de qualquer manipulação do projeto.
Ele mapeia cada arquivo, cada função relevante e o fluxo de dados completo.

---

## Objetivo do sistema

Pipeline: **Notion → IA → Anki**

```
Usuário escreve anotações no Notion
    ↓
App lê os data_sources configurados
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
├── install-linux.sh       ← Instalação de dependências do sistema (Linux)
├── uninstall-linux.sh     ← Remoção da instalação Linux
├── launcher.py            ← Instalador + launcher cross-platform (UI principal = Flet)
├── app_flet.py            ← Interface Flet (UI **principal** — usada pelo launcher)
├── app.py                 ← Interface Streamlit (alternativa, opcional)
├── ui_components.py       ← Paleta de cores e factories de widgets (Flet)
├── notion_helpers.py      ← Helpers Notion (HTTP) + sugestão de campos + sample page
├── notion_anki_sync.py    ← Motor de sincronização (core, roda como subprocess)
├── _test_notion.py        ← Sandbox manual para testar Notion API (não é teste pytest)
├── requirements.txt       ← Dependências Python
├── requirements-dev.txt   ← Dependências de desenvolvimento/testes
├── .env                   ← Credenciais runtime (NÃO versionado)
├── env.example            ← Template do .env
├── notion_config.json     ← Config da estrutura Notion (NÃO versionado, gerado pelo app)
├── icon.svg               ← Ícone fonte (versionado)
├── icon.png               ← Ícone gerado pelo launcher (NÃO versionado)
├── sync.log               ← Log da última sincronização (NÃO versionado)
├── streamlit_server.log   ← Log do servidor Streamlit (NÃO versionado, só se usar app.py)
└── tests/
    ├── conftest.py             ← Fixtures (mocks de Streamlit/Flet)
    ├── test_sync_helpers.py    ← Testes do motor de sync
    ├── test_app_helpers.py     ← Testes dos helpers do app Streamlit
    ├── test_flet_helpers.py    ← Testes dos helpers do app Flet
    ├── test_flet_views.py      ← Testes integração da view Flet
    └── test_launcher.py        ← Testes do launcher
```

---

## Notion API — versão e nomenclatura

> **IMPORTANTE:** A Notion API mudou em **2025-09-03**. O conceito de "database"
> foi dividido em duas camadas: `database` (container) e `data_source` (tabela
> consultável). O filtro `{"property":"object","value":"database"}` foi removido;
> use `data_source`. O endpoint `databases.query` foi removido em favor de
> `data_sources.query` no notion-client v3.0.

| Camada antiga (≤2022-06-28) | Atual (2025-09-03) |
|---|---|
| `database` | `database` (container) → contém `data_sources[]` |
| `databases.query(db_id, …)` | `data_sources.query(data_source_id=ds_id, …)` |
| `databases.retrieve(db_id)` | `data_sources.retrieve(data_source_id=ds_id)` ou `databases.retrieve(database_id=db_id)` (retorna lista de data_sources) |
| `/v1/databases/{id}/query` | `/v1/data_sources/{id}/query` |
| filter `value: "database"` | filter `value: "data_source"` |

Toda a UI usa **data_source_id** como o "ID do database" exposto ao usuário.
Para compat retroativa, `query_database_all()` em `notion_anki_sync.py` faz
fallback: se receber um database_id legacy, resolve para o primeiro data_source
associado e tenta de novo.

`_NOTION_VERSION = "2025-09-03"` em `notion_helpers.py` e `app_flet.py`.

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
        └─ _launch_flet()  ← roda app_flet.py em subprocess (janela nativa Flet)
```

`_launch_streamlit()` ainda existe no launcher mas NÃO é chamado pela `main()`.

### Sincronização (notion_anki_sync.py via app_flet.py)

```
app_flet.py  →  subprocess.Popen([venv/python, notion_anki_sync.py])
    ↓
notion_anki_sync.py
    ├─ load_dotenv()
    ├─ load_notion_config()  ← notion_config.json
    │
    ├─ MODO "hierarchical":
    │   ├─ query_database_all(parent_db_id)  ← lista categorias (data_source)
    │   └─ para cada categoria:
    │       ├─ find_child_database(page_id, keyword)  → data_source_id
    │       ├─ query_database_all(child_db_id, filter)
    │       └─ para cada item pendente:
    │           ├─ get_page_content() + get_rich_text_value()
    │           ├─ gerar_flashcards()  → Claude ou Gemini
    │           ├─ adicionar_nota()  → AnkiConnect
    │           └─ marcar_sincronizado()  → Notion
    │
    ├─ MODO "flat":
    │   └─ para cada DB em selected_dbs (lista de {id, name}):
    │       └─ query_database_all(db_id, filter)
    │           └─ mesma pipeline por item
    │
    └─ save_notion_config()  ← atualiza last_sync_time
```

Modo `flat` aceita **múltiplos** data_sources via `selected_dbs`. Fallback: se
ausente, usa `parent_db_id` único (compat).

---

## Arquivo: `notion_anki_sync.py`

Motor de sincronização. **Pode rodar standalone:** `python notion_anki_sync.py`

### Seções (em ordem no arquivo)

| Seção | Conteúdo |
|---|---|
| Imports e SDK detection | imports condicionais `anthropic` / `google.genai`; flags `ANTHROPIC_AVAILABLE` / `GOOGLE_GENAI_AVAILABLE` |
| Config | `load_dotenv()`, constantes, `load_notion_config()` / `save_notion_config()` |
| Clientes | `claude_client`, `gemini_client` — inicializados conforme `AI_PROVIDER` |
| Helpers Notion genéricos | funções de leitura de propriedades e blocos (usam `data_sources` API) |
| Helpers IA | `SYSTEM_PROMPT` + `gerar_flashcards()` |
| Helpers AnkiConnect | criação de decks, notas, modelos |
| Pipeline hierárquico | `processar_hierarquico()` |
| Pipeline plano | `processar_plano()` (suporta multi-DB) + `_processar_db_plano()` |
| Main | validação de conexões → despacha para o pipeline correto → salva config |

### Funções-chave

| Função | Assinatura | Responsabilidade |
|---|---|---|
| `load_notion_config` | `() → dict \| None` | Lê `notion_config.json`; retorna None se não existir |
| `save_notion_config` | `(cfg: dict)` | Serializa `notion_config.json` |
| `query_database_all` | `(db_id, filter_obj?) → list` | `data_sources.query` com paginação. Fallback: resolve database_id legacy → primeira data_source |
| `get_title_value` | `(page, prop_name) → str` | Extrai texto de propriedade `title` ou `rich_text` |
| `get_select_value` | `(page, prop_name) → str` | Extrai valor de propriedade `select` |
| `get_date_value` | `(page, prop_name) → str` | Extrai data de propriedade `date` |
| `get_rich_text_value` | `(page, prop_name) → str` | Extrai texto de propriedade `rich_text` |
| `get_page_content` | `(page_id) → str` | Busca e extrai texto dos blocos internos da página |
| `extrair_texto_blocos` | `(blocos, nivel?) → str` | Recursão sobre blocos Notion → string plana |
| `find_child_database` | `(page_id, keyword) → str \| None` | Acha child_database e retorna **data_source_id** (resolve via `databases.retrieve`) |
| `marcar_sincronizado` | `(page_id, prop_name, valor)` | Atualiza campo select no Notion |
| `is_newer_than` | `(page, iso_timestamp?) → bool` | Compara `last_edited_time` da página com timestamp |
| `gerar_flashcards` | `(categoria, titulo, data, conteudo, max_cards?) → list` | Chama IA; retorna `[{"front": ..., "back": ...}]` |
| `adicionar_nota` | `(deck, flashcard, categoria, titulo, data) → bool` | Envia nota ao Anki; retorna False em duplicata |
| `anki_disponivel` | `() → bool` | Verifica se AnkiConnect está respondendo |
| `criar_modelo_basico` | `() → str` | Cria modelo `Notion-Flashcard` no Anki se ausente |
| `processar_hierarquico` | `(cfg) → dict` | Pipeline modo hierárquico; retorna stats |
| `processar_plano` | `(cfg) → dict` | Pipeline modo plano; itera `selected_dbs`; retorna stats |
| `_processar_db_plano` | `(db_id, db_name, cfg, total)` | Helper interno: processa um único data_source no modo plano |
| `main` | `()` | Valida conexões, carrega config, despacha pipeline, salva timestamp |

### Formato de `notion_config.json`

```json
{
  "version": 2,
  "mode": "hierarchical",          // "hierarchical" | "flat"
  "parent_db_id": "uuid",          // data_source_id principal (modo hierarchical) ou fallback (flat)
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
  "selected_dbs": [                // Modo flat — múltiplos data_sources (opcional)
    {"id": "data_source_id", "name": "Nome exibido"}
  ],
  "last_sync_time": "ISO8601|null" // Atualizado após cada sync bem-sucedido
}
```

---

## Arquivo: `app_flet.py` (UI principal)

Interface Flet (janela nativa). **Não importa `notion_anki_sync`** — usa
subprocess para evitar efeitos colaterais de inicialização de clientes no
top-level.

### Estrutura

| Seção | Conteúdo |
|---|---|
| Imports + helpers | `load_cfg`, `save_cfg`, `load_notion_config`, `save_notion_config` |
| HTTP checkers | `check_notion`, `check_anki`, `check_ai_key` |
| Notion discovery | `_notion_get`, `list_notion_databases`, `get_database_properties`, `get_db_title`, `props_by_type`, `suggest_fields`, `get_sample_page`, `extract_prop_text` |
| Subprocess | `run_sync` (spawna `notion_anki_sync.py`), `parse_stats` (parseia log) |
| `main(page)` | Aplica tema (paleta blue-shifted), monta NavigationRail + 4 views, registra `on_resized` para layout responsivo |
| Views | `view_sync`, `setup_col` (wizard 4 passos), `view_settings`, `view_help` |

### Layout responsivo

- `page.window.min_width = 480`, `min_height = 600`
- `_apply_layout(width)` ajusta padding e visibilidade do NavigationRail:
  - **wide** (≥720 px): rail visível com labels, padding 28
  - **narrow** (<720 px): rail oculto, padding 12
- Atualizado em `page.on_resized`

### Paleta (blue-shifted, ver `ui_components.py`)

| Variável | Valor | Uso |
|---|---|---|
| `C_BG` | `#070d1a` | Fundo geral |
| `C_ACCENT` | `#3b82f6` (blue-500) | Ações primárias, foco, indicadores |
| `C_ACCENT2` | `#38bdf8` (sky-400) | Acentos secundários |
| `C_SUCCESS` | `#34d399` | Verde sucesso |
| `C_WARNING` | `#e98a34` | Amarelo aviso |
| `C_ERROR` | `#f56691` | Rosa erro |
| `C_TEXT` / `C_DIM` / `C_MUTED` | `#eaf0ff` / `#8aa0c8` / `#465879` | Texto principal/secundário/atenuado |

### State keys (dict `state` em `main()`)

| Key | Tipo | Uso |
|---|---|---|
| `cfg` | dict | Cache do `.env` |
| `conn_status` | dict \| None | Resultado do último teste |
| `log_lines` | list[str] | Log do sync atual |
| `last_stats` | dict \| None | Métricas do último sync |
| `sync_running` | bool | Sync em andamento |
| `sync_result` | str \| None | "success" / "error" |
| `notion_dbs` | list \| None | Cache de data_sources |
| `notion_dbs_err` | str \| None | Erro da última busca |
| `notion_loading` | bool | Spinner de busca |
| `notion_db_checked` | str \| None | data_source_id selecionado |
| `notion_db_expanded` | str \| None | Detalhes expandidos |
| `setup_step` | int (1-4) | Passo do wizard |
| `setup_mode` | str | "hierarchical" \| "flat" |
| `setup_parent_db_id` / `setup_parent_db_name` | str \| None | DB principal escolhido |
| `setup_selected_dbs` | list[dict] | Múltiplos DBs no modo flat |
| `setup_child_props` | dict \| None | Mapeamento de campos |
| `layout_mode` | "wide" \| "narrow" | Modo de layout responsivo |

---

## Arquivo: `app.py` (UI alternativa Streamlit)

Interface Streamlit. **Opcional** — não é mais executada pelo launcher,
mas o código continua mantido. **Streamlit não está em `requirements.txt`** —
instale separadamente se quiser usar (`pip install streamlit`).

### Estrutura

| Seção | Conteúdo |
|---|---|
| Imports + helpers | `load_cfg`, `save_cfg`, `load_notion_config`, `save_notion_config` |
| HTTP checkers | `check_notion`, `check_anki`, `check_ai_key` |
| Notion discovery | `list_notion_databases` (filter `data_source`), `get_database_properties` (`data_sources.retrieve`), `get_db_title`, `props_by_type` |
| Subprocess | `run_sync`, `parse_stats` |
| Page config | `st.set_page_config` com ícone customizado |
| CSS | Liquid Glass (glassmorphism + dark gradient azul + media queries responsivas) |
| Sidebar | Token Notion, provedor IA, chaves, Anki host, max cards, status da config |
| Tab Sincronizar | Teste de conexões + botão sync + live log + métricas |
| Tab Configurar Notion | Wizard 4 passos: modo → DB → campos → Anki |
| Tab Ajuda | Documentação inline |

### CSS responsivo

Breakpoints (em `app.py`):
- `≤900px` — reduz padding, fontes e altura dos cards
- `≤600px` — empilha colunas (`stHorizontalBlock` → flex-direction column)
- `≤420px` — fontes menores ainda
- `(hover: none)` — desativa hover transforms para touch

---

## Arquivo: `notion_helpers.py`

Helpers de Notion compartilhados (HTTP raw + sugestão de campos).
Usado por `app_flet.py` (que importa) e potencialmente por scripts standalone.

| Função | Responsabilidade |
|---|---|
| `_notion_get(token, path)` | GET raw `https://api.notion.com/v1{path}` |
| `list_notion_databases(token, on_progress?)` | Busca data_sources via `/search` + fallback explorando `parent` de pages |
| `get_database_properties(token, db_id)` | Tenta `data_sources/{db_id}`; fallback para `databases/{db_id}.data_sources[0]` |
| `get_db_title(db)` | Extrai title de uma data_source |
| `props_by_type(props, *types)` | Filtra propriedades por tipo |
| `suggest_fields(props)` | Heurística keyword+type para pré-preencher dropdowns no wizard |
| `get_sample_page(token, db_id)` | Busca uma página de exemplo via `/data_sources/{id}/query` |
| `extract_prop_text(page, prop_name)` | Extrai texto de qualquer tipo de propriedade |

`_NOTION_VERSION = "2025-09-03"`.

---

## Arquivo: `ui_components.py`

Paleta de cores e factories de widgets Flet. Funções **puras** (sem state).

| Símbolo | Tipo | Uso |
|---|---|---|
| `C_BG`, `C_ACCENT`, `C_ACCENT2`, `C_TEXT`, `C_DIM`, `C_MUTED` | str | Cores hex / hex+alpha |
| `C_SUCCESS`, `C_WARNING`, `C_ERROR` | str | Status |
| `C_GLASS`, `C_GLASS_HVR`, `C_BORDER` | str | Glass overlays |
| `_ball(width, color)` | `ft.Border` | Border de 4 lados igual |
| `_bonly(**sides)` | `ft.Border` | Border parcial |
| `glass(content, padding?, expand?, …)` | `ft.Container` | Card com glassmorphism + shadow |
| `h(text, size?, color?, weight?)` | `ft.Text` | Heading |
| `dim(text, size?, color?)` | `ft.Text` | Texto secundário |
| `badge(label, ok, msg)` | `ft.Container` | Badge colorido por status |
| `btn(text, on_click, …)` | `ft.Button` | Botão primário |
| `ghost_btn(text, on_click, …)` | `ft.OutlinedButton` | Botão secundário |
| `field(label, value?, password?, hint?, width?)` | `ft.TextField` | Input estilizado |
| `dropdown(label, options, value?)` | `ft.Dropdown` | Select estilizado |
| `hint(text)` | `ft.Container` | Helper text abaixo de um field |
| `field_with_hint(control, hint_text)` | `ft.Column` | Empacota field + hint |

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
| `_skip_streamlit_email_prompt()` | Cria `~/.streamlit/credentials.toml` (só usado se rodar Streamlit) |
| `_launch_flet()` | **Caminho ativo:** `subprocess.Popen([venv/python, app_flet.py])` |
| `_launch_streamlit()` | Caminho legado: roda Streamlit headless + webview |
| `_generate_icon()` | Cria `icon.png` via Pillow (PIL) se não existir |

`main()` chama `_launch_flet()`. `_launch_streamlit()` permanece definido mas
não é invocado.

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
| `NOTION_TOKEN` | Sim | — | Token `secret_...` ou `ntn_...` da integração |
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
| `query_database_all` recebe database_id legacy | Tenta resolver `databases.retrieve` → `data_sources[0]` e refaz a query |

---

## Testes

Arquivo de configuração: `pytest.ini` na raiz.
Executar: `.venv/Scripts/python.exe -m pytest tests/ -v`

| Arquivo de teste | O que cobre |
|---|---|
| `tests/conftest.py` | Fixtures: mock de `streamlit`, mock de `flet.app/run`, `_FakeSessionState` |
| `tests/test_sync_helpers.py` | Funções puras do motor de sync (extração de props Notion, blocos, timestamps) |
| `tests/test_app_helpers.py` | Helpers do app Streamlit (`parse_stats`, `check_ai_key`, `props_by_type`, `get_db_title`) |
| `tests/test_flet_helpers.py` | Helpers do app Flet (`save_cfg`, `load_cfg`, `parse_stats`) |
| `tests/test_flet_views.py` | Smoke tests da `app_flet.main(page)` (estrutura de controles, navigation) |
| `tests/test_launcher.py` | Utilitários do launcher (detecção de OS, contagem de pacotes, credentials) |

Testes de integração com Notion, IA e Anki **não estão incluídos** — requerem
credenciais reais. Use `_test_notion.py` como sandbox manual.

### Armadilhas conhecidas em testes

- **Não chame `save_cfg` sem mockar `ENV_FILE`** — o `.env` real é poluído.
  `tests/test_flet_views.py::page` patch `app_flet.ENV_FILE` para `tmp_path`.
  `tests/conftest.py::mock_streamlit` define `st.button.return_value = False`
  para que `if st.button(...)` no top-level de `app.py` não dispare `save_cfg`
  com MagicMocks no momento do `import app`.

---

## O que NÃO alterar sem entender

- `app_flet.py` e `app.py` usam `subprocess` para chamar `notion_anki_sync.py`
  — **não troque por import**. Motivo: `notion_anki_sync.py` inicializa
  clientes AI no top-level; importar causaria erro se as chaves não estiverem
  configuradas quando o app carrega.

- `launcher.py` re-executa a si mesmo via `subprocess.run` ao trocar de Python.
  O `if Path(sys.executable).resolve() != venv_python.resolve()` é o guard.

- `InstallerWindow` comunica com a thread worker via `_queue` + `_poll()`.
  Não chame widgets tkinter diretamente da thread worker — causará crash no Windows.

- `notion_config.json` tem `"version": 2`. Se adicionar campos novos, incremente
  a versão e adicione migration no `load_notion_config()`.

- **Notion API:** sempre prefira `data_sources.query(data_source_id=…)` sobre
  `databases.query` (removido em notion-client v3.0). O wrapper
  `query_database_all` em `notion_anki_sync.py` tem fallback para database_id
  legacy, mas novas escritas devem usar data_source_id direto.

- Layout responsivo de `app_flet.py`: `_apply_layout(width)` é idempotente
  (early-return se mode não mudou). Mudar o breakpoint requer atualizar
  `tests/test_flet_views.py::test_main_sets_window_dimensions`.
