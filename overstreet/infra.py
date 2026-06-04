"""Single-tenant infrastructure manager.

Directory layout (in single-tenant mode, each container = 1 tenant):
  DATA_DIR/tenant/imoveis.db    — property database (imoveis, clientes, visitas, followups, chaves, fotos)
  DATA_DIR/tenant/memory.db     — private memory (messages, user_profiles)
  DATA_DIR/tenant/fsm.db        — FSM state (managed by SQLiteFSMStorage)
  DATA_DIR/global/shared_memory.db — cross-session learnings (shared_insights, improved_responses, knowledge_entries)

This module exposes simple getters that return cached connections, opening
+ initializing the schema on first access.
"""
import sqlite3
import logging
from pathlib import Path
from typing import Optional
from unicodedata import normalize as _unicode_normalize

log = logging.getLogger("overstreet.infra")

DATA_DIR = Path(__file__).parent.parent / "data"

# Fixed subdirs for the (single) tenant
_TENANT_DIR = DATA_DIR / "tenant"
_GLOBAL_DIR = DATA_DIR / "global"


# ── Helpers ──────────────────────────────────────────────────────────────

def _unaccent(s):
    """Remove acentos para comparação accent-insensitive no SQLite."""
    if s is None:
        return ""
    return _unicode_normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii').lower()


def _register_unaccent(conn: sqlite3.Connection):
    """Registra função unaccent() na conexão SQLite."""
    conn.create_function("unaccent", 1, _unaccent)


# ── Connection singletons (single-tenant) ────────────────────────────────

_imoveis_db: Optional[sqlite3.Connection] = None
_memory_db: Optional[sqlite3.Connection] = None
_fsm_db: Optional[sqlite3.Connection] = None
_shared_db: Optional[sqlite3.Connection] = None


# ── Path helpers ─────────────────────────────────────────────────────────

def tenant_dir() -> Path:
    return _TENANT_DIR


def imoveis_path() -> Path:
    return _TENANT_DIR / "imoveis.db"


def memory_path() -> Path:
    return _TENANT_DIR / "memory.db"


def fsm_path() -> Path:
    return _TENANT_DIR / "fsm.db"


def shared_db_path() -> Path:
    return _GLOBAL_DIR / "shared_memory.db"


# ── Schema init functions ────────────────────────────────────────────────

def _init_imoveis_schema(conn: sqlite3.Connection):
    """Inicializa schema do banco de imóveis (imóveis, clientes, visitas, followups, chaves, fotos).

    Single-tenant: nenhuma coluna tenant_id. A coluna tenant_id em imoveis
    é removida do schema e mantida apenas como INTEGER (nullable) por
    compatibilidade com ingest legado.
    """
    from overstreet.db.schema import (
        IMOVEIS_SCHEMA, IMOVEL_FOTOS_SCHEMA,
        CLIENTES_SCHEMA, CLIENTE_ALIASES_SCHEMA, CLIENTE_FAVORITOS_SCHEMA,
        VISITAS_SCHEMA, FOLLOWUPS_SCHEMA, CHAVES_SCHEMA,
    )
    for ddl in [
        IMOVEIS_SCHEMA, IMOVEL_FOTOS_SCHEMA,
        CLIENTES_SCHEMA, CLIENTE_ALIASES_SCHEMA, CLIENTE_FAVORITOS_SCHEMA,
        VISITAS_SCHEMA, FOLLOWUPS_SCHEMA, CHAVES_SCHEMA,
    ]:
        conn.execute(ddl)

    # Índices (single-tenant — sem idx_*_tenant)
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_imoveis_city ON imoveis(city)",
        "CREATE INDEX IF NOT EXISTS idx_imoveis_situacao ON imoveis(situacao)",
        "CREATE INDEX IF NOT EXISTS idx_imoveis_district ON imoveis(district)",
        "CREATE INDEX IF NOT EXISTS idx_imoveis_city_district ON imoveis(city, district)",
        "CREATE INDEX IF NOT EXISTS idx_imoveis_finalidade ON imoveis(finalidade)",
        "CREATE INDEX IF NOT EXISTS idx_clientes_nome ON clientes(nome)",
        "CREATE INDEX IF NOT EXISTS idx_aliases_alias ON cliente_aliases(alias)",
        "CREATE INDEX IF NOT EXISTS idx_favoritos_cliente ON cliente_favoritos(cliente_id)",
        "CREATE INDEX IF NOT EXISTS idx_visitas_data ON visitas(data_visita)",
        "CREATE INDEX IF NOT EXISTS idx_visitas_imovel ON visitas(imovel_id)",
        "CREATE INDEX IF NOT EXISTS idx_followups_status_prazo ON followups(status, data_prazo)",
        "CREATE INDEX IF NOT EXISTS idx_chaves_status ON chaves(status)",
        "CREATE INDEX IF NOT EXISTS idx_chaves_imovel ON chaves(imovel_id)",
    ]:
        try:
            conn.execute(idx)
        except Exception:
            pass

    # Adiciona latitude/longitude se faltarem (migrations idempotentes)
    for col_ddl in [
        "ALTER TABLE imoveis ADD COLUMN latitude REAL",
        "ALTER TABLE imoveis ADD COLUMN longitude REAL",
    ]:
        try:
            conn.execute(col_ddl)
        except Exception:
            pass  # coluna já existe

    conn.commit()


