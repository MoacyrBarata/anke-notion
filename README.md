# 📚 Notion → Anki Sync

Automação que lê suas anotações de aula do Notion, gera flashcards inteligentes
com a Claude AI e os envia diretamente para o Anki.

---

## ⚙️ Instalação

### 1. Pré-requisitos

- Python 3.10 ou superior
- Anki instalado no computador
- Plugin **AnkiConnect** instalado no Anki

#### Instalar o AnkiConnect:
1. Abra o Anki
2. Menu **Ferramentas → Complementos → Obter Complementos**
3. Digite o código: **`2055492159`**
4. Reinicie o Anki
5. Deixe o Anki **aberto** sempre que rodar o script

---

### 2. Instalar dependências Python

```bash
pip install notion-client anthropic requests python-dotenv
```

---

### 3. Configurar credenciais

Copie o arquivo de exemplo e preencha suas chaves:

```bash
cp .env.example .env
```

Edite o `.env`:

```
NOTION_TOKEN=secret_...       # Token da integração Notion
ANTHROPIC_API_KEY=sk-ant-...  # Chave da API Claude
ANKI_HOST=http://localhost:8765
```

#### Como obter o NOTION_TOKEN:
1. Acesse https://www.notion.so/my-integrations
2. Clique em **"+ Nova integração"**
3. Dê um nome (ex: "Anki Sync") e clique em **Enviar**
4. Copie o **"Token interno de integração"**
5. **Importante:** Abra cada página de disciplina no Notion → clique em `...` → **Conectar** → selecione sua integração

#### Como obter o ANTHROPIC_API_KEY:
1. Acesse https://console.anthropic.com/
2. Menu **API Keys → Create Key**
3. Copie a chave gerada

---

## 🚀 Como usar

Com o Anki aberto, execute:

```bash
python notion_anki_sync.py
```

O script irá:
1. Buscar todas as disciplinas do seu **📚 Banco de Disciplinas**
2. Para cada disciplina, encontrar o banco **"Aulas — Anotações por Dia"**
3. Processar apenas aulas com **Status = ✅ Completa** e **Sincronização ≠ ✅ Sincronizado**
4. Gerar flashcards inteligentes com a Claude
5. Criar os flashcards no Anki no deck `Estudo::Concurso::<Disciplina>`
6. Marcar a aula como **✅ Sincronizado** no Notion

---

## 📁 Estrutura dos Decks no Anki

```
Estudo::Concurso
├── Contabilidade Geral
├── Direito Constitucional + Controle Externo
├── Língua Portuguesa
├── Raciocínio Lógico
├── Auditoria Governamental
└── ...
```

---

## 🔁 Workflow recomendado

1. **Depois de cada aula:** escreva suas anotações no Notion e mude o Status para **✅ Completa**
2. **Ao final do dia (ou semana):** rode o script `python notion_anki_sync.py`
3. **No Anki:** os novos flashcards já estarão prontos para revisão

---

## 🛠️ Personalização

No arquivo `notion_anki_sync.py`, você pode ajustar:

| Variável | Descrição | Padrão |
|---|---|---|
| `MAX_FLASHCARDS_POR_AULA` | Máximo de flashcards gerados por aula | `10` |
| `ANKI_DECK_RAIZ` | Nome do deck raiz no Anki | `Estudo::Concurso` |

---

## 🗂️ Arquivos

| Arquivo | Descrição |
|---|---|
| `notion_anki_sync.py` | Script principal |
| `.env` | Suas credenciais (não compartilhe!) |
| `.env.example` | Modelo das credenciais |
| `sync.log` | Log gerado automaticamente ao rodar |

---

## ❓ Resolução de problemas

**"Anki não está disponível"**
→ Abra o Anki antes de rodar o script e verifique se o AnkiConnect está instalado.

**"NOTION_TOKEN não configurado"**
→ Renomeie `.env.example` para `.env` e preencha as chaves.

**Aula com conteúdo não está sendo processada**
→ Verifique se o Status da aula está como **✅ Completa** no Notion.

**Flashcards duplicados não entram**
→ Comportamento esperado — o AnkiConnect ignora duplicatas automaticamente.
