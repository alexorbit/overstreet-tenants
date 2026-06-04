"""CRUD para controle de chaves de imóveis (single-tenant, sem tenant_id)."""
import sqlite3
import time
import logging

log = logging.getLogger("overstreet.db.chaves")

# Status possíveis
STATUS_IMOBILIARIA = "imobiliaria"
STATUS_COM_CORRETOR = "com_corretor"
STATUS_COM_PROPRIETARIO = "com_proprietario"
STATUS_PERDIDA = "perdida"

VALID_STATUS = {STATUS_IMOBILIARIA, STATUS_COM_CORRETOR, STATUS_COM_PROPRIETARIO, STATUS_PERDIDA}


def _query_dict(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def _query_dicts(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def registrar_chave(conn: sqlite3.Connection, imovel_id: int,
                    local: str = "", **kwargs) -> int:
    """Registra uma nova chave. Aceita `tenant_id=` em kwargs (ignorado).

    Retorna o id.
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO chaves (imovel_id, status, local, criado_em) "
        "VALUES (?, ?, ?, ?)",
        (imovel_id, STATUS_IMOBILIARIA, local, now)
    )
    conn.commit()
    log.info("Chave registrada: imovel_id=%d", imovel_id)
    return cur.lastrowid


def get_chave(conn: sqlite3.Connection, chave_id: int) -> dict | None:
    return _query_dict(conn, "SELECT * FROM chaves WHERE id = ?", (chave_id,))


def get_chave_by_imovel(conn: sqlite3.Connection, imovel_id: int,
                        **kwargs) -> dict | None:
    """Busca chave por imovel_id. Aceita `tenant_id=` em kwargs (ignorado)."""
    return _query_dict(conn, "SELECT * FROM chaves WHERE imovel_id = ?", (imovel_id,))


def list_chaves(conn: sqlite3.Connection, status: str | None = None,
                **kwargs) -> list[dict]:
    """Lista chaves, opcionalmente filtradas por status.

    Aceita `tenant_id=` em kwargs (ignorado).
    """
    if status:
        return _query_dicts(
            conn,
            "SELECT * FROM chaves WHERE status = ? ORDER BY criado_em DESC",
            (status,)
        )
    return _query_dicts(
        conn,
        "SELECT * FROM chaves ORDER BY criado_em DESC"
    )


def retirar_chave(conn: sqlite3.Connection, chave_id: int,
                  retirada_por: str) -> bool:
    """Muda status para 'com_corretor' e registra quem retirou."""
    try:
        conn.execute(
            "UPDATE chaves SET status = ?, retirada_por = ?, devolvida_em = NULL WHERE id = ?",
            (STATUS_COM_CORRETOR, retirada_por, chave_id)
        )
        conn.commit()
        log.info("Chave %d retirada por %s", chave_id, retirada_por)
        return True
    except Exception as e:
        log.warning("Erro ao retirar chave %d: %s", chave_id, e)
        return False


def devolver_chave(conn: sqlite3.Connection, chave_id: int) -> bool:
    """Muda status para 'imobiliaria' e registra data de devolução."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "UPDATE chaves SET status = ?, devolvida_em = ?, retirada_por = NULL WHERE id = ?",
            (STATUS_IMOBILIARIA, now, chave_id)
        )
        conn.commit()
        log.info("Chave %d devolvida", chave_id)
        return True
    except Exception as e:
        log.warning("Erro ao devolver chave %d: %s", chave_id, e)
        return False


def set_chave_local(conn: sqlite3.Connection, chave_id: int, local: str) -> bool:
    """Registra localização física da chave."""
    try:
        conn.execute(
            "UPDATE chaves SET local = ? WHERE id = ?",
            (local, chave_id)
        )
        conn.commit()
        log.info("Local da chave %d atualizado: %s", chave_id, local)
        return True
    except Exception as e:
        log.warning("Erro ao atualizar local da chave %d: %s", chave_id, e)
        return False


def set_chave_status(conn: sqlite3.Connection, chave_id: int, status: str) -> bool:
    """Muda status da chave para qualquer status válido."""
    if status not in VALID_STATUS:
        log.warning("Status inválido: %s", status)
        return False
    try:
        conn.execute(
            "UPDATE chaves SET status = ? WHERE id = ?",
            (status, chave_id)
        )
        conn.commit()
        return True
    except Exception as e:
        log.warning("Erro ao atualizar status da chave %d: %s", chave_id, e)
        return False
