"""Handler /match — busca imóveis compatíveis com perfil de cliente."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from overstreet.db.clientes import list_clientes, resolve_alias, get_cliente_by_id
from overstreet.db.match import match_imoveis_to_cliente, get_recent_imoveis

router = Router()


@router.message(Command("match"))
async def cmd_match(message: Message, conn=None, tenant=None, tenant_conn=None, **kwargs):
    """Busca imóveis compatíveis com o perfil de um cliente.

    /match         → mostra lista de clientes como botões
    /match João    → busca matches para o cliente João
    """
    db = tenant_conn or conn
    if not db:
        await message.answer("❌ Não consegui acessar os dados.")
        return

    args = message.text.removeprefix("/match").strip()

    if not args:
        # Mostrar clientes como botões para seleção
        clientes = list_clientes(db)
        if not clientes:
            await message.answer("Nenhum cliente cadastrado ainda.")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=c["nome"], callback_data=f"match_{c['id']}")]
            for c in clientes[:15]
        ])
        await message.answer("Selecione um cliente:", reply_markup=kb)
        return

    # Buscar matches para o cliente mencionado
    await _show_matches(message, db, args)


@router.callback_query(F.data.startswith("match_"))
async def on_match_callback(callback: CallbackQuery, conn=None, tenant=None,
                             tenant_conn=None, **kwargs):
    """Callback quando o corretor seleciona um cliente no menu /match."""
    db = tenant_conn or conn
    if not db:
        await callback.answer("❌ Erro interno", show_alert=True)
        return

    cliente_id = int(callback.data.removeprefix("match_"))
    cliente = get_cliente_by_id(db, cliente_id)
    if not cliente:
        await callback.answer("❌ Cliente não encontrado", show_alert=True)
        return

    await callback.answer()
    await _show_matches_from_cliente(callback.message, db, cliente)


async def _show_matches(message: Message, db, query: str):
    """Resolve cliente por nome/alias e mostra matches."""
    cliente = resolve_alias(db, query)
    if not cliente:
        await message.answer(f"❌ Cliente '{query}' não encontrado.")
        return
    await _show_matches_from_cliente(message, db, cliente)


async def _show_matches_from_cliente(target, db, cliente: dict):
    """Mostra imóveis compatíveis com o perfil de um cliente."""
    matches = match_imoveis_to_cliente(db, cliente, limit=5)
    if not matches:
        await target.answer(
            f"Nenhum imóvel compatível com o perfil de '{cliente['nome']}' no momento."
        )
        return
    lines = [f"🎯 <b>Imóveis compatíveis com {cliente['nome']}:</b>\n"]
    for m in matches:
        price = m.get("sale_price") or m.get("rental_price") or "?"
        lines.append(
            f"• COD {m['id']} · {m.get('district','?')} · "
            f"{m.get('bedrooms','?')} dorm · R$ {price}"
        )
    await target.answer("\n".join(lines))


@router.message(Command("recentes"))
async def cmd_recentes(message: Message, conn=None, tenant=None, tenant_conn=None, **kwargs):
    """Mostra imóveis cadastrados recentemente (últimos 7 dias)."""
    db = tenant_conn or conn
    if not db:
        await message.answer("❌ Não consegui acessar os dados.")
        return

    recent = get_recent_imoveis(db, days=7, limit=10)
    if not recent:
        await message.answer("Nenhum imóvel cadastrado nos últimos 7 dias.")
        return
    lines = ["🆕 <b>Imóveis recentes (7 dias):</b>\n"]
    for m in recent:
        price = m.get("sale_price") or m.get("rental_price") or "?"
        lines.append(
            f"• COD {m['id']} · {m.get('district','?')} · R$ {price}"
        )
    await message.answer("\n".join(lines))
