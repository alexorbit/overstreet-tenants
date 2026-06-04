"""Handler FSM: cadastro de cliente, favoritos, aliases."""
import re
from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from overstreet.states import CadastroClienteStates

router = Router()

_CLIENT_EXTRACTION_PROMPT = """Extraia dados estruturados deste cliente descrito em português.
Responda APENAS em JSON válido com estas chaves (omita as vazias):
{{
  "nome": "nome completo",
  "email": "email@example.com",
  "whatsapp": "numero sem formatacao",
  "perfil_descricao": "descricao do perfil e preferencias do cliente"
}}

Descrição do cliente: {user_text}"""


def _is_cadastro_cliente_trigger(text: str | None) -> bool:
    if not text:
        return False
    patterns = [
        r'cadastrar?\s+cliente', r'novo\s+cliente', r'adicionar?\s+cliente',
        r'registrar?\s+cliente', r'cadastro\s+de\s+cliente',
        r'incluir\s+cliente',
    ]
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


@router.message(F.text.func(lambda t: _is_cadastro_cliente_trigger(t)))
async def cmd_cadastrar_cliente(message: Message, state: FSMContext,
                                conn=None, tenant=None, tenant_conn=None, **kwargs):
    db = tenant_conn or conn
    if db and tenant:
        from overstreet.db.tenants import get_bot_config
        if not get_bot_config(db, tenant["id"]).get("enable_clientes", 1):
            await message.answer("Cadastro de clientes não disponível neste plano.")
            return

    text = message.text.strip()

    # ── Fast path: if user already provided data inline, extract and save ──
    # e.g. "cadastre cliente: Alex Freire, email x, whatsapp y, perfil z"
    inline_data = await _try_extract_inline(text)
    if inline_data and inline_data.get("nome"):
        await _handle_descricao_cliente(message, text, state, conn=db, tenant=tenant)
        return

    # ── Otherwise ask for details ──────────────────────────────────────────
    await state.set_state(CadastroClienteStates.aguardando_descricao)
    await message.answer(
        "👤 <b>Cadastro de Cliente</b>\n\n"
        "Descreva o cliente:\n"
        "Nome, email, WhatsApp, perfil de busca...\n\n"
        "<i>Ex: João Silva, jsilva@email.com, WhatsApp 11999999999, "
        "busca apartamento 2 quartos em Santana até 400 mil.</i>",
        parse_mode=ParseMode.HTML
    )


async def _try_extract_inline(text: str) -> dict | None:
    """Check if text already has enough client info inline (post-colon part)."""
    # Extract everything after "cadastre cliente:" or similar
    m = re.match(
        r'(?:cadastrar?|novo|adicionar?|registrar?)\s+cliente\s*[:\-]\s*(.+)',
        text, re.IGNORECASE
    )
    if not m:
        return None
    detail = m.group(1).strip()
    if len(detail) < 5:
        return None
    # Use NIM to extract structured data
    from overstreet.ai.nim import nim_extract_json
    prompt = _CLIENT_EXTRACTION_PROMPT.format(user_text=detail)
    return await nim_extract_json(prompt)


@router.message(StateFilter(CadastroClienteStates.aguardando_descricao), F.text)
async def on_descricao_cliente(message: Message, state: FSMContext,
                               conn=None, tenant=None, tenant_conn=None, **kwargs):
    db = tenant_conn or conn
    await _handle_descricao_cliente(message, message.text, state, conn=db, tenant=tenant)


