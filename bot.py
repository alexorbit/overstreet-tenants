"""Overstreet single-tenant entrypoint — runs bot + dashboard no mesmo loop asyncio."""
import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from overstreet.config import (
    BOT_TOKEN, NVIDIA_API_KEY, QDRANT_URL, QDRANT_API_KEY,
    QDRANT_GRPC_HOST, QDRANT_GRPC_PORT,
)
from overstreet.fsm_storage import SQLiteFSMStorage
from overstreet.middleware.tenant import TenantMiddleware
from overstreet.infra import (
    get_imoveis_db, get_memory_db, get_fsm_db, get_shared_db, get_qdrant_collection,
)

logger = logging.getLogger(__name__)

_qdrant = None
_embedder = None


def _warm_singletons() -> None:
    """Inicializa Qdrant client + embedder (sync, roda no threadpool)."""
    global _qdrant, _embedder
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

    try:
        from fastembed import TextEmbedding
        cache = os.getenv("FASTEMBED_CACHE_PATH", "/app/whisper_cache")
        _embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache)
        logger.info("Embedder carregado: BAAI/bge-small-en-v1.5")
    except Exception as e:
        logger.warning("Embedder indisponível: %s", e)
        _embedder = None


async def _ensure_qdrant_collection() -> None:
    """Cria a collection `imoveis` se não existir."""
    if not _qdrant:
        return
    try:
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


def _ensure_fts_sync() -> None:
    """Garante FTS5 populado para o imoveis.db (idempotente)."""
    try:
        from overstreet.search.fts import ensure_fts
        from overstreet.infra import imoveis_path
        ensure_fts(imoveis_path())
        logger.info("FTS5 OK")
    except Exception as e:
        logger.warning("FTS5 setup falhou: %s", e)


async def _ensure_fts() -> None:
    await asyncio.to_thread(_ensure_fts_sync)


def _load_canonical_sync() -> None:
    """Carrega dicionário canônico de grafias (sync)."""
    try:
        from overstreet.db.canonical import load_canonical
        conn = get_imoveis_db()
        load_canonical(conn)
        logger.info("Dicionário canônico carregado")
    except Exception as e:
        logger.warning("Canonical load falhou: %s", e)


def _build_dispatcher() -> Dispatcher:
    """Cria o Dispatcher com FSM storage, middleware e routers."""
    from overstreet.infra import fsm_path
    fsm_storage = SQLiteFSMStorage(fsm_path())
    dp = Dispatcher(storage=fsm_storage)

    # Middleware (no-op, injeta tenant_conn)
    dp.message.middleware(TenantMiddleware())
    dp.callback_query.middleware(TenantMiddleware())

    # Routers
    from overstreet.handlers import (
        commands, callbacks, search, imovel, cliente, media,
        visitas, followups, match, chaves, proposta, proximidade,
        relatorio, mercado, admin,
    )
    for r in [
        commands.router, callbacks.router, search.router,
        imovel.router, cliente.router, media.router,
        visitas.router, followups.router, match.router,
        chaves.router, proposta.router, proximidade.router,
        relatorio.router, mercado.router, admin.router,
    ]:
        dp.include_router(r)

    return dp


async def _run_dashboard() -> None:
    """Sobe o dashboard FastAPI (uvicorn programático)."""
    import uvicorn
    from dashboard.server import app
    config = uvicorn.Config(
        app, host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8000")),
        log_level="info", access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info("Dashboard subindo em :%d", config.port)
    await server.serve()


async def _run_bot() -> None:
    """Sobe o bot Telegram (polling)."""
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher()
    logger.info("Bot iniciando polling...")
    try:
        await dp.start_polling(
            bot,
            conn=get_imoveis_db(),
            qdrant=_qdrant,
            embedder=_embedder,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("Bot encerrado.")


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Validar env vars obrigatórias
    for var in ("BOT_TOKEN", "NVIDIA_API_KEY", "GROQ_API_KEY", "DASHBOARD_PASSWORD"):
        if not os.getenv(var):
            logger.fatal("%s nao definida", var)
            return 2

    # 1. Init SQLite (cria schema se primeira execução)
    logger.info("Inicializando SQLite...")
    get_imoveis_db()
    get_memory_db()
    get_fsm_db()
    get_shared_db()
    logger.info("SQLite OK")

    # 2. Warm Qdrant + embedder (pode demorar 30s no 1º boot por causa do download do modelo)
    logger.info("Carregando Qdrant + embedder...")
    await asyncio.to_thread(_warm_singletons)
    await _ensure_qdrant_collection()

    # 3. Ensure FTS5 + canonical dict
    await _ensure_fts()
    await asyncio.to_thread(_load_canonical_sync)

    # 4. Sobe bot + dashboard em paralelo
    logger.info("Subindo bot + dashboard...")
    await asyncio.gather(
        _run_bot(),
        _run_dashboard(),
    )
    return 0


def _sigterm_handler(*_):
    logger.info("SIGTERM recebido, encerrando...")
    sys.exit(0)


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        signal.signal(signal.SIGTERM, _sigterm_handler)
        sys.exit(asyncio.run(main()))
