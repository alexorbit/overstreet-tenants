"""OverStreet-Corretor-Agent — entry point (multi-bot, multi-tenant SaaS)."""
import asyncio
import logging
import sqlite3

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from overstreet.config import (
    BOT_TOKEN, NVIDIA_API_KEY, QDRANT_URL, QDRANT_API_KEY,
    QDRANT_GRPC_HOST, QDRANT_GRPC_PORT,
)
from overstreet.fsm_storage import SQLiteFSMStorage
from overstreet.middleware.tenant import TenantMiddleware

_qdrant = None
_embedder = None

logger = logging.getLogger(__name__)


def _get_meta_db() -> sqlite3.Connection:
    """Open the global metadata DB."""
    from overstreet.infra import get_meta_db
    conn = get_meta_db()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _warm_singletons():
    global _qdrant, _embedder
    try:
        from qdrant_client import QdrantClient
        if QDRANT_GRPC_HOST and QDRANT_GRPC_PORT:
            _qdrant = QdrantClient(
                host=QDRANT_GRPC_HOST, grpc_port=QDRANT_GRPC_PORT,
                api_key=QDRANT_API_KEY or None, prefer_grpc=True, https=False,
            )
            logger.info("Qdrant gRPC conectado: %s:%s", QDRANT_GRPC_HOST, QDRANT_GRPC_PORT)
        else:
            _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
            logger.info("Qdrant conectado: %s", QDRANT_URL)
    except Exception as e:
        logger.warning("Qdrant indisponível: %s — buscas semânticas desabilitadas", e)
        _qdrant = None

    try:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5", cache_dir="whisper_cache"
        )
        logger.info("Embedder carregado: BAAI/bge-small-en-v1.5")
    except Exception as e:
        logger.warning("Embedder indisponível: %s", e)
        _embedder = None


def _build_dispatcher(fixed_tenant: dict | None = None) -> Dispatcher:
    # FSM storage: per-tenant if possible, else global fallback
    if fixed_tenant and fixed_tenant.get("slug"):
        from overstreet.infra import tenant_fsm_path
        fsm_path = str(tenant_fsm_path(fixed_tenant["slug"]))
    else:
        from overstreet.config import FSM_DB_PATH
        fsm_path = str(FSM_DB_PATH)

    dp = Dispatcher(storage=SQLiteFSMStorage(fsm_path))

    tenant_mw = TenantMiddleware(fixed_tenant=fixed_tenant)
    dp.message.middleware(tenant_mw)
    dp.callback_query.middleware(tenant_mw)

    from overstreet.handlers import (
        admin, imovel, cliente, callbacks, commands, media, search, visitas, match, followups, proposta, proximidade, chaves, mercado, relatorio
    )

    dp.include_router(admin.router)
    dp.include_router(imovel.router)
    dp.include_router(cliente.router)
    dp.include_router(callbacks.router)
    dp.include_router(commands.router)
    dp.include_router(media.router)
    dp.include_router(search.router)
    dp.include_router(visitas.router)
    dp.include_router(match.router)
    dp.include_router(followups.router)
    dp.include_router(proposta.router)
    dp.include_router(proximidade.router)
    dp.include_router(chaves.router)
    dp.include_router(mercado.router)
    dp.include_router(relatorio.router)

    return dp


