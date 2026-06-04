"""No-op middleware (single-tenant mode). Pode ser removido futuramente.

Em modo single-tenant, sempre injetamos o mesmo contexto (tenant_conn =
conexão com imoveis.db, shared_memory = shared_db). Handlers não precisam
mais resolver tenant — `tenant` é apenas um dict marcador com slug/id
fictícios.
"""
import logging
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Any, Callable, Awaitable

log = logging.getLogger("overstreet.middleware.tenant")


class TenantMiddleware(BaseMiddleware):
    """No-op: single-tenant, sempre injeta o mesmo contexto."""

    def __init__(self, fixed_tenant: dict | None = None):
        # Mantido por compatibilidade com a assinatura antiga; ignorado.
        pass

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Injeta imoveis conn e shared memory diretamente
        try:
            from overstreet.infra import get_imoveis_db, get_shared_db
            data["tenant_conn"] = get_imoveis_db()
            data["shared_memory"] = get_shared_db()
            data["tenant"] = {"slug": "default", "id": 1, "nome": "default"}
        except Exception as e:
            log.warning("Tenant context injection failed: %s", e)
            data["tenant_conn"] = None
            data["shared_memory"] = None
            data["tenant"] = None
        return await handler(event, data)
