"""Handlers de mídia: voz, áudio, fotos."""
from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from overstreet.ai.whisper import transcribe_audio
from overstreet.states import CadastroImovelStates
from overstreet.states import CadastroClienteStates

router = Router()


@router.message(F.voice)
async def on_voice(message: Message, bot: Bot, state: FSMContext,
                   conn=None, tenant_conn=None, qdrant=None, embedder=None, tenant=None, **kwargs):
    if conn and tenant:
        from overstreet.db.tenants import get_bot_config
        if not get_bot_config(conn, tenant["id"]).get("enable_audio", 1):
            await message.answer("Recurso de áudio não disponível neste plano.")
            return
    text = await transcribe_audio(bot, message)
    if not text:
        await message.answer("Não consegui entender o áudio. Tente novamente.")
        return

    # Se estiver no fluxo de cadastro de imóvel, encaminhar para lá
    current_state = await state.get_state()
    if current_state in (
        CadastroImovelStates.aguardando_descricao.state,
        CadastroImovelStates.corrigindo.state,
    ):
        from overstreet.handlers.imovel import handle_descricao_imovel
        await handle_descricao_imovel(message, text, state, conn=conn, tenant=tenant)
        return

    # Se estiver no fluxo de cadastro de cliente, encaminhar para lá
    if current_state == CadastroClienteStates.aguardando_descricao.state:
        from overstreet.handlers.cliente import _handle_descricao_cliente
        await _handle_descricao_cliente(message, text, state, conn=conn, tenant=tenant)
        return

    # Se for trigger de cadastro com dados completos (endereço + quartos/preço),
    # pular o passo intermediário e ir direto pra extração
    from overstreet.handlers.imovel import _is_cadastro_trigger
    if _is_cadastro_trigger(text):
        import re
        has_address = bool(re.search(r'rua|av\.|avenida|praça|travessa|alameda|estrada|rudo|cedros', text, re.I))
        has_details = bool(re.search(r'quarto|dormitório|suite|banheiro|vaga|preço|aluguel|locação|venda|metro|m²|dorm', text, re.I))
        if has_address and has_details:
            # Dados completos no áudio → extrair direto
            from overstreet.handlers.imovel import handle_descricao_imovel
            await state.set_state(CadastroImovelStates.aguardando_descricao)
            await handle_descricao_imovel(message, text, state, conn=conn, tenant=tenant)
        else:
            # Trigger sem dados → iniciar FSM e pedir descrição
            from overstreet.handlers.imovel import cmd_cadastrar_imovel
            message.text = text
            await cmd_cadastrar_imovel(message, state, conn=conn, tenant=tenant)
        return

    # Se for trigger de cadastro de CLIENTE
    from overstreet.handlers.cliente import _is_cadastro_cliente_trigger
    if _is_cadastro_cliente_trigger(text):
        import re
        has_name = bool(re.search(r'[A-ZÀ-Ú][a-zà-ú]+\s+[A-ZÀ-Ú]', text))
        has_contact = bool(re.search(r'\d{2}[\s.\-]?\d{4,5}[\s.\-]?\d{4}|@', text))
        if has_name and has_contact:
            # Dados completos no áudio → extrair direto
            from overstreet.handlers.cliente import _handle_descricao_cliente
            await state.set_state(CadastroClienteStates.aguardando_descricao)
            await _handle_descricao_cliente(message, text, state, conn=conn, tenant=tenant)
        else:
            # Trigger sem dados → iniciar FSM e pedir descrição
            from overstreet.handlers.cliente import cmd_cadastrar_cliente
            message.text = text
            await cmd_cadastrar_cliente(message, state, conn=conn, tenant=tenant)
        return

    from overstreet.handlers.search import _process_text_message, _handle_action
    result = await _process_text_message(message, text, conn=conn, tenant_conn=tenant_conn,
                                          qdrant=qdrant, embedder=embedder, tenant=tenant, from_audio=True)
    await _handle_action(result, state)


@router.message(F.audio)
async def on_audio_file(message: Message, bot: Bot, state: FSMContext,
                        conn=None, tenant_conn=None, qdrant=None, embedder=None, tenant=None, **kwargs):
    if conn and tenant:
        from overstreet.db.tenants import get_bot_config
        if not get_bot_config(conn, tenant["id"]).get("enable_audio", 1):
            await message.answer("Recurso de áudio não disponível neste plano.")
            return
    text = await transcribe_audio(bot, message)
    if not text:
        await message.answer("Não consegui transcrever o arquivo de áudio.")
        return
    from overstreet.handlers.search import _process_text_message, _handle_action
    result = await _process_text_message(message, text, conn=conn, tenant_conn=tenant_conn,
                                          qdrant=qdrant, embedder=embedder, tenant=tenant, from_audio=True)
    await _handle_action(result, state)


@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext, conn=None, tenant=None, **kwargs):
    """Recebe fotos. Se no estado recebendo_fotos, adiciona ao imóvel."""
    current_state = await state.get_state()

    if current_state == CadastroImovelStates.recebendo_fotos.state:
        data = await state.get_data()
        imovel_id = data.get("foto_imovel_id")
        if imovel_id and conn is not None:
            # Pegar maior qualidade da foto
            file_id = message.photo[-1].file_id
            tenant_id = tenant["id"] if tenant else None
            from overstreet.db.imoveis import add_foto, count_fotos
            ordem = count_fotos(conn, imovel_id)
            add_foto(conn, imovel_id, file_id, tenant_id=tenant_id, ordem=ordem)
            await message.answer(
                f"📸 Foto adicionada ao imóvel COD {imovel_id}! "
                f"Envie mais ou diga <b>pronto</b>.",
                parse_mode=ParseMode.HTML
            )
        return

    if current_state == CadastroImovelStates.aguardando_codigo_foto.state:
        # Salvou a foto antes de informar o código — guardar temporariamente
        data = await state.get_data()
        pending = data.get("pending_photos", [])
        pending.append(message.photo[-1].file_id)
        await state.update_data(pending_photos=pending)
        await message.answer(
            "📸 Foto recebida! Para qual imóvel devo cadastrar? Informe o código:"
        )
        return

    # Nenhum estado ativo: perguntar o código
    file_id = message.photo[-1].file_id
    await state.set_state(CadastroImovelStates.aguardando_codigo_foto)
    await state.update_data(pending_photos=[file_id])
    await message.answer(
        "📸 Foto recebida! Para qual imóvel devo cadastrar?\n"
        "Informe o <b>código do imóvel</b>:",
        parse_mode=ParseMode.HTML
    )
