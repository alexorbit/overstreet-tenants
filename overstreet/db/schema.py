"""Schema SQLite e migrations do OverStreet-Corretor-Agent (single-tenant)."""
import sqlite3
import logging

log = logging.getLogger("overstreet.db.schema")


# ── Imóveis ──────────────────────────────────────────────────────────────
# 38 colunas + latitude + longitude = 40 (latitude/longitude adicionadas em migration).
IMOVEIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS imoveis (
    id INTEGER PRIMARY KEY,
    owner_name TEXT,
    owner_phone TEXT,
    owner_mobile TEXT,
    owner_email TEXT,
    street TEXT,
    number TEXT,
    district TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    bedrooms INTEGER,
    bathrooms INTEGER,
    garage INTEGER,
    land_area REAL,
    built_area REAL,
    sale_price TEXT,
    rental_price TEXT,
    condo_fee TEXT,
    iptu TEXT,
    description TEXT,
    property_type TEXT,
    finalidade TEXT,
    situacao TEXT,
    complement TEXT,
    reference TEXT,
    apartment TEXT,
    suites INTEGER,
    salas INTEGER,
    area_util REAL,
    tipo_imovel_id INTEGER,
    agencia_id INTEGER,
    created_at TEXT,
    updated_at TEXT,
    full_text TEXT,
    latitude REAL,
    longitude REAL
)
"""


# ── Clientes / aliases / favoritos (single-tenant, sem tenant_id/FK) ────

CLIENTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT,
    whatsapp TEXT,
    perfil_descricao TEXT,
    created_at REAL
)
"""

CLIENTE_ALIASES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cliente_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE (alias),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
)
"""

CLIENTE_FAVORITOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS cliente_favoritos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL,
    imovel_id INTEGER NOT NULL,
    created_at REAL,
    UNIQUE (cliente_id, imovel_id),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
)
"""


# ── Fotos de imóveis (single-tenant, sem tenant_id) ──────────────────────

IMOVEL_FOTOS_SCHEMA = """
CREATE TABLE IF NOT EXISTS imovel_fotos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imovel_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    ordem INTEGER DEFAULT 0,
    created_at REAL
)
"""


# ── Visitas (single-tenant) ──────────────────────────────────────────────

VISITAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS visitas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imovel_id INTEGER NOT NULL,
    cliente_id INTEGER,
    cliente_nome TEXT,
    data_visita TEXT NOT NULL,
    status TEXT DEFAULT 'agendada',
    notas TEXT,
    criada_em TEXT DEFAULT (datetime('now','localtime')),
    atualizada_em TEXT DEFAULT (datetime('now','localtime'))
)
"""


# ── Follow-ups (single-tenant) ───────────────────────────────────────────

FOLLOWUPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER,
    cliente_nome TEXT,
    imovel_id INTEGER,
    tipo TEXT NOT NULL,
    descricao TEXT,
    data_prazo TEXT NOT NULL,
    status TEXT DEFAULT 'pendente',
    criado_em TEXT DEFAULT (datetime('now','localtime')),
    concluido_em TEXT
)
"""


# ── Chaves (single-tenant) ───────────────────────────────────────────────

CHAVES_SCHEMA = """
CREATE TABLE IF NOT EXISTS chaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imovel_id INTEGER NOT NULL,
    status TEXT DEFAULT 'imobiliaria',
    local TEXT,
    retirada_por TEXT,
    devolvida_em TEXT,
    criado_em TEXT DEFAULT (datetime('now','localtime'))
)
"""


# ── FSM state (gerenciado pelo SQLiteFSMStorage, mantido aqui p/ ref) ───

FSM_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_state (
    chat_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    state TEXT,
    data TEXT DEFAULT '{}',
    PRIMARY KEY (chat_id, user_id)
)
"""


# ── Memória privada (messages + user_profiles; single-tenant, sem tenant_id)

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
)
"""

USER_PROFILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY,
    nome TEXT DEFAULT '',
    preferencias TEXT DEFAULT '{}',
    contexto_resumo TEXT DEFAULT '',
    total_mensagens INTEGER DEFAULT 0,
    ultima_atividade REAL
)
"""


# ── Memória compartilhada (cross-session learnings) ──────────────────────

SHARED_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    quality_score REAL DEFAULT 0.5,
    times_used INTEGER DEFAULT 0,
    created_at REAL
)
"""

