"""CRUD para tabela imoveis (single-tenant)."""
import sqlite3
import time
import logging
import asyncio
from overstreet.config import TIPO_GROUPS

log = logging.getLogger("overstreet.db.imoveis")


# ── Helpers ──────────────────────────────────────────────────────────────

def _query_dict(conn: sqlite3.Connection, sql: str, params=()) -> dict | None:
    """Executa query e retorna dict sem alterar row_factory da conexão."""
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def _query_dicts(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    """Executa query e retorna lista de dicts sem alterar row_factory da conexão."""
    cursor = conn.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# ── Reads ────────────────────────────────────────────────────────────────

def get_imovel_by_id(conn: sqlite3.Connection, imovel_id: int) -> dict | None:
    return _query_dict(conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))


def get_imoveis_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    return _query_dicts(
        conn,
        f"SELECT * FROM imoveis WHERE id IN ({placeholders})",
        ids
    )


def get_fotos(conn: sqlite3.Connection, imovel_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT file_id FROM imovel_fotos WHERE imovel_id = ? ORDER BY ordem",
        (imovel_id,)
    ).fetchall()
    return [r[0] for r in rows]


def count_fotos(conn: sqlite3.Connection, imovel_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM imovel_fotos WHERE imovel_id = ?", (imovel_id,)
    ).fetchone()[0]


def list_imoveis_paginated(
    conn: sqlite3.Connection,
    page: int = 1,
    per_page: int = 20,
    filters: dict | None = None,
) -> list[dict]:
    """Lista imóveis com paginação e filtros opcionais.

    filters suporta: city, district, situacao, finalidade, tipo_ids (list).
    Retorna lista de dicts ordenados por id DESC.
    """
    where = []
    params: list = []
    if filters:
        if filters.get("city"):
            where.append("city LIKE ?")
            params.append(f"%{filters['city']}%")
        if filters.get("district"):
            where.append("district LIKE ?")
            params.append(f"%{filters['district']}%")
        if filters.get("situacao"):
            where.append("situacao = ?")
            params.append(filters["situacao"])
        if filters.get("finalidade"):
            where.append("finalidade = ?")
            params.append(filters["finalidade"])
        if filters.get("tipo_ids"):
            placeholders = ",".join(["?"] * len(filters["tipo_ids"]))
            where.append(f"property_type IN ({placeholders})")
            params.extend(filters["tipo_ids"])

    sql = "SELECT * FROM imoveis"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([per_page, max(0, (page - 1) * per_page)])
    return _query_dicts(conn, sql, params)


def count_imoveis(conn: sqlite3.Connection, filters: dict | None = None) -> int:
    """Conta imóveis com filtros opcionais."""
    where = []
    params: list = []
    if filters:
        if filters.get("city"):
            where.append("city LIKE ?")
            params.append(f"%{filters['city']}%")
        if filters.get("district"):
            where.append("district LIKE ?")
            params.append(f"%{filters['district']}%")
        if filters.get("situacao"):
            where.append("situacao = ?")
            params.append(filters["situacao"])
        if filters.get("finalidade"):
            where.append("finalidade = ?")
            params.append(filters["finalidade"])
        if filters.get("tipo_ids"):
            placeholders = ",".join(["?"] * len(filters["tipo_ids"]))
            where.append(f"property_type IN ({placeholders})")
            params.extend(filters["tipo_ids"])

    sql = "SELECT COUNT(*) FROM imoveis"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(sql, params).fetchone()[0]


# ── Writes ───────────────────────────────────────────────────────────────

