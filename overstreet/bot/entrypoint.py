"""Entrypoint do bot — orquestra startup, runners, shutdown."""
from __future__ import annotations
import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress

from overstreet.bot.runners import run_bot, run_dashboard
from overstreet.bot.warm import (
    warm_singletons, ensure_qdrant_collection,
    ensure_fts_sync, load_canonical_sync,
)

logger = logging.getLogger(__name__)


REQUIRED_ENV = ("BOT_TOKEN", "NVIDIA_API_KEY", "GROQ_API_KEY", "DASHBOARD_PASSWORD")


def _validate_env() -> None:
    missing = [v for v in REQUIRED_ENV if not os.getenv(v)]
    if missing:
        for v in missing:
            logger.fatal("%s nao definida", v)
        sys.exit(2)


def _init_sqlite() -> None:
    from overstreet.infra import (
        get_imoveis_db, get_memory_db, get_fsm_db, get_shared_db,
    )
    get_imoveis_db()
    get_memory_db()
    get_fsm_db()
    get_shared_db()
    logger.info("SQLite OK")


async def _startup() -> None:
    """Sequência de boot (ordem importa)."""
    _validate_env()
    logger.info("Inicializando SQLite...")
    _init_sqlite()

    logger.info("Carregando Qdrant + embedder...")
    await asyncio.to_thread(warm_singletons)
    await ensure_qdrant_collection()

    logger.info("Garantindo FTS5 + dicionário canônico...")
    await asyncio.to_thread(ensure_fts_sync)
    await asyncio.to_thread(load_canonical_sync)


async def main() -> int:
    """Entry principal: startup → bot + dashboard em paralelo."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await _startup()
    logger.info("Subindo bot + dashboard...")
    await asyncio.gather(run_bot(), run_dashboard())
    return 0


# Alias para `python -m overstreet.bot`
run = main


def _sigterm_handler(*_):
    logger.info("SIGTERM recebido, encerrando...")
    sys.exit(0)


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        signal.signal(signal.SIGTERM, _sigterm_handler)
        sys.exit(asyncio.run(main()))
