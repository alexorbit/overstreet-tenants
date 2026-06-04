# OverStreet — Tenant Edition

> Bot Telegram de corretagem imobiliária com IA, busca semântica e dashboard web.
> Single-tenant: **1 container = 1 imobiliária**. Tudo embutido: SQLite, Qdrant (sidecar), Whisper, dashboard FastAPI.

---

## ⚠️ Status

**Alpha** — single-tenant, single-user. Pensado pra rodar 1 instância por imobiliária.
Não há painel multi-tenant (cada cliente tem seu próprio deploy/container).

---

## ✨ Features

- 🤖 **Bot Telegram** (aiogram 3) com agente conversacional em PT-BR
- 🔍 **Busca híbrida**: FTS5 (BM25) + embeddings semânticos (Qdrant + fastembed)
- 🎙️ **Whisper local** (faster-whisper, via Groq) pra transcrição de áudios
- 🧠 **LLM** (NVIDIA NIM: qwen3.5 / mistral-small) pra matching de clientes ↔ imóveis
- 📊 **Dashboard FastAPI** com auth (cookie + password) e upload de catálogo
- 💾 **SQLite** com WAL, FTS5 e migrations idempotentes
- 🐳 **Single container** com Qdrant binário como sidecar local (sem Docker Compose)
- ☁️ **Deploy Railway-ready** (1 click, ~$5/mês)

---

## 🚀 Quick Start (local)

### Pré-requisitos
- Python 3.11+
- Docker (recomendado) OU pip + sistema com ffmpeg/libsndfile1

### 1. Clonar
```bash
git clone <repo> overstreet-tenants
cd overstreet-tenants
```

### 2. Configurar
```bash
cp .env.example .env
# edite .env: BOT_TOKEN, NVIDIA_API_KEY, GROQ_API_KEY, DASHBOARD_PASSWORD
```

### 3a. Com Docker
```bash
docker compose up --build
```

### 3b. Sem Docker (dev)
```bash
bash scripts/setup.sh    # cria data/ + inicializa SQLite
python3 bot.py
```

Dashboard: http://localhost:8000/dashboard (senha: a do `DASHBOARD_PASSWORD`)

---

## ☁️ Deploy no Railway

1. **Novo projeto** → Deploy from GitHub Repo → selecione o repo
2. **Adicione um volume** montado em `/app/data` (5GB grátis é suficiente pra começar)
3. **Setar env vars** (Variables tab):
   - `BOT_TOKEN` *(obrigatório)*
   - `NVIDIA_API_KEY` *(obrigatório)*
   - `GROQ_API_KEY` *(obrigatório)*
   - `DASHBOARD_PASSWORD` *(obrigatório)*
   - `DASHBOARD_SECRET` *(32+ chars aleatórios — gere com `openssl rand -hex 32`)*
   - `QDRANT_URL=http://localhost:6333` *(sidecar local, NÃO mude)*
4. **Deploy**: Railway detecta o `Dockerfile` e constrói automaticamente
5. **Pegar URL** (Settings → Networking → Generate Domain)

> O `entrypoint.sh` sobe o Qdrant binário local antes do bot/dashboard. Não precisa de serviço Qdrant separado.

---

## 🏠 Adicionar imóveis

### Via dashboard (recomendado)
1. Acesse `https://<seu-app>.up.railway.app/dashboard`
2. Login com `DASHBOARD_PASSWORD`
3. Vá em **Imóveis → Upload** e arraste seu `.jsonl`

### Via CLI (bulk import)
```bash
# Dentro do container ou local com venv ativo
python3 ingest.py catalogo.jsonl

# Rebuild do FTS5 (após mudança no tokenizer, por exemplo)
python3 ingest.py catalogo.jsonl --reindex

# Pular embeddings (só SQLite + FTS5)
python3 ingest.py catalogo.jsonl --no-qdrant

# Batch customizado
python3 ingest.py catalogo.json --batch-size 512
```

**Formato aceito**: JSON array (`[{...}, {...}]`) ou JSONL (um JSON por linha).
Cada item precisa ter pelo menos `id` (int). Veja o schema completo em `overstreet/db/schema.py` (38 colunas).

