"""CRUD para clientes, aliases e favoritos (single-tenant, sem tenant_id)."""
import sqlite3
import time
import logging

log = logging.getLogger("overstreet.db.clientes")


def insert_cliente(conn: sqlite3.Connection, data: dict, **kwargs) -> int:
    """Insere um novo cliente. Aceita `tenant_id=` em kwargs (ignorado).

    Retorna o id.
    """
    cur = conn.execute(
        "INSERT INTO clientes (nome, email, whatsapp, perfil_descricao, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (data["nome"], data.get("email"), data.get("whatsapp"),
         data.get("perfil_descricao"), time.time())
    )
    conn.commit()
    return cur.lastrowid


def get_cliente_by_id(conn: sqlite3.Connection, cliente_id: int) -> dict | None:
    """Busca cliente por ID usando cursor.description (sem alterar row_factory)."""
    cursor = conn.execute("SELECT * FROM clientes WHERE id = ?", (cliente_id,))
    if not cursor.description:
        return None
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def get_cliente_by_id_safe(conn: sqlite3.Connection, cliente_id: int) -> dict | None:
    return get_cliente_by_id(conn, cliente_id)


def search_clientes(conn: sqlite3.Connection, query: str, **kwargs) -> list[dict]:
    """Busca clientes por nome. Aceita `tenant_id=` em kwargs (ignorado)."""
    cursor = conn.execute(
        "SELECT * FROM clientes WHERE nome LIKE ?",
        (f"%{query}%",)
    )
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def list_clientes(conn: sqlite3.Connection, **kwargs) -> list[dict]:
    """Lista todos os clientes. Aceita `tenant_id=` em kwargs (ignorado)."""
    cursor = conn.execute(
        "SELECT * FROM clientes ORDER BY nome"
    )
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def resolve_alias(conn: sqlite3.Connection, alias: str, **kwargs) -> dict | None:
    """Resolve alias para cliente. Retorna dict do cliente ou None.

    Aceita `tenant_id=` em kwargs (ignorado).
    """
    # Primeira query: JOIN — usar cursor.description para dict (sem row_factory)
    cursor = conn.execute(
        "SELECT c.* FROM clientes c "
        "JOIN cliente_aliases a ON a.cliente_id = c.id "
        "WHERE LOWER(a.alias) = LOWER(?)",
        (alias.strip(),)
    )
    if cursor.description:
        cols = [d[0] for d in cursor.description]
        row = cursor.fetchone()
        if row:
            return dict(zip(cols, row))

    # Tentar pelo nome direto
    cursor = conn.execute(
        "SELECT * FROM clientes WHERE LOWER(nome) LIKE LOWER(?)",
        (f"%{alias.strip()}%",)
    )
    if cursor.description:
        cols = [d[0] for d in cursor.description]
        row = cursor.fetchone()
        if row:
            return dict(zip(cols, row))
    return None


def add_alias(conn: sqlite3.Connection, cliente_id: int, alias: str, **kwargs) -> bool:
    """Adiciona alias para o cliente. Aceita `tenant_id=` em kwargs (ignorado)."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cliente_aliases (cliente_id, alias) VALUES (?, ?)",
            (cliente_id, alias.lower().strip())
        )
        conn.commit()
        return True
    except Exception as e:
        log.warning(f"Alias '{alias}' já existe: {e}")
        return False


def get_aliases(conn: sqlite3.Connection, cliente_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT alias FROM cliente_aliases WHERE cliente_id = ?", (cliente_id,)
    ).fetchall()
    return [r[0] for r in rows]


def add_favorite(conn: sqlite3.Connection, cliente_id: int, imovel_id: int, **kwargs) -> bool:
    """Adiciona imóvel aos favoritos do cliente. Aceita `tenant_id=` em kwargs (ignorado)."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cliente_favoritos (cliente_id, imovel_id, created_at) "
            "VALUES (?, ?, ?)",
            (cliente_id, imovel_id, time.time())
        )
        conn.commit()
        return True
    except Exception as e:
        log.warning(f"Favorito já existe: {e}")
        return False


def remove_favorite(conn: sqlite3.Connection, cliente_id: int, imovel_id: int):
    conn.execute(
        "DELETE FROM cliente_favoritos WHERE cliente_id = ? AND imovel_id = ?",
        (cliente_id, imovel_id)
    )
    conn.commit()


def get_favorites(conn: sqlite3.Connection, cliente_id: int, **kwargs) -> list[int]:
    """Lista IDs de imóveis favoritos do cliente. Aceita `tenant_id=` em kwargs (ignorado)."""
    rows = conn.execute(
        "SELECT imovel_id FROM cliente_favoritos WHERE cliente_id = ? "
        "ORDER BY created_at DESC",
        (cliente_id,)
    ).fetchall()
    return [r[0] for r in rows]


def update_cliente(conn: sqlite3.Connection, cliente_id: int, data: dict):
    fields = []
    params = []
    for key in ("nome", "email", "whatsapp", "perfil_descricao"):
        if key in data:
            fields.append(f"{key} = ?")
            params.append(data[key])
    if not fields:
        return
    params.append(cliente_id)
    conn.execute(f"UPDATE clientes SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
