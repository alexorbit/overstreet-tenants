"""Busca híbrida: filtros SQLite + re-ranking Qdrant."""
import sqlite3
import logging
from overstreet.config import COLLECTION_NAME, MAX_RESULTS
from overstreet.search.filters import extract_filters
from overstreet.search.semantic import busca_semantica
from overstreet.config import TIPO_GROUPS
from overstreet.db.imoveis import _query_dicts

log = logging.getLogger("overstreet.search.hybrid")


def busca_hibrida(conn: sqlite3.Connection, qdrant, embedder,
                  text: str, limit: int = MAX_RESULTS * 2) -> list[dict]:
    """
    1. Extrai filtros heurísticos
    2. Filtra no SQLite
    3. Re-rankeia com Qdrant se disponível
    """
    filters = extract_filters(text)
    conditions = []
    params = []

    if filters.get("tipo"):
        tipo_key = filters["tipo"].lower().strip()
        tipo_ids = TIPO_GROUPS.get(tipo_key)
        if tipo_ids:
            placeholders = ",".join(["?"] * len(tipo_ids))
            conditions.append(f"property_type IN ({placeholders})")
            params.extend(tipo_ids)

    if filters.get("quartos_min"):
        conditions.append("bedrooms >= ?")
        params.append(filters["quartos_min"])

    if filters.get("suites_min"):
        conditions.append("suites >= ?")
        params.append(filters["suites_min"])

    if filters.get("vagas_min"):
        conditions.append("garage >= ?")
        params.append(filters["vagas_min"])

    if filters.get("bairro"):
        conditions.append("(district LIKE ? OR street LIKE ?)")
        b = f"%{filters['bairro']}%"
        params.extend([b, b])

    if filters.get("finalidade"):
        fin = filters["finalidade"].lower()
        if "alug" in fin:
            conditions.append("finalidade LIKE ?")
            params.append("%aluguel%")
        elif "venda" in fin or "compra" in fin:
            conditions.append("finalidade LIKE ?")
            params.append("%venda%")

    if not filters.get("todos"):
        conditions.append("situacao = 'Disponivel'")

    if conditions:
        where = " AND ".join(conditions)
        results = _query_dicts(
            conn,
            f"SELECT * FROM imoveis WHERE {where} ORDER BY id DESC LIMIT {limit}",
            params
        )
    else:
        results = busca_semantica(conn, qdrant, embedder, text, limit=limit)
        if results:
            return results[:MAX_RESULTS]
        results = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE situacao = 'Disponivel' AND "
            "(district LIKE ? OR street LIKE ? OR description LIKE ?) LIMIT ?",
            (f"%{text}%", f"%{text}%", f"%{text}%", limit)
        )

    # Re-ranking com Qdrant
    if results and len(results) > 3 and qdrant is not None and embedder is not None:
        try:
            query_vector = list(embedder.embed([text]))[0].tolist()
            q_results = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=query_vector,
                limit=limit,
                score_threshold=0.2,
            )
            qdrant_scores = {
                (p.payload.get("id") if p.payload else p.id): p.score
                for p in q_results.points
            }
            for r in results:
                r["_score"] = qdrant_scores.get(r["id"], 0.5)
            results.sort(key=lambda x: x.get("_score", 0), reverse=True)
        except Exception:
            pass

    return results[:MAX_RESULTS]


def busca_por_filtros(conn: sqlite3.Connection,
                      bairro: str | None = None, cidade: str | None = None,
                      quartos: int | None = None, cep: str | None = None,
                      limit: int = MAX_RESULTS * 2) -> list[dict]:
    conditions = []
    params = []
    if bairro:
        conditions.append("district LIKE ?")
        params.append(f"%{bairro}%")
    if cidade:
        conditions.append("(city LIKE ? OR district LIKE ?)")
        params.extend([f"%{cidade}%", f"%{cidade}%"])
    if quartos:
        conditions.append("bedrooms >= ?")
        params.append(quartos)
    if cep:
        conditions.append("zip LIKE ?")
        params.append(f"{cep}%")
    if not conditions:
        return []
    where = " AND ".join(conditions)
    return _query_dicts(
        conn,
        f"SELECT * FROM imoveis WHERE {where} ORDER BY id DESC LIMIT {limit}",
        params
    )
