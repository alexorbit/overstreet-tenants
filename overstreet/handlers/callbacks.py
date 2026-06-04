"""Callbacks inline: email, rota, quiz, filtros, salvar favorito."""
import re
import sqlite3
from urllib.parse import quote
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from overstreet.config import TIPO_MAP
from overstreet.formatters.card import build_address_query, format_card_shareable
from overstreet.formatters.messages import send_fichas, _user_locations
from overstreet.db.imoveis import _query_dict, _query_dicts
from overstreet.db.clientes import list_clientes, add_favorite, get_cliente_by_id

router = Router()


@router.callback_query(F.data.startswith("email_"))
async def on_email_callback(callback: CallbackQuery, conn=None, **kwargs):
    imovel_id = int(callback.data.removeprefix("email_"))
    if conn is None:
        await callback.answer("Erro interno", show_alert=True)
        return
    row = conn.execute(
        "SELECT owner_name, owner_email FROM imoveis WHERE id = ?", (imovel_id,)
    ).fetchone()
    if not row or not row[1]:
        await callback.answer("Email não disponível", show_alert=True)
        return
    email = row[1].strip()
    nome = row[0] or "Proprietário"
    await callback.answer()
    await callback.message.answer(
        f"<b>{nome}</b>\n<a href=\"mailto:{email}\">{email}</a>",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("route_"))
async def on_route_callback(callback: CallbackQuery, conn=None, **kwargs):
    imovel_id = int(callback.data.removeprefix("route_"))
    user_id = callback.from_user.id
    if conn is None:
        await callback.answer("Erro interno", show_alert=True)
        return
    row = _query_dict(conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
    if not row:
        await callback.answer("Imóvel não encontrado", show_alert=True)
        return
    addr = build_address_query(dict(row))
    encoded = quote(addr)
    user_loc = _user_locations.get(user_id)
    if user_loc:
        lat, lon = user_loc
        url = f"https://www.google.com/maps/dir/?api=1&origin={lat},{lon}&destination={encoded}"
    else:
        url = f"https://www.google.com/maps/dir/?api=1&destination={encoded}"
    await callback.answer()
    await callback.message.answer(
        f"<a href=\"{url}\">Abrir rota no Google Maps</a>",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("quiz_"))
async def on_quiz_callback(callback: CallbackQuery, **kwargs):
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer()
        return
    quiz_type = parts[1]

    if quiz_type == "bairro":
        await callback.message.answer("Qual bairro ou região? Ex: Santana, Mooca, Centro...")
    elif quiz_type == "tipo":
        await callback.message.answer(
            "Qual tipo de imóvel?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Apartamento", callback_data="filter_tipo_72"),
                    InlineKeyboardButton(text="Casa", callback_data="filter_tipo_68"),
                ],
                [
                    InlineKeyboardButton(text="Sobrado", callback_data="filter_tipo_47"),
                    InlineKeyboardButton(text="Cobertura", callback_data="filter_tipo_83"),
                ],
                [
                    InlineKeyboardButton(text="Terreno", callback_data="filter_tipo_66"),
                    InlineKeyboardButton(text="Ponto Com.", callback_data="filter_tipo_64"),
                ],
                [
                    InlineKeyboardButton(text="Flat", callback_data="filter_tipo_69"),
                    InlineKeyboardButton(text="Loja", callback_data="filter_tipo_76"),
                ],
            ])
        )
    elif quiz_type == "preco":
        await callback.message.answer(
            "Qual faixa de preço?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Até 300k", callback_data="filter_preco_300"),
                    InlineKeyboardButton(text="300k - 500k", callback_data="filter_preco_500"),
                ],
                [
                    InlineKeyboardButton(text="500k - 1M", callback_data="filter_preco_1000"),
                    InlineKeyboardButton(text="1M+", callback_data="filter_preco_9999"),
                ],
                [
                    InlineKeyboardButton(text="Aluguel até 2k", callback_data="filter_aluguel_2000"),
                    InlineKeyboardButton(text="Aluguel 2k-5k", callback_data="filter_aluguel_5000"),
                ],
            ])
        )
    elif quiz_type == "quartos":
        await callback.message.answer(
            "Quantos dormitórios?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="1", callback_data="filter_dorm_1"),
                    InlineKeyboardButton(text="2", callback_data="filter_dorm_2"),
                ],
                [
                    InlineKeyboardButton(text="3", callback_data="filter_dorm_3"),
                    InlineKeyboardButton(text="4+", callback_data="filter_dorm_4"),
                ],
            ])
        )
    await callback.answer()


