"""Handler FSM: cadastro de tenant (imobiliária/corretor) — admin only."""
import re
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from overstreet.states import CadastroTenantStates
from overstreet.config import ADMIN_IDS

router = Router()


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _is_cadastro_tenant_trigger(text: str) -> bool:
    patterns = [
        r'cadastrar?\s+imobili[aá]ria',
        r'cadastrar?\s+corretor',
        r'novo\s+tenant',
        r'criar\s+imobili[aá]ria',
        r'criar\s+corretor',
    ]
    if not text:
        return False
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


@router.message(F.text.func(lambda t: _is_cadastro_tenant_trigger(t)))
async def cmd_cadastrar_tenant(message: Message, state: FSMContext, **kwargs):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Acesso negado. Apenas administradores podem cadastrar tenants.")
        return

    text_lower = message.text.lower()
    tipo = "corretor" if "corretor" in text_lower else "imobiliaria"
    await state.update_data(tenant_tipo=tipo)
    await state.set_state(CadastroTenantStates.aguardando_nome)

    tipo_label = "Corretor Solo" if tipo == "corretor" else "Imobiliária"
    await message.answer(
        f"🏢 <b>Cadastro de {tipo_label}</b>\n\n"
        f"Qual é o nome da {tipo_label.lower()}?\n"
        f"<i>Ex: Zana Imóveis, João Corretor</i>",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroTenantStates.aguardando_nome), F.text)
async def on_tenant_nome(message: Message, state: FSMContext, **kwargs):
    nome = message.text.strip()
    if len(nome) < 2:
        await message.answer("Nome muito curto. Tente novamente:")
        return

    await state.update_data(tenant_nome=nome)
    await state.set_state(CadastroTenantStates.aguardando_agente_nome)
    await message.answer(
        f"Como se chama a assistente/agente de <b>{nome}</b>?\n"
        f"<i>Ex: Ana, Zana, Sofia</i>",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroTenantStates.aguardando_agente_nome), F.text)
async def on_tenant_agente_nome(message: Message, state: FSMContext, **kwargs):
    agente_nome = message.text.strip()
    await state.update_data(tenant_agente_nome=agente_nome)
    await state.set_state(CadastroTenantStates.aguardando_agente_tel)
    await message.answer(
        f"Qual o WhatsApp de contato da <b>{agente_nome}</b>? (com DDD)\n"
        f"<i>Ex: 11999999999</i>\n\n"
        f"Ou diga <b>pular</b> para deixar em branco.",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroTenantStates.aguardando_agente_tel), F.text)
async def on_tenant_agente_tel(message: Message, state: FSMContext, **kwargs):
    text = message.text.strip()
    tel = "" if text.lower() in ("pular", "skip") else re.sub(r'\D', '', text)
    await state.update_data(tenant_agente_tel=tel)
    await state.set_state(CadastroTenantStates.aguardando_ids)
    await message.answer(
        "Informe os IDs Telegram dos usuários autorizados (corretores/admins).\n"
        "Separe por vírgula se forem vários.\n\n"
        "<i>Para descobrir seu ID, envie qualquer mensagem para @userinfobot no Telegram.</i>\n"
        f"<i>Seu ID atual: <code>{message.from_user.id}</code></i>",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroTenantStates.aguardando_ids), F.text)
async def on_tenant_ids(message: Message, state: FSMContext, **kwargs):
    text = message.text.strip()
    ids_raw = re.split(r'[,\s]+', text)
    ids = [int(x) for x in ids_raw if x.isdigit()]

    if not ids:
        await message.answer(
            "Não reconheci nenhum ID válido. Informe números separados por vírgula:\n"
            f"<i>Ex: {message.from_user.id}, 987654321</i>",
            parse_mode=ParseMode.HTML
        )
        return

    await state.update_data(tenant_user_ids=ids)
    await state.set_state(CadastroTenantStates.confirmando)

    data = await state.get_data()
    tipo_label = "Corretor Solo" if data.get("tenant_tipo") == "corretor" else "Imobiliária"
    ids_str = ", ".join(str(i) for i in ids)

    await message.answer(
        f"<b>📋 Confirmar cadastro:</b>\n\n"
        f"Tipo: <b>{tipo_label}</b>\n"
        f"Nome: <b>{data.get('tenant_nome')}</b>\n"
        f"Agente: <b>{data.get('tenant_agente_nome')}</b>\n"
        f"WhatsApp: <b>{data.get('tenant_agente_tel') or 'não informado'}</b>\n"
        f"IDs autorizados: <code>{ids_str}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirmar", callback_data="tenant_confirmar"),
                InlineKeyboardButton(text="❌ Cancelar", callback_data="tenant_cancelar"),
            ],
        ])
    )


@router.callback_query(F.data == "tenant_confirmar",
                        StateFilter(CadastroTenantStates.confirmando))
async def on_confirmar_tenant(callback: CallbackQuery, state: FSMContext,
                               conn=None, **kwargs):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Acesso negado.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()

    if conn is None:
        await callback.answer("Erro de conexão.", show_alert=True)
        await state.clear()
        return

    from overstreet.db.tenants import create_tenant, add_user_to_tenant
    tenant_id = create_tenant(
        conn,
        nome=data.get("tenant_nome"),
        agente_nome=data.get("tenant_agente_nome"),
        agente_telefone=data.get("tenant_agente_tel", ""),
        admin_telegram_id=callback.from_user.id,
    )

    user_ids = data.get("tenant_user_ids", [])
    for uid in user_ids:
        add_user_to_tenant(conn, tenant_id, uid)

    await state.clear()
    await callback.answer("✅ Tenant cadastrado!")
    await callback.message.edit_reply_markup(reply_markup=None)

    ids_str = ", ".join(str(i) for i in user_ids)
    await callback.message.answer(
        f"✅ <b>{data.get('tenant_nome')}</b> cadastrado com sucesso!\n"
        f"ID do tenant: <b>{tenant_id}</b>\n"
        f"Usuários autorizados: <code>{ids_str}</code>\n\n"
        f"Os corretores já podem usar o bot com a identidade <b>{data.get('tenant_agente_nome')}</b>.",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "tenant_cancelar")
async def on_cancelar_tenant(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await callback.answer("Cancelado.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Cadastro de tenant cancelado.")


@router.message(F.text.func(lambda t: bool(t and re.match(r'^listar?\s+tenants?$', t.strip(), re.I))))
async def cmd_listar_tenants(message: Message, conn=None, **kwargs):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Acesso negado.")
        return
    if conn is None:
        await message.answer("Erro de conexão.")
        return

    from overstreet.db.tenants import list_tenants
    tenants = list_tenants(conn)
    if not tenants:
        await message.answer("Nenhum tenant cadastrado ainda.")
        return

    lines = ["<b>🏢 Tenants cadastrados:</b>\n"]
    for t in tenants:
        lines.append(
            f"• <b>ID {t['id']}</b>: {t['nome']} "
            f"(agente: {t.get('agente_nome', '-')})"
        )
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