IMPROVED_RESPONSES_SCHEMA = """
CREATE TABLE IF NOT EXISTS improved_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_pattern TEXT,
    improved_answer TEXT,
    votes_up INTEGER DEFAULT 0,
    votes_down INTEGER DEFAULT 0,
    created_at REAL
)
"""

KNOWLEDGE_ENTRIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT,
    fact TEXT,
    confidence REAL DEFAULT 0.8,
    source TEXT DEFAULT 'learned',
    created_at REAL
)
"""


# ── Índices (sem idx_*_tenant) ───────────────────────────────────────────

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_imoveis_city ON imoveis(city)",
    "CREATE INDEX IF NOT EXISTS idx_imoveis_situacao ON imoveis(situacao)",
    "CREATE INDEX IF NOT EXISTS idx_imoveis_district ON imoveis(district)",
    "CREATE INDEX IF NOT EXISTS idx_imoveis_city_district ON imoveis(city, district)",
    "CREATE INDEX IF NOT EXISTS idx_imoveis_finalidade ON imoveis(finalidade)",
    "CREATE INDEX IF NOT EXISTS idx_clientes_nome ON clientes(nome)",
    "CREATE INDEX IF NOT EXISTS idx_aliases_alias ON cliente_aliases(alias)",
    "CREATE INDEX IF NOT EXISTS idx_favoritos_cliente ON cliente_favoritos(cliente_id)",
    "CREATE INDEX IF NOT EXISTS idx_fotos_imovel ON imovel_fotos(imovel_id)",
    "CREATE INDEX IF NOT EXISTS idx_visitas_data ON visitas(data_visita)",
    "CREATE INDEX IF NOT EXISTS idx_visitas_imovel ON visitas(imovel_id)",
    "CREATE INDEX IF NOT EXISTS idx_followups_status_prazo ON followups(status, data_prazo)",
    "CREATE INDEX IF NOT EXISTS idx_chaves_status ON chaves(status)",
    "CREATE INDEX IF NOT EXISTS idx_chaves_imovel ON chaves(imovel_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id)",
]


# ── Helpers de migration ─────────────────────────────────────────────────

def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_def: str):
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        log.info(f"Migration: adicionado {table}.{column}")


# ── Inits granulares por tipo de DB ─────────────────────────────────────

def init_imoveis_db(conn: sqlite3.Connection):
    """Cria tabelas do DB de imóveis (idempotente)."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for ddl in [
        IMOVEIS_SCHEMA, IMOVEL_FOTOS_SCHEMA,
        CLIENTES_SCHEMA, CLIENTE_ALIASES_SCHEMA, CLIENTE_FAVORITOS_SCHEMA,
        VISITAS_SCHEMA, FOLLOWUPS_SCHEMA, CHAVES_SCHEMA,
    ]:
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    # Migrations: latitude/longitude
    try:
        _add_column_if_missing(conn, "imoveis", "latitude", "REAL")
        _add_column_if_missing(conn, "imoveis", "longitude", "REAL")
    except Exception:
        pass
    # AUTOINCREMENT sequence: garante continuidade para novos IDs
    try:
        conn.execute("""
            INSERT INTO sqlite_sequence (name, seq)
            VALUES ('imoveis', (SELECT COALESCE(MAX(id), 0) FROM imoveis))
            ON CONFLICT(name) DO UPDATE SET
                seq = MAX(seq, (SELECT COALESCE(MAX(id), 0) FROM imoveis))
        """)
    except Exception:
        pass
    conn.commit()
    log.info("Imoveis DB schema inicializado")


def init_memory_db(conn: sqlite3.Connection):
    """Cria tabelas do DB de memória privada (idempotente)."""
    conn.execute("PRAGMA journal_mode=WAL")
    for ddl in [MEMORY_SCHEMA, USER_PROFILES_SCHEMA]:
        conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id)")
    conn.commit()
    log.info("Memory DB schema inicializado")


def init_shared_db(conn: sqlite3.Connection):
    """Cria tabelas do DB de memória compartilhada (idempotente)."""
    conn.execute("PRAGMA journal_mode=WAL")
    for ddl in [SHARED_SCHEMA, IMPROVED_RESPONSES_SCHEMA, KNOWLEDGE_ENTRIES_SCHEMA]:
        conn.execute(ddl)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_insights_score "
        "ON shared_insights(quality_score DESC, times_used DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_knowledge_conf "
        "ON knowledge_entries(confidence DESC)"
    )
    conn.commit()
    log.info("Shared DB schema inicializado")
