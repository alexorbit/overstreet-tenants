"""Bot module — modular entrypoint for the Overstreet single-tenant bot.

Public API:
- run()           : asyncio entrypoint (bot + dashboard em paralelo)
- main()          : alias para run() (compat)
- register(...)   : hook para plugins/features externas adicionarem routers/middleware
"""
from .entrypoint import main, run
from .registry import register, get_registered_routers, get_registered_middlewares
from .warm import warm_singletons, get_qdrant, get_embedder

__all__ = [
    "main", "run",
    "register", "get_registered_routers", "get_registered_middlewares",
    "warm_singletons", "get_qdrant", "get_embedder",
]
