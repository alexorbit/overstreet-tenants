"""Runners — bot polling + dashboard uvicorn (asyncio.gather)."""
from __future__ import annotations
import logging
import os
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from overstreet.bot.dispatcher import build_dispatcher
from overstreet.bot.warm import get_qdrant, get_embedder

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    """Sobe o bot Telegram (polling)."""
    from overstreet.config import BOT_TOKEN
    from overstreet.infra import get_imoveis_db

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()
    logger.info("Bot iniciando polling...")
    try:
        await dp.start_polling(
            bot,
            conn=get_imoveis_db(),
            qdrant=get_qdrant(),
            embedder=get_embedder(),
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("Bot encerrado.")


async def run_dashboard() -> None:
    """Sobe o dashboard FastAPI (uvicorn programático)."""
    import uvicorn
    from dashboard.server import app
    config = uvicorn.Config(
        app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
        log_level="info", access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info("Dashboard subindo em :%d", config.port)
    await server.serve()