async def _handle_descricao_cliente(message: Message, text: str, state: FSMContext,
                                     conn=None, tenant: dict | None = None):
    await message.answer("<i>Analisando dados do cliente...</i>", parse_mode=ParseMode.HTML)

    from overstreet.ai.nim import nim_extract_json
    prompt = _CLIENT_EXTRACTION_PROMPT.format(user_text=text)
    data = await nim_extract_json(prompt)

    if not data or not data.get("nome"):
        await message.answer(
            "Não consegui extrair os dados. Tente descrever com mais detalhes:\n"
            "<i>Ex: Ana Costa, ana@gmail.com, 11988887777, busca casa 3 quartos</i>",
            parse_mode=ParseMode.HTML
        )
        return

    await state.update_data(cliente_data=data, descricao_original=text)
    await state.set_state(CadastroClienteStates.revisando_preview)

    preview = _format_cliente_preview(data)
    await message.answer(
        f"<b>📋 Preview do Cliente:</b>\n\n{preview}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Cadastrar", callback_data="cliente_cadastrar"),
                InlineKeyboardButton(text="✏️ Corrigir", callback_data="cliente_corrigir"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancelar", callback_data="cliente_cancelar"),
            ],
        ])
    )


def _format_cliente_preview(data: dict) -> str:
    lines = [f"<b>👤 {data.get('nome', 'Sem nome')}</b>"]
    if data.get("email"):
        lines.append(f"📧 {data['email']}")
    if data.get("whatsapp"):
        lines.append(f"📱 {data['whatsapp']}")
    if data.get("perfil_descricao"):
        lines.append(f"\n<i>{data['perfil_descricao']}</i>")
    return "\n".join(lines)


@router.callback_query(F.data == "cliente_cadastrar",
                       StateFilter(CadastroClienteStates.revisando_preview))
async def on_confirmar_cliente(callback: CallbackQuery, state: FSMContext,
                               conn=None, tenant=None, tenant_conn=None, **kwargs):
    db = tenant_conn or conn
    data = await state.get_data()
    cliente_data = data.get("cliente_data", {})

    if not cliente_data or db is None:
        await callback.answer("Erro: dados perdidos. Tente novamente.", show_alert=True)
        await state.clear()
        return

    from overstreet.db.clientes import insert_cliente
    new_id = insert_cliente(db, cliente_data)

    await state.update_data(cliente_id=new_id)
    await state.set_state(CadastroClienteStates.adicionando_alias)

    await callback.answer("✅ Cliente cadastrado!")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ <b>Cliente cadastrado!</b> ID: <b>{new_id}</b>\n\n"
        f"Quer adicionar um apelido para facilitar buscas?\n"
        f"<i>Ex: japa, cantareira, joao_sp</i>\n\n"
        f"Digite o apelido ou <b>pular</b> para continuar.",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroClienteStates.adicionando_alias), F.text)
async def on_alias_cliente(message: Message, state: FSMContext,
                           conn=None, tenant=None, tenant_conn=None, **kwargs):
    text = message.text.strip().lower()
    db = tenant_conn or conn

    if text in ("pular", "não", "nao", "skip", "ok", "pronto"):
        data = await state.get_data()
        cliente_id = data.get("cliente_id")
        await state.clear()
        await message.answer(
            f"✅ Pronto! Cliente cadastrado. Use <code>imóveis do cliente</code> para ver favoritos.",
            parse_mode=ParseMode.HTML
        )
        return

    data = await state.get_data()
    cliente_id = data.get("cliente_id")

    if db is None or not cliente_id:
        await state.clear()
        return

    from overstreet.db.clientes import add_alias
    ok = add_alias(db, cliente_id, text)

    if ok:
        await message.answer(
            f"✅ Apelido <b>{text}</b> adicionado!\n"
            f"Adicione mais apelidos ou diga <b>pronto</b> para finalizar.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            f"⚠️ Apelido <b>{text}</b> já existe. Tente outro ou diga <b>pronto</b>.",
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "cliente_corrigir",
                        StateFilter(CadastroClienteStates.revisando_preview))
async def on_corrigir_cliente(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(CadastroClienteStates.aguardando_descricao)
    await callback.answer()
    await callback.message.answer(
        "O que precisa corrigir? Descreva novamente os dados do cliente:",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "cliente_cancelar")
async def on_cancelar_cliente(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await callback.answer("Cadastro cancelado.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Cadastro de cliente cancelado.")