@router.callback_query(F.data.startswith("filter_"))
async def on_filter_callback(callback: CallbackQuery, conn=None, **kwargs):
    parts = callback.data.split("_")
    if len(parts) < 3 or conn is None:
        await callback.answer()
        return
    filter_type = parts[1]
    filter_val = parts[2]

    if filter_type == "tipo":
        rows = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE property_type = ? AND situacao = 'Disponivel' ORDER BY RANDOM() LIMIT 5",
            (filter_val,)
        )
        label = TIPO_MAP.get(int(filter_val), filter_val)
        if rows:
            await send_fichas(callback.message, [dict(r) for r in rows],
                              f"<b>{label}s disponíveis:</b>", conn=conn)
        else:
            await callback.message.answer(f"Nenhum {label} disponível no momento.")

    elif filter_type == "preco":
        limit_val = int(filter_val) * 1000
        rows = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE situacao = 'Disponivel' AND sale_price != '' "
            "AND sale_price IS NOT NULL ORDER BY id DESC LIMIT 30"
        )
        filtered = []
        for r in rows:
            rd = dict(r)
            num_str = re.sub(r'[^\d]', '', str(rd.get("sale_price", "")))
            if num_str and int(num_str) <= limit_val:
                filtered.append(rd)
            if len(filtered) >= 5:
                break
        if filtered:
            await send_fichas(callback.message, filtered,
                              f"<b>Imóveis até R$ {filter_val}k:</b>", conn=conn)
        else:
            await callback.message.answer(f"Nada encontrado até R$ {filter_val}k.")

    elif filter_type == "aluguel":
        limit_val = int(filter_val)
        rows = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE situacao = 'Disponivel' AND rental_price != '' "
            "AND rental_price IS NOT NULL ORDER BY id DESC LIMIT 30"
        )
        filtered = []
        for r in rows:
            rd = dict(r)
            num_str = re.sub(r'[^\d]', '', str(rd.get("rental_price", "")))
            if num_str and int(num_str) <= limit_val:
                filtered.append(rd)
            if len(filtered) >= 5:
                break
        if filtered:
            await send_fichas(callback.message, filtered,
                              f"<b>Aluguéis até R$ {limit_val}:</b>", conn=conn)
        else:
            await callback.message.answer(f"Nada encontrado até R$ {limit_val} de aluguel.")

    elif filter_type == "dorm":
        min_dorm = int(filter_val)
        rows = _query_dicts(conn,
            "SELECT * FROM imoveis WHERE bedrooms >= ? AND situacao = 'Disponivel' "
            "ORDER BY RANDOM() LIMIT 5",
            (min_dorm,)
        )
        if rows:
            await send_fichas(callback.message, [dict(r) for r in rows],
                              f"<b>{min_dorm}+ dormitórios:</b>", conn=conn)
        else:
            await callback.message.answer(f"Nada com {min_dorm}+ dormitórios.")

    elif filter_type == "imovel_confirm":
        # Confirmação de cadastro de imóvel (callback_data: imovel_confirm_{imovel_id})
        imovel_id = int(parts[2]) if len(parts) > 2 else 0
        await callback.answer(f"✅ Imóvel COD {imovel_id} cadastrado!")
        await callback.message.edit_reply_markup(reply_markup=None)

    await callback.answer()


@router.callback_query(F.data.startswith("saveto_"))
async def on_saveto_callback(callback: CallbackQuery, conn=None, tenant=None,
                            tenant_conn=None, **kwargs):
    """Salva imóvel como favorito para o cliente selecionado."""
    parts = callback.data.split("_", 2)
    if len(parts) < 3:
        await callback.answer("Erro: dados inválidos", show_alert=True)
        return
    imovel_id = int(parts[1])
    cliente_id = int(parts[2])
    db = tenant_conn or conn
    tenant_id = tenant.get("id") if tenant else None
    if not db or not tenant_id:
        await callback.answer("Erro interno", show_alert=True)
        return
    cliente = get_cliente_by_id(db, cliente_id)
    if not cliente:
        await callback.answer("Cliente não encontrado", show_alert=True)
        return
    ok = add_favorite(db, cliente_id, imovel_id, tenant_id)
    if ok:
        await callback.answer(
            f"✅ Imóvel COD {imovel_id} salvo para {cliente['nome']}",
            show_alert=True,
        )
    else:
        await callback.answer(
            f"⚠️ Imóvel COD {imovel_id} já está nos favoritos de {cliente['nome']}",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("save_"))
async def on_save_callback(callback: CallbackQuery, conn=None, tenant=None,
                           tenant_conn=None, **kwargs):
    """Mostra lista de clientes para salvar imóvel como favorito."""
    imovel_id = callback.data.removeprefix("save_")
    db = tenant_conn or conn
    tenant_id = tenant.get("id") if tenant else None
    if not db or not tenant_id:
        await callback.answer("Erro interno", show_alert=True)
        return
    clientes = list_clientes(db, tenant_id)
    if not clientes:
        await callback.answer(
            "Nenhum cliente cadastrado. Cadastre um cliente primeiro!",
            show_alert=True,
        )
        return
    buttons = []
    row = []
    for c in clientes:
        row.append(InlineKeyboardButton(
            text=c["nome"],
            callback_data=f"saveto_{imovel_id}_{c['id']}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    await callback.answer()
    await callback.message.answer(
        "📂 Salvar imóvel para qual cliente?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("proposta_"))
async def on_proposta_callback(callback: CallbackQuery, tenant_conn=None, **kwargs):
    """Gera proposta padronizada para o imóvel via callback."""
    imovel_id = callback.data.removeprefix("proposta_")
    db = tenant_conn or callback
    if not db or not hasattr(db, "execute"):
        await callback.answer("Erro interno", show_alert=True)
        return
    from overstreet.db.imoveis import _query_dict
    from overstreet.handlers.proposta import gerar_proposta
    row = _query_dict(db, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
    if not row:
        await callback.answer("Imóvel não encontrado", show_alert=True)
        return
    texto = gerar_proposta(dict(row))
    await callback.answer("✅ Proposta gerada!", show_alert=True)
    await callback.message.answer(texto, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("share_"))
async def on_share_callback(callback: CallbackQuery, conn=None, tenant=None,
                            tenant_conn=None, **kwargs):
    """Envia mensagem formatada sem botões para o corretor encaminhar."""
    imovel_id = callback.data.removeprefix("share_")
    db = tenant_conn or conn
    if not db:
        await callback.answer("Erro interno", show_alert=True)
        return
    row = _query_dict(db, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
    if not row:
        await callback.answer("Imóvel não encontrado", show_alert=True)
        return
    await callback.answer("✅ Mensagem pronta para encaminhar!", show_alert=True)
    share_text = format_card_shareable(dict(row))
    await callback.message.answer(share_text, parse_mode=ParseMode.HTML)
