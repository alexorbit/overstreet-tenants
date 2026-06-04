"""Handlers de busca: Ana é o cérebro central, não um fallback."""
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from overstreet.formatters.card import parse_codigo
from overstreet.formatters.messages import send_card, send_fichas, set_user_location
from overstreet.db.imoveis import _query_dict, _query_dicts

router = Router()


@router.message(F.location)
async def on_location(message: Message, tenant_conn=None, **kwargs):
    loc = message.location
    set_user_location(message.from_user.id, loc.latitude, loc.longitude)

    # Buscar imóveis próximos automaticamente
    from overstreet.handlers.proximidade import _busca_por_coordenadas, _busca_por_bairro, _format_resumo, DEFAULT_RADIUS_KM

    if tenant_conn:
        resultados = _busca_por_coordenadas(tenant_conn, loc.latitude, loc.longitude, DEFAULT_RADIUS_KM, 5)
        if not resultados:
            resultados = _busca_por_bairro(tenant_conn, loc.latitude, loc.longitude, 5)
        if resultados:
            lines = [f"📍 <b>Imóveis próximos (raio {DEFAULT_RADIUS_KM:.0f}km):</b>", ""]
            for r in resultados:
                dist = r.get("distancia_km")
                lines.append(_format_resumo(r, dist))
            lines.append("──────────")
            lines.append(f"<i>Mostrando {len(resultados)} imóveis mais próximos</i>")
            await message.answer("\n".join(lines), parse_mode="HTML")
        else:
            await message.answer(
                "📍 Localização salva!\n"
                "😔 Nenhum imóvel encontrado num raio de 5km.",
                parse_mode="HTML"
            )
    else:
        await message.answer(
            "📍 Localização salva! Agora posso calcular rotas.\n"
            "Diga: <code>imóveis próximos</code> para buscar perto de você.",
            parse_mode="HTML"
        )


@router.message(F.text)
async def on_text(message: Message, state: FSMContext,
 conn=None, tenant_conn=None, qdrant=None, embedder=None, tenant=None, **kwargs):
    text = message.text.strip()

    # ── Keyboard shortcuts ───────────────────────────────────────────────
    if text in ("🔍 Buscar", "Buscar"):
        await message.answer(
            "Me diz o que procura:\n"
            "• Código: <code>270034</code>\n"
            "• Descrição: <code>apto 2 quartos Santana</code>\n"
            "• Ou manda áudio!",
            parse_mode=ParseMode.HTML
        )
        return
    if text in ("🎤 Áudio", "Áudio", "Audio"):
        await message.answer("Grave um áudio e envie! Fale: tipo, bairro, quartos, preço...")
        return
    if text in ("📋 Ajuda", "Ajuda"):
        from overstreet.handlers.commands import cmd_ajuda
        await cmd_ajuda(message, tenant=tenant)
        return
    if text in ("🏡 Aptos", "Aptos"):
        text = "mostre apartamentos disponíveis"
    elif text in ("🏠 Casas", "Casas"):
        text = "mostre casas disponíveis"

    action = await _process_text_message(
        message, text, conn=conn, tenant_conn=tenant_conn, qdrant=qdrant, embedder=embedder, tenant=tenant
    )
    await _handle_action(action, state)


async def _process_text_message(
    message: Message,
    text: str,
    conn=None,
    tenant_conn=None,
    qdrant=None,
    embedder=None,
    tenant: dict | None = None,
    from_audio: bool = False,
) -> dict | None:
    user_id = message.from_user.id
    user_name = message.from_user.first_name or ""
    db = tenant_conn or conn # prefer tenant DB for imovel queries

    # ── Client alias shortcut ────────────────────────────────────────────
    if tenant and conn is not None:
        from overstreet.db.clientes import resolve_alias
        cliente = resolve_alias(conn, tenant["id"], text.strip())
        if cliente:
            await _show_client_profile(message, cliente, db, conn, tenant)
            return None

    text_lower = text.lower()

    # ── Client commands ──────────────────────────────────────────────────
    m = re.match(r'salvar\s+im[oó]vel\s+(\d+)\s+para\s+(.+)', text_lower)
    if m and tenant and conn:
        await _handle_salvar_imovel(message, int(m.group(1)), m.group(2).strip(), db, conn, tenant)
        return None

    m = re.match(r'enviar?\s+im[oó]vel\s+(\d+)\s+para\s+(.+)', text_lower)
    if m and tenant and conn:
        await _handle_enviar_imovel(message, int(m.group(1)), m.group(2).strip(), db, conn, tenant)
        return None

    m = re.match(r'(?:list[ae]?\s+)?im[oó]veis\s+d[ao]\s+(.+)', text_lower)
    if m and tenant and conn:
        await _handle_listar_favoritos(message, m.group(1).strip(), db, conn, tenant)
        return None

    # ── Fast path: exact numeric code ───────────────────────────────────
    codigo = parse_codigo(text)
    if codigo and db is not None:
        row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (codigo,))
        if row:
            await send_card(message, row, user_id, conn=conn)
            return None
        # Code not found — let Ana handle it conversationally
        text = f"o imóvel {codigo} existe no banco?"

    # ── Ana (NIM) — central intelligence ────────────────────────────────
    await message.bot.send_chat_action(message.chat.id, "typing")

    from overstreet.ai.nim import ask_ana
    ana = await ask_ana(
        conn, qdrant, embedder, text,
        user_id=user_id, tenant=tenant,
        user_name=user_name, tenant_conn=tenant_conn,
    )

    if ana["text"]:
        await message.answer(ana["text"], parse_mode=ParseMode.HTML)

    for imovel in ana["imoveis"]:
        await send_card(message, imovel, user_id, conn=conn)

    return ana # caller checks ana["action"]


