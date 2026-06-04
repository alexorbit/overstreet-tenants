"""Busca semântica via Qdrant + fastembed — per-tenant collections."""
import json
import re
import sqlite3
import logging
from overstreet.db.imoveis import _query_dict, _query_dicts

log = logging.getLogger("overstreet.search.semantic")


def busca_semantica(conn: sqlite3.Connection, qdrant, embedder,
                    query: str, limit: int = 5,
                    collection_name: str = "imoveis") -> list[dict]:
    """Qdrant encontra IDs → SQLite busca dados completos."""
    if qdrant is None or embedder is None:
        return []
    try:
        query_vector = list(embedder.embed([query]))[0]
        response = qdrant.query_points(
            collection_name=collection_name,
            query=query_vector.tolist(),
            limit=limit,
            score_threshold=0.25,
        )
        id_score_map: dict[int, float] = {}
        for r in response.points:
            imovel_id = r.payload.get("id") if r.payload else r.id
            id_score_map[imovel_id] = r.score

        if not id_score_map:
            return []

        imoveis = []
        for imovel_id, score in id_score_map.items():
            rdict = _query_dict(conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
            if rdict:
                rdict["_score"] = score
                imoveis.append(rdict)
        return imoveis
    except Exception as e:
        log.error("Erro busca semantica: %s", e)
        return []


def busca_por_localizacao(conn: sqlite3.Connection, nvidia,
                          place: str, radius_km: float = 5.0,
                          limit: int = 10) -> list[dict]:
    """Infere bairros/cidades via NIM e busca no SQLite."""
    from overstreet.config import NVIDIA_MODEL
    try:
        geo_resp = nvidia.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    f"Liste bairros e cidades num raio de {radius_km}km de: {place}, Sao Paulo, Brasil.\n"
                    f'Responda APENAS em JSON: {{"bairros": ["bairro1", ...], "cidades": ["cidade1"]}}'
                )
            }], temperature=0.1, max_tokens=200,
        )
        geo_text = geo_resp.choices[0].message.content.strip()
        geo_match = re.search(r'\{.*\}', geo_text, re.DOTALL)
        loc = json.loads(geo_match.group()) if geo_match else {"bairros": [place], "cidades": []}
    except Exception:
        loc = {"bairros": [place], "cidades": []}

    log.info("Localização inferida: %s", loc)

    seen_ids: set[int] = set()
    all_results = []

    for term in (loc.get("bairros", []) + loc.get("cidades", [])) or [place]:
        if not term:
            continue
        rows = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE district LIKE ? OR city LIKE ? LIMIT 20",
            (f"%{term}%", f"%{term}%")
        )
        for rd in rows:
            if rd["id"] not in seen_ids:
                seen_ids.add(rd["id"])
                all_results.append(rd)

    return all_results[:limit]
