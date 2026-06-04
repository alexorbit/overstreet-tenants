"""Envio de cards e fichas para o Telegram."""
import logging
from aiogram import types, Bot
from aiogram.types import InputMediaPhoto
from aiogram.enums import ParseMode
from overstreet.formatters.card import format_card, build_card_keyboard

log = logging.getLogger("overstreet.formatters.messages")

# Localização por user_id (cache em memória)
_user_locations: dict[int, tuple[float, float]] = {}


def set_user_location(user_id: int, lat: float, lon: float):
    _user_locations[user_id] = (lat, lon)


def get_user_location(user_id: int) -> tuple[float, float] | None:
    return _user_locations.get(user_id)


async def send_card(message: types.Message, row: dict, user_id: int,
                    score: float | None = None, conn=None):
    """Envia card de imóvel. Se houver fotos, envia carrossel antes."""
    # Verificar fotos
    if conn is not None:
        try:
            fotos_rows = conn.execute(
                "SELECT file_id FROM imovel_fotos WHERE imovel_id = ? ORDER BY ordem",
                (row.get("id"),)
            ).fetchall()
            file_ids = [r[0] for r in fotos_rows]
        except Exception:
            file_ids = []

        if file_ids:
            await _send_carousel(message.bot, message.chat.id, file_ids)

    card_text = format_card(row, score)
    keyboard = build_card_keyboard(row, user_id, _user_locations)
    await message.answer(card_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _send_carousel(bot: Bot, chat_id: int, file_ids: list[str]):
    """Envia album de fotos (max 10)."""
    media = [InputMediaPhoto(media=fid) for fid in file_ids[:10]]
    try:
        await bot.send_media_group(chat_id, media)
    except Exception as e:
        log.warning(f"Carrossel falhou: {e}")


async def send_fichas(message: types.Message, results: list[dict],
                      intro_msg: str = "", conn=None):
    """Envia múltiplos cards."""
    if intro_msg:
        await message.answer(intro_msg, parse_mode=ParseMode.HTML)
    user_id = message.from_user.id
    for r in results:
        score = r.pop("_score", None)
        await send_card(message, r, user_id, score=score, conn=conn)
