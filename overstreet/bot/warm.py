"""Warm singletons (Qdrant client + embedder) — sync, lazy.

Pode ser chamado antes do polling, ou on-demand em background.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_qdrant = None
_embedder = None
_warmed = False


def get_qdrant():
    return _qdrant


def get_embedder():
    return _embedder


def is_warmed() -> bool:
    return _warmed


def warm_singletons() -> None:
    """Inicializa Qdrant client + fastembed embedder. Idempotente."""
    global _qdrant, _embedder, _warmed
    if _warmed:
        return

    from overstreet.config import (
        QDRANT_URL, QDRANT_API_KEY, QDRANT_GRPC_HOST, QDRANT_GRPC_PORT,
    )

    # Qdrant
    try:
        from qdrant_client import QdrantClient
        if QDRANT_GRPC_HOST and QDRANT_GRPC_PORT:
            _qdrant = QdrantClient(
                host=QDRANT_GRPC_HOST, grpc_port=QDRANT_GRPC_PORT,
                api_key=QDRANT_API_KEY or None, prefer_grpc=True, https=False,
            )
            logger.info("Qdrant gRPC: %s:%s", QDRANT_GRPC_HOST, QDRANT_GRPC_PORT)
        else:
            _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
            logger.info("Qdrant HTTP: %s", QDRANT_URL)
    except Exception as e:
        logger.warning("Qdrant indisponível: %s — busca semântica desabilitada", e)
        _qdrant = None

    # Embedder
    try:
        from fastembed import TextEmbedding
        cache = os.getenv("FASTEMBED_CACHE_PATH", "/app/whisper_cache")
        _embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache)
        logger.info("Embedder carregado: BAAI/bge-small-en-v1.5")
    except Exception as e:
        logger.warning("Embedder indisponível: %s", e)
        _embedder = None

    _warmed = True


async def ensure_qdrant_collection() -> None:
    """Cria a collection `imoveis` se não existir."""
    if not _qdrant:
        return
    try:
        from overstreet.infra import get_qdrant_collection
        coll = get_qdrant_collection()
        existing = {c.name for c in _qdrant.get_collections().collections}
        if coll not in existing:
            _qdrant.create_collection(
                collection_name=coll,
                vectors_config={"size": 384, "distance": "Cosine"},
            )
            logger.info("Qdrant collection '%s' criada", coll)
    except Exception as e:
        logger.warning("Qdrant ensure_collection falhou: %s", e)


def ensure_fts_sync() -> None:
    """Garante FTS5 populado para o imoveis.db (idempotente)."""
    try:
        from overstreet.search.fts import ensure_fts
        from overstreet.infra import imoveis_path
        ensure_fts(imoveis_path())
        logger.info("FTS5 OK")
    except Exception as e:
        logger.warning("FTS5 setup falhou: %s", e)


def load_canonical_sync() -> None:
    """Carrega dicionário canônico de grafias (sync)."""
    try:
        from overstreet.db.canonical import load_canonical
        from overstreet.infra import get_imoveis_db
        conn = get_imoveis_db()
        load_canonical(conn)
        logger.info("Dicionário canônico carregado")
    except Exception as e:
        logger.warning("Canonical load falhou: %s", e)