def _init_memory_schema(conn: sqlite3.Connection):
    """Inicializa schema do banco de memória (messages + user_profiles).

    Single-tenant: sem coluna tenant_id; user_id é PK única.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            nome TEXT DEFAULT '',
            preferencias TEXT DEFAULT '{}',
            contexto_resumo TEXT DEFAULT '',
            total_mensagens INTEGER DEFAULT 0,
            ultima_atividade REAL
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id)")
    conn.commit()


def _init_shared_schema(conn: sqlite3.Connection):
    """Inicializa schema do banco de memória compartilhada (cross-session learnings).

    Single-tenant: sem source_tenant_id.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shared_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            quality_score REAL DEFAULT 0.5,
            times_used INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS improved_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_pattern TEXT,
            improved_answer TEXT,
            votes_up INTEGER DEFAULT 0,
            votes_down INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS knowledge_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            fact TEXT,
            confidence REAL DEFAULT 0.8,
            source TEXT DEFAULT 'learned',
            created_at REAL
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_insights_score "
        "ON shared_insights(quality_score DESC, times_used DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_knowledge_conf "
        "ON knowledge_entries(confidence DESC)"
    )
    conn.commit()


# ── Public getters ───────────────────────────────────────────────────────

def get_imoveis_db() -> sqlite3.Connection:
    """Retorna conexão com o banco de imóveis do tenant único."""
    global _imoveis_db
    if _imoveis_db is None:
        path = imoveis_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _imoveis_db = sqlite3.connect(str(path), check_same_thread=False)
        _imoveis_db.execute("PRAGMA journal_mode=WAL")
        _imoveis_db.execute("PRAGMA foreign_keys=ON")
        _register_unaccent(_imoveis_db)
        _init_imoveis_schema(_imoveis_db)
        log.info("Imoveis DB aberto: %s", path)
    return _imoveis_db


def get_memory_db() -> sqlite3.Connection:
    """Retorna conexão com o banco de memória privada (messages + user_profiles)."""
    global _memory_db
    if _memory_db is None:
        path = memory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _memory_db = sqlite3.connect(str(path), check_same_thread=False)
        _memory_db.execute("PRAGMA journal_mode=WAL")
        _init_memory_schema(_memory_db)
        log.info("Memory DB aberto: %s", path)
    return _memory_db


def get_fsm_db() -> sqlite3.Connection:
    """Retorna conexão com o banco de FSM (mesmo banco que SQLiteFSMStorage usa).

    OBS: O init de schema é feito pelo próprio SQLiteFSMStorage. Aqui
    apenas garantimos que o diretório existe e retornamos a conexão.
    """
    global _fsm_db
    if _fsm_db is None:
        path = fsm_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _fsm_db = sqlite3.connect(str(path), check_same_thread=False)
        _fsm_db.execute("PRAGMA journal_mode=WAL")
        log.info("FSM DB path: %s", path)
    return _fsm_db


def get_shared_db() -> sqlite3.Connection:
    """Retorna conexão com o banco de memória compartilhada (cross-session learnings)."""
    global _shared_db
    if _shared_db is None:
        path = shared_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _shared_db = sqlite3.connect(str(path), check_same_thread=False)
        _shared_db.execute("PRAGMA journal_mode=WAL")
        _init_shared_schema(_shared_db)
        log.info("Shared memory DB aberto: %s", path)
    return _shared_db


def get_qdrant_collection() -> str:
    """Nome da coleção Qdrant (fixo em modo single-tenant)."""
    return "imoveis"


# ── Stats (para o dashboard) ─────────────────────────────────────────────

def get_tenant_stats() -> dict:
    """Stats rápidas do tenant único (usado pelo dashboard)."""
    conn = get_imoveis_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM imoveis").fetchone()[0]
    except Exception:
        total = 0
    try:
        disponiveis = conn.execute(
            "SELECT COUNT(*) FROM imoveis WHERE situacao='Disponivel' OR situacao IS NULL"
        ).fetchone()[0]
    except Exception:
        disponiveis = 0
    return {"total_imoveis": total, "disponiveis": disponiveis}
