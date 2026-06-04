"""Dispatcher builder — FSM storage, middleware, routers, plugins."""
from __future__ import annotations
import logging
from aiogram import Dispatcher

from overstreet.fsm_storage import SQLiteFSMStorage
from overstreet.middleware.tenant import TenantMiddleware
from overstreet.bot.registry import (
    get_registered_routers,
    get_registered_middlewares,
)

logger = logging.getLogger(__name__)


def _load_builtin_routers() -> list:
    """Carrega routers built-in. Import lazy pra evitar ciclos."""
    from overstreet.handlers import (
        commands, callbacks, search, imovel, cliente, media,
        visitas, followups, match, chaves, proposta, proximidade,
        relatorio, mercado, admin,
    )
    return [
        commands.router, callbacks.router, search.router,
        imovel.router, cliente.router, media.router,
        visitas.router, followups.router, match.router,
        chaves.router, proposta.router, proximidade.router,
        relatorio.router, mercado.router, admin.router,
    ]


def build_dispatcher() -> Dispatcher:
    """Cria Dispatcher com FSM storage, middleware, routers built-in e plugins."""
    from overstreet.infra import fsm_path
    fsm_storage = SQLiteFSMStorage(fsm_path())
    dp = Dispatcher(storage=fsm_storage)

    # Middleware built-in (no-op, injeta tenant_conn)
    dp.message.middleware(TenantMiddleware())
    dp.callback_query.middleware(TenantMiddleware())

    # Routers built-in
    for r in _load_builtin_routers():
        dp.include_router(r)

    # Plugins/middleware registrados via overstreet.bot.register()
    for mw in get_registered_middlewares():
        dp.message.middleware(mw)
        dp.callback_query.middleware(mw)
        logger.info("Middleware plugin aplicado")

    for r in get_registered_routers():
        dp.include_router(r)
        logger.info("Router plugin incluido")

    return dp
