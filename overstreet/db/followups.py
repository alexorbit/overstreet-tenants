"""CRUD para tabela followups (single-tenant, sem tenant_id)."""
import sqlite3
import logging

log = logging.getLogger("overstreet.db.followups")


def create_followups_table(conn: sqlite3.Connection):
    """Cria tabela de follow-ups (idempotente)."""
    conn.execute("""
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
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_followups_status_prazo "
        "ON followups(status, data_prazo)"
    )
    conn.commit()
    log.info("Tabela followups criada/verificada")


def _query_dict(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def _query_dicts(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def insert_followup(
    conn: sqlite3.Connection,
    tipo: str,
    data_prazo: str,
    descricao: str = "",
    cliente_id: int | None = None,
    cliente_nome: str | None = None,
    imovel_id: int | None = None,
    **kwargs,
) -> int:
    """Insere novo follow-up. Aceita `tenant_id=` em kwargs (ignorado).

    Retorna o id.
    """
    cur = conn.execute(
        "INSERT INTO followups "
        "(tipo, data_prazo, descricao, cliente_id, cliente_nome, imovel_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tipo, data_prazo, descricao,
         cliente_id, cliente_nome, imovel_id),
    )
    conn.commit()
    log.info("Follow-up inserido: id=%d tipo=%s prazo=%s", cur.lastrowid, tipo, data_prazo)
    return cur.lastrowid


def get_followup_by_id(conn: sqlite3.Connection, followup_id: int) -> dict | None:
    return _query_dict(conn, "SELECT * FROM followups WHERE id = ?", (followup_id,))


def mark_done(conn: sqlite3.Connection, followup_id: int) -> bool:
    """Marca follow-up como concluído. Retorna True se atualizou."""
    cursor = conn.execute(
        "UPDATE followups SET status = 'feito', "
        "concluido_em = datetime('now','localtime') "
        "WHERE id = ? AND status != 'feito'",
        (followup_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_adiado(conn: sqlite3.Connection, followup_id: int, nova_data: str) -> bool:
    """Marca follow-up como adiado com nova data."""
    cursor = conn.execute(
        "UPDATE followups SET status = 'adiado', data_prazo = ? WHERE id = ?",
        (nova_data, followup_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_followup(conn: sqlite3.Connection, followup_id: int) -> bool:
    """Remove follow-up."""
    cursor = conn.execute("DELETE FROM followups WHERE id = ?", (followup_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_pending(conn: sqlite3.Connection, **kwargs) -> list[dict]:
    """Lista todos follow-ups pendentes, ordenados por data_prazo.

    Aceita `tenant_id=` em kwargs (ignorado).
    """
    return _query_dicts(
        conn,
        "SELECT * FROM followups "
        "WHERE status = 'pendente' "
        "ORDER BY data_prazo ASC",
    )


def list_by_status_range(
    conn: sqlite3.Connection,
    status: str = "pendente",
    future_days: int = 7,
    **kwargs,
) -> list[dict]:
    """Lista follow-ups por status e faixa de data.

    Se future_days=0, traz todos pendentes (incluindo atrasados).
    Aceita `tenant_id=` em kwargs (ignorado).
    """
    if future_days <= 0:
        return _query_dicts(
            conn,
            "SELECT * FROM followups "
            "WHERE status = ? "
            "ORDER BY data_prazo ASC",
            (status,),
        )
    return _query_dicts(
        conn,
        "SELECT * FROM followups "
        "WHERE status = ? "
        "AND date(data_prazo) <= date('now','localtime', '+' || ? || ' days') "
        "ORDER BY data_prazo ASC",
        (status, future_days),
    )


def list_atrasados(conn: sqlite3.Connection, **kwargs) -> list[dict]:
    """Lista follow-ups pendentes com data_prazo anterior a hoje."""
    return _query_dicts(
        conn,
        "SELECT * FROM followups "
        "WHERE status = 'pendente' "
        "AND date(data_prazo) < date('now','localtime') "
        "ORDER BY data_prazo ASC",
    )


def list_hoje(conn: sqlite3.Connection, **kwargs) -> list[dict]:
    """Lista follow-ups pendentes de hoje."""
    return _query_dicts(
        conn,
        "SELECT * FROM followups "
        "WHERE status = 'pendente' "
        "AND date(data_prazo) = date('now','localtime') "
        "ORDER BY data_prazo ASC",
    )


def list_proximos(conn: sqlite3.Connection, days: int = 7, **kwargs) -> list[dict]:
    """Lista follow-ups pendentes dos próximos N dias (excluindo hoje e atrasados)."""
    return _query_dicts(
        conn,
        "SELECT * FROM followups "
        "WHERE status = 'pendente' "
        "AND date(data_prazo) > date('now','localtime') "
        "AND date(data_prazo) <= date('now','localtime', '+' || ? || ' days') "
        "ORDER BY data_prazo ASC",
        (days,),
    )
