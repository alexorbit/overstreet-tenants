"""Plugin registry — extensions podem registrar routers/middleware sem editar bot/.

Uso:
    from overstreet.bot import register
    from aiogram import Router
    router = Router()

    @router.message(Command("mycmd"))
    async def mycmd(...): ...

    register(router=router, middleware=None, name="my_feature")
"""
from __future__ import annotations
import logging
from typing import List, Optional
from aiogram import BaseMiddleware
from aiogram import Router

logger = logging.getLogger(__name__)

# Lista global de extensões registradas
_registered: List[dict] = []


def register(
    router: Optional[Router] = None,
    middleware: Optional[BaseMiddleware] = None,
    name: str = "unnamed",
) -> None:
    """Registra uma feature externa.

    Args:
        router: aiogram Router (opcional)
        middleware: aiogram BaseMiddleware (opcional)
        name: identificador único da feature (para debug/log)
    """
    if router is None and middleware is None:
        raise ValueError("register() precisa de router ou middleware")
    if any(r["name"] == name for r in _registered):
        logger.warning("Feature '%s' já registrada, ignorando duplicata", name)
        return
    _registered.append({"name": name, "router": router, "middleware": middleware})
    logger.info("Feature registrada: %s", name)


def get_registered_routers() -> List[Router]:
    return [r["router"] for r in _registered if r["router"] is not None]


def get_registered_middlewares() -> List[BaseMiddleware]:
    return [r["middleware"] for r in _registered if r["middleware"] is not None]


def reset() -> None:
    """Limpa registro (apenas para testes)."""
    _registered.clear()
