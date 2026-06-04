#!/bin/bash
set -euo pipefail

echo "[overstreet] boot: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. Inicializar SQLite
mkdir -p /app/data/global /app/data/tenant

# 2. Qdrant sidecar em background
echo "[overstreet] starting Qdrant on :6333"
qdrant \
  --storage-storage-path /qdrant_storage/storage \
  --storage-snapshots-path /qdrant_storage/snapshots \
  --uri http://0.0.0.0:6333 \
  > /tmp/qdrant.log 2>&1 &
QDRANT_PID=$!
echo "[overstreet] Qdrant pid=$QDRANT_PID"

# 3. Esperar Qdrant ficar pronto (max 30s)
for i in {1..30}; do
  if curl -fsS http://localhost:6333/healthz >/dev/null 2>&1; then
    echo "[overstreet] Qdrant ready"
    break
  fi
  sleep 1
done

# 4. Validar env vars obrigatórias
for var in BOT_TOKEN NVIDIA_API_KEY GROQ_API_KEY DASHBOARD_PASSWORD; do
  if [ -z "${!var:-}" ]; then
    echo "[overstreet] FATAL: $var is not set"
    exit 1
  fi
done

# 5. Sinal de vida (healthcheck)
touch /app/.alive

# 6. Inicializar SQLite (cria tabelas se não existirem)
python3 -c "
from overstreet.infra import get_imoveis_db, get_memory_db, get_fsm_db, get_shared_db
get_imoveis_db()
get_memory_db()
get_fsm_db()
get_shared_db()
print('[overstreet] SQLite initialized')
"

# 7. Subir bot + dashboard (bot.py gerencia ambos via asyncio.gather)
echo "[overstreet] starting bot + dashboard"
exec python3 bot.py