async def _handle_action(result: dict | None, state: FSMContext):
    """Trigger FSM states from Ana's iniciar_acao tool calls."""
    if not result or not result.get("action"):
        return
    action = result["action"]
    if action == "cadastrar_imovel":
        from overstreet.states import CadastroImovelStates
        await state.set_state(CadastroImovelStates.aguardando_descricao)
    elif action == "cadastrar_cliente":
        from overstreet.states import CadastroClienteStates
        await state.set_state(CadastroClienteStates.aguardando_descricao)
    elif action == "mostrar_ajuda":
        pass # message already sent by Ana


# ── Client helpers ────────────────────────────────────────────────────────

async def _show_client_profile(message: Message, cliente: dict, db, conn, tenant: dict):
    from overstreet.db.clientes import get_aliases, get_favorites
    aliases = get_aliases(conn, cliente["id"])
    favs = get_favorites(conn, cliente["id"], tenant["id"])
    text = (
        f"<b>👤 {cliente['nome']}</b>\n"
        f"{'📧 ' + cliente['email'] if cliente.get('email') else ''}\n"
        f"{'📱 ' + cliente['whatsapp'] if cliente.get('whatsapp') else ''}\n"
        f"{'<i>' + cliente['perfil_descricao'] + '</i>' if cliente.get('perfil_descricao') else ''}\n\n"
        f"Apelidos: {', '.join(aliases) if aliases else 'nenhum'}\n"
        f"Imóveis favoritos: {len(favs)}"
    )
    await message.answer(text.strip(), parse_mode=ParseMode.HTML)
    if favs:
        for fav_id in favs[:5]:
            row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (fav_id,))
            if row:
                await send_card(message, row, message.from_user.id, conn=conn)


async def _handle_salvar_imovel(message: Message, imovel_id: int, alias: str,
                                db, conn, tenant: dict):
    from overstreet.db.clientes import resolve_alias, add_favorite
    cliente = resolve_alias(conn, tenant["id"], alias)
    if not cliente:
        await message.answer(f"Cliente <b>{alias}</b> não encontrado. Cadastre primeiro.",
                             parse_mode=ParseMode.HTML)
        return
    row = _query_dict(db, "SELECT id FROM imoveis WHERE id=?", (imovel_id,))
    if not row:
        await message.answer(f"Imóvel COD {imovel_id} não encontrado.")
        return
    ok = add_favorite(conn, cliente["id"], imovel_id, tenant["id"])
    if ok:
        await message.answer(
            f"✅ COD {imovel_id} salvo nos favoritos de <b>{cliente['nome']}</b>!",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(f"COD {imovel_id} já está nos favoritos de {cliente['nome']}.")


async def _handle_listar_favoritos(message: Message, alias: str, db, conn, tenant: dict):
    from overstreet.db.clientes import resolve_alias, get_favorites
    cliente = resolve_alias(conn, tenant["id"], alias)
    if not cliente:
        await message.answer(f"Cliente <b>{alias}</b> não encontrado.", parse_mode=ParseMode.HTML)
        return
    favs = get_favorites(conn, cliente["id"], tenant["id"])
    if not favs:
        await message.answer(f"<b>{cliente['nome']}</b> não tem imóveis salvos ainda.",
                             parse_mode=ParseMode.HTML)
        return
    await message.answer(f"<b>🏡 Imóveis de {cliente['nome']} ({len(favs)}):</b>",
                         parse_mode=ParseMode.HTML)
    for fav_id in favs:
        row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (fav_id,))
        if row:
            await send_card(message, row, message.from_user.id, conn=conn)


async def _handle_enviar_imovel(message: Message, imovel_id: int, alias: str,
                                db, conn, tenant: dict):
    from overstreet.db.clientes import resolve_alias
    from overstreet.formatters.card import build_client_send_keyboard
    cliente = resolve_alias(conn, tenant["id"], alias)
    if not cliente:
        await message.answer(f"Cliente <b>{alias}</b> não encontrado.", parse_mode=ParseMode.HTML)
        return
    row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (imovel_id,))
    if not row:
        await message.answer(f"Imóvel COD {imovel_id} não encontrado.")
        return
    imovel = row
    agente_nome = tenant.get("agente_nome", "Ana")
    agente_tel = tenant.get("agente_telefone", "")
    keyboard = build_client_send_keyboard(imovel, cliente, agente_nome, agente_tel)
    await message.answer(
        f"📤 Enviar <b>COD {imovel_id}</b> para <b>{cliente['nome']}</b>:\n\n"
        "Clique abaixo para enviar via WhatsApp.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    if cliente.get("email"):
        from overstreet.ai.email import send_email, build_imovel_email_html
        html = build_imovel_email_html(imovel, agente_nome, agente_tel)
        ok = await send_email(
            cliente["email"],
            f"{agente_nome} separou um imóvel especialmente para você!",
            html
        )
        if ok:
            await message.answer(f"✅ Email enviado para <b>{cliente['email']}</b>",
                                  parse_mode=ParseMode.HTML)
