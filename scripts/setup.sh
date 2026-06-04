#!/bin/bash
# Bootstrap local de dev: cria diretórios + inicializa SQLite.
# Uso: bash scripts/setup.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p data/global data/tenant qdrant_storage
python3 -c "
from overstreet.infra import get_imoveis_db, get_memory_db, get_fsm_db, get_shared_db
get_imoveis_db(); get_memory_db(); get_fsm_db(); get_shared_db()
print('SQLite initialized')
"
echo "OK. Run: python3 bot.py"