---

## ⚙️ Configurar o bot

### Onboarding
Na primeira execução, o bot cria um usuário admin baseado em `ADMIN_IDS` (env).
Envie `/start` no Telegram pra ver o menu principal.

### Comandos úteis
- `/start` — menu principal
- `/imoveis` — listar / buscar catálogo
- `/clientes` — gerenciar clientes
- `/visitas` — agenda de visitas
- `/followups` — lembretes pendentes
- `/chaves` — controle de chaves
- `/settings` — ajustar perfil do agente (nome, prompt, etc.)

### Customizar agente
Edite `overstreet/handlers/...` e templates em `dashboard/templates/`.
O nome do agente vem de `AGENT_NAME` (env, default `Ana`).

---

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│ Container único (1 imobiliária = 1 mundo)                  │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Bot        │    │  Dashboard   │    │  Qdrant      │  │
│  │  (aiogram)   │    │  (FastAPI)   │    │  (sidecar)   │  │
│  │  :8000/tg    │    │  :8000/web   │    │  :6333       │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                   │                   │          │
│         └───────────────────┴───────────────────┘          │
│                             │                              │
│                    ┌────────▼─────────┐                    │
│                    │     SQLite       │                    │
│                    │  data/tenant/    │                    │
│                    │  data/global/    │                    │
│                    └──────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

- **Bot** e **Dashboard** rodam no mesmo processo Python via `asyncio.gather`
- **Qdrant** é um binário Rust baixado no build, iniciado em background pelo `entrypoint.sh`
- **SQLite** em WAL mode, com 1 connection por DB (singleton via `overstreet.infra`)
- **Whisper** roda local (faster-whisper) ou via Groq API (configurável)
- **Embeddings** gerados sob demanda (fastembed, modelo `BAAI/bge-small-en-v1.5`, 384 dims)

---

## 💸 Custos Railway (estimativa)

| Recurso                | Custo/mês |
|------------------------|-----------|
| Hobby plan (RAM 8GB)   | $5        |
| Volume 5GB             | grátis    |
| NVIDIA NIM API (LLM)   | ~$2-10    |
| **Total estimado**     | **~$7-15** |

> Embeddings e Whisper (Groq) são opcionais — modelo local do fastembed roda offline; faster-whisper usa CPU. O LLM é o único componente que cobra por token.

---

## ⚠️ Limitações conhecidas

- **Sem multi-tenant**: rodar várias imobiliárias = várias instâncias (cada uma com seu BOT_TOKEN)
- **Sem backup automático**: SQLite é local no volume. Configure snapshot Railway ou `cron` externo
- **Whisper local** consome ~500MB RAM; se a instância for pequena, use Groq API
- **Embeddings**: 1ª execução baixa ~130MB do modelo (cache em `whisper_cache/`)
- **FTS5** requer SQLite ≥ 3.9 (compilado default em Python builds)
- **Postgres não é usado** — esta versão é SQLite-only (psycopg foi removido do requirements)

---

## 📂 Estrutura

```
overstreet-tenants/
├── bot.py                 # entrypoint
├── ingest.py              # CLI de bulk import
├── entrypoint.sh          # boot do container
├── Dockerfile             # build c/ Qdrant sidecar
├── docker-compose.yml     # dev local
├── railway.toml/json      # config Railway
├── .env.example
├── requirements.txt
├── overstreet/            # código do bot
│   ├── ai/                # LLM clients
│   ├── db/                # schema + queries
│   ├── handlers/          # comandos Telegram
│   ├── search/            # FTS5 + semântica
│   ├── infra.py           # singletons SQLite
│   └── main.py            # asyncio.gather(bot, dashboard)
├── dashboard/             # FastAPI web
│   ├── server.py
│   ├── auth.py
│   └── templates/
├── data/                  # (volume, gitignored)
│   ├── tenant/
│   └── global/
└── qdrant_storage/        # (volume, gitignored)
```

---

## 📜 Licença

MIT (ou a que você definir). Sem garantias — use por sua conta e risco.
