# 📚 Notion → Anki Sync

Transforma suas anotações do Notion em flashcards prontos para revisão no Anki,
usando inteligência artificial (Claude ou Gemini) para gerar as perguntas e respostas.

---

## Como funciona

```
Notion (suas anotações)
    ↓
IA (Claude ou Gemini) — gera flashcards inteligentes
    ↓
Anki (flashcards prontos para revisar)
```

---

## Antes de instalar — pré-requisitos obrigatórios

Você precisa ter os 3 itens abaixo instalados e configurados **antes** de abrir o app.
Pule qualquer item que você já tenha.

---

### 1. Python 3.10 ou superior

O app roda em Python. Se não tiver instalado:

**Windows**
1. Acesse **python.org/downloads**
2. Baixe a versão mais recente (3.10+)
3. Na instalação, marque obrigatoriamente **"Add Python to PATH"**
4. Clique em "Install Now"
5. Para verificar: abra o Prompt de Comando e digite `python --version`

**macOS**
1. Acesse **python.org/downloads**
2. Baixe e instale o pacote `.pkg`
3. Para verificar: abra o Terminal e digite `python3 --version`

**Linux (Ubuntu/Debian)**
```bash
sudo apt update && sudo apt install python3 python3-venv python3-pip
```

> ⚠️ **Windows — problema comum:** se `python --version` não funcionar após a instalação,
> reinicie o computador. Se continuar sem funcionar, reabra o instalador do Python,
> clique em "Modify" e marque "Add Python to environment variables".

---

### 2. Anki com o plugin AnkiConnect

O Anki precisa estar **aberto e com o plugin AnkiConnect** instalado para receber os flashcards.

**Instalar o Anki**
1. Acesse **apps.ankiweb.net** e baixe para seu sistema
2. Instale e abra o Anki normalmente

**Instalar o AnkiConnect**
1. No Anki, clique em **Ferramentas → Complementos → Obter Complementos**
2. Digite o código: **`2055492159`**
3. Clique em OK e **reinicie o Anki**

> ⚠️ **Importante:** o Anki deve estar **aberto** toda vez que você usar o app.
> O AnkiConnect só funciona com o Anki em execução.

---

### 3. Chaves de API

Você precisa de **duas** chaves: uma do Notion e uma da IA (Claude **ou** Gemini).

#### Notion — Token de integração

1. Acesse **notion.so/my-integrations** (faça login se necessário)
2. Clique em **"+ Nova integração"**
3. Dê um nome (ex: `Anki Sync`) e clique em **Enviar**
4. Copie o **"Token interno de integração"** (começa com `secret_`)

**Conectar a integração às suas páginas:**
> A integração só acessa páginas que você explicitamente autorizar.

Para cada database do Notion que quiser usar:
1. Abra a página no Notion
2. Clique nos três pontos `...` no canto superior direito
3. Role até **"Conectar a"** → selecione sua integração (`Anki Sync`)

> ⚠️ Se você não conectar a integração às páginas, o app não conseguirá ler seus dados.

#### Claude (Anthropic) — opção padrão

1. Acesse **console.anthropic.com**
2. Crie uma conta ou faça login
3. Vá em **"API Keys"** → **"Create Key"**
4. Copie a chave (começa com `sk-ant-`)
5. Guarde bem — ela não será exibida novamente

#### Gemini (Google) — alternativa gratuita

1. Acesse **aistudio.google.com/app/apikey**
2. Clique em **"Create API key"**
3. Copie a chave (começa com `AIza`)

> Você só precisa de **uma** das duas IAs. Claude é a opção padrão.
> Gemini pode ser usado gratuitamente dentro dos limites do plano free.

---

## Instalação e primeiro uso

### Windows

1. Baixe ou clone este repositório
2. Abra a pasta do projeto
3. Dê **duplo clique** em `start.bat`

### macOS

1. Baixe ou clone este repositório
2. Abra o Terminal e navegue até a pasta do projeto
3. Execute uma vez para dar permissão:
   ```bash
   chmod +x start.command
   ```
4. A partir daí: **duplo clique** em `start.command` no Finder

### Linux