async def _run_bot(token: str, meta_conn, qdrant, embedder,
                   tenant: dict | None = None):
    """Start polling for one bot (global or per-tenant)."""
    label = tenant["nome"] if tenant else "global"
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher(fixed_tenant=tenant)

    logger.info("Bot '%s' iniciando polling...", label)
    try:
        await dp.start_polling(
            bot,
            conn=meta_conn,
            qdrant=qdrant,
            embedder=embedder,
            allowed_updates=dp.resolve_used_update_types(),
        )
    except Exception as e:
        logger.error("Bot '%s' encerrou com erro: %s", label, e)
    finally:
        await bot.session.close()
        logger.info("Bot '%s' encerrado.", label)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY nao definida")

    # Auto-migrate SQLite data to Postgres on first boot (Railway internal network)
    try:
        import asyncio as _asyncio
        from overstreet.db.postgres_migrate import run_migration
        migrated = await _asyncio.to_thread(run_migration)
        if migrated:
            logger.info("Dados migrados de SQLite para Postgres")
    except Exception as e:
        logger.warning("Postgres migration skipped/failed: %s", e)

    # Open global metadata DB (init_db já foi chamado dentro de get_meta_db)
    meta_conn = _get_meta_db()
    logger.info("Meta DB inicializado")

    await asyncio.to_thread(_warm_singletons)

    # ── Ensure all tenant DBs are loaded and stats logged ──────────────
    from overstreet.db.tenants import list_tenants
    from overstreet.infra import get_tenant_connections, get_tenant_stats
    all_tenants = list_tenants(meta_conn)
    for t in all_tenants:
        slug = t["slug"]
        try:
            conns = get_tenant_connections(slug)
            stats = get_tenant_stats(slug)
            logger.info("Tenant '%s' (id=%d): %d imóveis, %d disponíveis",
                slug, t["id"], stats["total_imoveis"], stats["disponiveis"])

            # Carregar dicionário canônico de grafias
            try:
                from overstreet.db.canonical import load_canonical
                imoveis_conn = conns.get("imoveis") or conns.get("meta")
                if imoveis_conn:
                    load_canonical(imoveis_conn)
            except Exception as e:
                logger.warning("Canonical load falhou para tenant '%s': %s", slug, e)
        except Exception as e:
            logger.warning("Erro ao carregar tenant '%s': %s", slug, e)

    # ── Ensure FTS5 on each tenant DB ──────────────────────────────────
    try:
        from overstreet.search.fts import ensure_fts
        for t in all_tenants:
            slug = t["slug"]
            from overstreet.infra import tenant_imoveis_path
            tp = tenant_imoveis_path(slug)
            if tp.exists():
                await asyncio.to_thread(ensure_fts, tp)
    except Exception as e:
        logger.warning("FTS5 setup falhou: %s", e)

    # ── Ensure Qdrant collections exist for all tenants ───────────────
    if _qdrant:
        try:
            existing = {c.name for c in _qdrant.get_collections().collections}
            for t in all_tenants:
                slug = t["slug"]
                if slug not in existing:
                    _qdrant.create_collection(
                        collection_name=slug,
                        vectors_config={"size": 384, "distance": "Cosine"},
                    )
                    logger.info("Qdrant collection criada: %s", slug)
        except Exception as e:
            logger.warning("Qdrant collection setup falhou: %s", e)

    tasks: list[asyncio.Task] = []

    # ── Per-tenant bots ─────────────────────────────────────────────
    from overstreet.db.tenants import get_tenants_with_bot_tokens
    tenant_bots = get_tenants_with_bot_tokens(meta_conn)
    for t in tenant_bots:
        token = (t.get("bot_token") or "").strip()
        if token:
            tasks.append(asyncio.create_task(
                _run_bot(token, meta_conn, _qdrant, _embedder, tenant=t),
                name=f"bot-tenant-{t['id']}",
            ))

    # ── Global bot (env var) ───────────────────────────────────────
    if BOT_TOKEN:
        # Em multi-tenant, o bot global precisa resolver o tenant por usuário
        # (TenantMiddleware resolve automaticamente via get_tenant_for_user)
        # Em single-tenant, podemos fixar direto pra eficiência
        fixed_tenant = all_tenants[0] if len(all_tenants) == 1 else None
        if fixed_tenant:
            logger.info(
                "Bot global: single-tenant mode → tenant '%s' (id=%d)",
                fixed_tenant["nome"], fixed_tenant["id"],
            )
        else:
            logger.info(
                "Bot global: multi-tenant mode (%d tenants) → resolução por usuário",
                len(all_tenants),
            )
        tasks.append(asyncio.create_task(
            _run_bot(BOT_TOKEN, meta_conn, _qdrant, _embedder, tenant=fixed_tenant),
            name="bot-global",
        ))

    if not tasks:
        raise RuntimeError(
            "Nenhum bot token configurado. "
            "Defina TELEGRAM_BOT_TOKEN ou configure tokens por tenant no dashboard."
        )

    logger.info(
        "%d bot(s) iniciados: %d tenant, %d global",
        len(tasks), len(tenant_bots), 1 if BOT_TOKEN else 0,
    )

    try:
        await asyncio.gather(*tasks)
    finally:
        meta_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
