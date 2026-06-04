"""Busca sintática via FTS5 (BM25)."""
import re
import sqlite3
import logging
from pathlib import Path
from overstreet.config import MAX_RESULTS
from overstreet.db.imoveis import _query_dicts

log = logging.getLogger("overstreet.search.fts")


def fts_escape(query: str) -> str:
    """Escapa caracteres especiais do FTS5 e monta query de prefix match."""
    cleaned = re.sub(r'[^\w\sáàãâéèêíïóòõôúüçñÁÀÃÂÉÈÊÍÏÓÒÕÔÚÜÇÑ-]', ' ', query)
    tokens = cleaned.strip().split()
    if not tokens:
        return ""
    escaped = [f"{t}*" for t in tokens if not (t.isdigit() and len(t) < 4)]
    return " ".join(escaped) if escaped else ""


def busca_sintatica(conn: sqlite3.Connection, text: str,
                    limit: int = MAX_RESULTS * 2) -> list[dict]:
    """Busca via FTS5 com BM25. Retorna lista de dicts com _score."""
    try:
        conn.execute("SELECT COUNT(*) FROM imoveis_fts LIMIT 1")
    except Exception:
        log.warning("FTS5 não disponível")
        return []

    fts_query = fts_escape(text)
    if not fts_query:
        return []

    log.info(f"FTS5: '{fts_query}'")
    try:
        rows = _query_dicts(conn, """
            SELECT i.*, f.rank AS _fts_rank
            FROM imoveis_fts f
            JOIN imoveis i ON i.id = f.id
            WHERE imoveis_fts MATCH ?
              AND (i.situacao = 'Disponivel' OR i.situacao IS NULL OR i.situacao = '')
            ORDER BY f.rank
            LIMIT ?
        """, (fts_query, limit))

        results = []
        for rd in rows:
            rank = rd.pop("_fts_rank", 0)
            rd["_score"] = min(1.0, max(0.0, 1.0 + rank / 10.0))
            results.append(rd)

        log.info(f"FTS5: {len(results)} resultados")
        return results
    except Exception as e:
        log.warning(f"Erro FTS5: {e}")
        return []


def ensure_fts(db_path: Path):
    """Garante que a tabela FTS5 existe e está populada."""
    import sqlite3 as _sq3
    db = _sq3.connect(db_path)
    try:
        count = db.execute("SELECT COUNT(*) FROM imoveis_fts").fetchone()[0]
        if count > 0:
            log.info(f"FTS5 pronta: {count} registros")
            return
    except Exception:
        pass

    log.info("Criando FTS5...")
    db.execute("DROP TABLE IF EXISTS imoveis_fts")
    db.execute("""
        CREATE VIRTUAL TABLE imoveis_fts USING fts5(
            id UNINDEXED,
            content,
            tokenize="porter unicode61"
        )
    """)

    batch = 1000
    offset = 0
    inserted = 0
    while True:
        rows = db.execute("""
            SELECT id, full_text, street, number, district, city, state, zip,
                   bedrooms, bathrooms, garage, sale_price, rental_price,
                   built_area, area_util, suites, description, finalidade,
                   complement, reference, property_type
            FROM imoveis ORDER BY id LIMIT ? OFFSET ?
        """, (batch, offset)).fetchall()
        if not rows:
            break
        for r in rows:
            parts = [str(v) for v in r[1:]
                     if v is not None and str(v).strip() not in ("", "None", "0", "0.00", "0,01")]
            db.execute("INSERT INTO imoveis_fts (id, content) VALUES (?, ?)",
                       (r[0], " ".join(parts)))
            inserted += 1
        offset += batch
        if inserted % 10000 == 0:
            db.commit()

    db.commit()
    log.info(f"FTS5 criada: {inserted} registros")
    db.close()