1. Baixe ou clone este repositório
2. No Terminal, dentro da pasta do projeto:
   ```bash
   chmod +x start.sh
   ./start.sh
   ```

---

## O que acontece no primeiro acesso

1. **Terminal abre** — cria o ambiente virtual e instala o instalador gráfico (~10s)
2. **Janela do instalador aparece** — mostra barra de progresso enquanto instala todas as dependências (~2 min dependendo da internet)
3. **Janela fecha automaticamente** quando tudo estiver pronto
4. **O app abre no navegador** em `http://localhost:8501`

> A partir do segundo acesso, os passos 1 e 2 são pulados.
> O app abre direto no navegador em poucos segundos.

---

## Configuração inicial no app

Na **primeira vez** que abrir o app, você precisa fazer duas coisas:

### 1. Salvar as chaves (barra lateral esquerda)

- Cole o **Notion Token**
- Selecione o provedor de IA (Claude ou Gemini)
- Cole a **chave da API** da IA escolhida
- Clique em **💾 Salvar configurações**

### 2. Configurar a estrutura do Notion (aba "Configurar Notion")

O app precisa saber quais databases do Notion usar. Siga os 4 passos do assistente:

1. **Modo** — escolha entre *Hierárquico* (DB com categorias → sub-databases) ou *Plano* (DB único)
2. **Database** — selecione qual database principal usar (o app lista automaticamente os acessíveis)
3. **Campos** — indique quais propriedades correspondem a título, conteúdo, data, status, etc.
4. **Anki** — defina o nome do deck raiz onde os flashcards serão criados

Após salvar, o app lembra dessa configuração em todos os usos futuros.

---

## Uso no dia a dia

1. **Abra o Anki** (obrigatório — deve ficar aberto)
2. Clique em `start.bat` / `start.command` / `./start.sh`
3. No app, clique em **🔍 Testar** para verificar as conexões
4. Clique em **▶ Iniciar Sincronização**
5. O log mostra o progresso em tempo real
6. Ao final, os flashcards já estão no Anki prontos para revisar

---

## Estrutura de decks criada no Anki

O app cria automaticamente subdecks por categoria:

```
[Seu deck raiz]
├── Categoria A
├── Categoria B
└── Categoria C
```

---

## Resolução de problemas

**"Python não encontrado" ao abrir o start.bat**
→ Reinstale o Python marcando "Add Python to PATH". Reinicie o computador.

**"Anki não disponível"**
→ Abra o Anki antes de iniciar a sincronização. Verifique se o AnkiConnect está instalado.

**"Nenhum database encontrado" no configurador**
→ Verifique se conectou a integração Notion às suas páginas (passo "Conectar a integração").

**"NOTION_TOKEN não configurado"**
→ Preencha o campo na barra lateral e clique em "Salvar configurações".

**Flashcards duplicados não entram**
→ Comportamento esperado — o AnkiConnect ignora duplicatas automaticamente.

**A janela de instalação travou / ficou mais de 5 minutos sem avançar**
→ Feche a janela e abra `start.bat` novamente. Se o problema persistir,
execute manualmente no terminal:
```bash
pip install -r requirements.txt
```

**macOS: "start.command não pode ser aberto porque é de um desenvolvedor não identificado"**
→ Clique com botão direito em `start.command` → "Abrir" → "Abrir mesmo assim".
Isso só precisa ser feito uma vez.

---

## Arquivos do projeto

| Arquivo | Descrição |
|---|---|
| `start.bat` | Inicializador para Windows |
| `start.command` | Inicializador para macOS (duplo clique) |
| `start.sh` | Inicializador para Linux |
| `launcher.py` | Lógica do instalador e launcher |
| `app.py` | Interface web (Streamlit) |
| `notion_anki_sync.py` | Motor de sincronização |
| `requirements.txt` | Dependências Python |
| `env.example` | Modelo de configuração |
| `.env` | Suas credenciais (**não compartilhe**) |
| `notion_config.json` | Configuração da estrutura Notion (**gerado pelo app**) |
| `sync.log` | Log gerado a cada sincronização |
| `icon.png` | Ícone do app (**gerado automaticamente**) |