def insert_imovel(conn: sqlite3.Connection, data: dict, **kwargs) -> int:
    """Insere novo imóvel e atualiza FTS5. Retorna o novo id.

    Aceita `tenant_id=` em kwargs por compatibilidade com chamadas antigas
    (single-tenant: ignorado).
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    tipo_id = _resolve_tipo_id(data.get("tipo", ""))

    # Montar full_text para embedding/FTS
    parts = []
    for field in ["description", "street", "district", "city", "state",
                  "bedrooms", "bathrooms", "garage", "sale_price", "rental_price",
                  "built_area", "area_util", "finalidade"]:
        v = data.get(field)
        if v and str(v).strip() not in ("", "None", "0", "0.00"):
            parts.append(str(v))
    full_text = " ".join(parts)

    cur = conn.execute("""
        INSERT INTO imoveis (
            street, number, district, city, state, zip, bedrooms, bathrooms, garage,
            land_area, built_area, area_util, sale_price, rental_price, condo_fee,
            suites, description, property_type, tipo_imovel_id, finalidade, situacao,
            complement, reference, full_text, created_at, updated_at,
            owner_name, owner_mobile, owner_phone, owner_email,
            latitude, longitude
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?
        )
    """, (
        data.get("street"), data.get("number"), data.get("district"),
        data.get("city", ""), data.get("state", "SP"), data.get("zip"),
        data.get("bedrooms"), data.get("bathrooms"), data.get("garage"),
        data.get("land_area"), data.get("built_area"), data.get("area_util"),
        data.get("sale_price"), data.get("rental_price"), data.get("condo_fee"),
        data.get("suites"), data.get("description"), tipo_id, tipo_id,
        data.get("finalidade", "Venda"), data.get("situacao", "Disponivel"),
        data.get("complement"), data.get("reference"), full_text,
        now, now,
        data.get("owner_name"), data.get("owner_mobile"),
        data.get("owner_phone"), data.get("owner_email"),
        data.get("latitude"), data.get("longitude"),
    ))
    new_id = cur.lastrowid
    conn.commit()

    # Atualizar FTS5
    _update_fts(conn, new_id, full_text)
    log.info(f"Imóvel inserido: COD {new_id}")
    return new_id


def insert_imovel_from_dict(conn: sqlite3.Connection, data: dict) -> int:
    """Helper que aceita dict de form e insere imóvel.

    Equivalente a `insert_imovel(conn, data)` em single-tenant.
    """
    return insert_imovel(conn, data)


def update_imovel(conn: sqlite3.Connection, imovel_id: int, data: dict) -> bool:
    """Atualiza campos de um imóvel existente. Retorna True se atualizou."""
    if not data:
        return False
    allowed = {
        "street", "number", "district", "city", "state", "zip",
        "bedrooms", "bathrooms", "garage", "suites",
        "land_area", "built_area", "area_util",
        "sale_price", "rental_price", "condo_fee", "iptu",
        "description", "property_type", "finalidade", "situacao",
        "complement", "reference", "apartment", "salas",
        "owner_name", "owner_phone", "owner_mobile", "owner_email",
        "latitude", "longitude",
    }
    fields = []
    params: list = []
    for k, v in data.items():
        if k in allowed:
            fields.append(f"{k} = ?")
            params.append(v)
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(time.strftime("%Y-%m-%d %H:%M:%S"))
    params.append(imovel_id)
    cur = conn.execute(
        f"UPDATE imoveis SET {', '.join(fields)} WHERE id = ?",
        params
    )
    conn.commit()
    if cur.rowcount:
        # Refaz full_text e atualiza FTS5
        try:
            full_text = _build_full_text(conn, imovel_id)
            if full_text is not None:
                _update_fts(conn, imovel_id, full_text)
        except Exception as e:
            log.warning("FTS update após update_imovel falhou: %s", e)
        return True
    return False


def delete_imovel(conn: sqlite3.Connection, imovel_id: int) -> bool:
    """Deleta imóvel e remove do FTS. Retorna True se deletou."""
    cur = conn.execute("DELETE FROM imoveis WHERE id = ?", (imovel_id,))
    conn.commit()
    if cur.rowcount:
        try:
            conn.execute("DELETE FROM imoveis_fts WHERE id = ?", (imovel_id,))
            conn.commit()
        except Exception as e:
            log.warning("FTS delete falhou para %d: %s", imovel_id, e)
        return True
    return False


def _build_full_text(conn: sqlite3.Connection, imovel_id: int):
    """Reconstrói full_text de um imóvel (usado após update)."""
    row = _query_dict(conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
    if not row:
        return None
    parts = []
    for field in ["description", "street", "district", "city", "state",
                  "bedrooms", "bathrooms", "garage", "sale_price", "rental_price",
                  "built_area", "area_util", "finalidade"]:
        v = row.get(field)
        if v and str(v).strip() not in ("", "None", "0", "0.00"):
            parts.append(str(v))
    return " ".join(parts)


def _update_fts(conn: sqlite3.Connection, imovel_id: int, content: str):
    try:
        conn.execute(
            "INSERT OR REPLACE INTO imoveis_fts (id, content) VALUES (?, ?)",
            (imovel_id, content)
        )
        conn.commit()
    except Exception as e:
        log.warning(f"FTS update falhou para {imovel_id}: {e}")


def add_foto(conn: sqlite3.Connection, imovel_id: int, file_id: str,
             ordem: int = 0, **kwargs) -> int:
    """Adiciona foto a um imóvel. Aceita `tenant_id=` em kwargs (ignorado)."""
    cur = conn.execute(
        "INSERT INTO imovel_fotos (imovel_id, file_id, ordem, created_at) "
        "VALUES (?, ?, ?, ?)",
        (imovel_id, file_id, ordem, time.time())
    )
    conn.commit()
    return cur.lastrowid


# ── Tipo resolution ─────────────────────────────────────────────────────

def _resolve_tipo_id(tipo_nome: str) -> str | None:
    if not tipo_nome:
        return None
    key = tipo_nome.lower().strip()
    ids = TIPO_GROUPS.get(key)
    return ids[0] if ids else None
