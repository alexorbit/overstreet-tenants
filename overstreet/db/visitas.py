"""CRUD para tabela visitas (single-tenant, sem tenant_id)."""
import sqlite3
import logging

log = logging.getLogger("overstreet.db.visitas")


def create_visitas_table(conn: sqlite3.Connection):
    """Cria tabela de visitas (idempotente)."""
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visitas_data ON visitas(data_visita)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visitas_imovel ON visitas(imovel_id)"
    )
    conn.commit()
    log.info("Tabela visitas criada/verificada")


def insert_visita(
    conn: sqlite3.Connection,
    imovel_id: int,
    data_visita: str,
    cliente_id: int | None = None,
    cliente_nome: str | None = None,
    notas: str | None = None,
    **kwargs,
) -> int:
    """Insere nova visita. Aceita `tenant_id=` em kwargs (ignorado em single-tenant).

    Retorna o id.
    """
    cur = conn.execute(
        "INSERT INTO visitas (imovel_id, cliente_id, cliente_nome, "
        "data_visita, notas) VALUES (?, ?, ?, ?, ?)",
        (imovel_id, cliente_id, cliente_nome, data_visita, notas),
    )
    conn.commit()
    log.info("Visita inserida: id=%d imovel=%d data=%s", cur.lastrowid, imovel_id, data_visita)
    return cur.lastrowid


def get_visita_by_id(conn: sqlite3.Connection, visita_id: int) -> dict | None:
    """Busca visita por ID."""
    cursor = conn.execute("SELECT * FROM visitas WHERE id = ?", (visita_id,))
    if not cursor.description:
        return None
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def list_visitas_upcoming(conn: sqlite3.Connection, days: int = 7, **kwargs) -> list[dict]:
    """Lista visitas agendadas dos próximos N dias (incluindo hoje).

    Aceita `tenant_id=` em kwargs (ignorado).
    """
    cursor = conn.execute(
        "SELECT * FROM visitas "
        "WHERE status = 'agendada' "
        "AND date(data_visita) BETWEEN date('now','localtime') "
        "AND date('now','localtime', '+' || ? || ' days') "
        "ORDER BY data_visita ASC",
        (days,),
    )
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def update_visita_status(conn: sqlite3.Connection, visita_id: int, status: str):
    """Atualiza status da visita."""
    conn.execute(
        "UPDATE visitas SET status = ?, atualizada_em = datetime('now','localtime') "
        "WHERE id = ?",
        (status, visita_id),
    )
    conn.commit()


def update_visita_data(conn: sqlite3.Connection, visita_id: int, data_visita: str):
    """Atualiza data da visita (remarcagem)."""
    conn.execute(
        "UPDATE visitas SET data_visita = ?, status = 'remarcada', "
        "atualizada_em = datetime('now','localtime') WHERE id = ?",
        (data_visita, visita_id),
    )
    conn.commit()


def list_visitas_hoje(conn: sqlite3.Connection, **kwargs) -> list[dict]:
    """Lista visitas do dia. Aceita `tenant_id=` em kwargs (ignorado)."""
    cursor = conn.execute(
        "SELECT * FROM visitas "
        "WHERE status = 'agendada' "
        "AND date(data_visita) = date('now','localtime') "
        "ORDER BY data_visita ASC",
    )
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
