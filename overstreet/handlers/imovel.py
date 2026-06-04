"""Handler FSM: cadastro de imóvel e upload de fotos."""
import re
import logging
import httpx
from aiogram import Router, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InputMediaPhoto,
)
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from overstreet.states import CadastroImovelStates
from overstreet.formatters.card import format_card

log = logging.getLogger("overstreet.handlers.imovel")
router = Router()

_EXTRACTION_PROMPT = """Extraia dados estruturados deste imóvel descrito em português.
Responda APENAS em JSON válido com estas chaves (omita as vazias).
IMPORTANTE: Preserve TODOS os números exatamente como informados — CEP, telefone, preços, áreas. NUNCA invente ou altere dígitos.

{{
  "tipo": "apartamento|casa|sobrado|cobertura|terreno|loja|sala comercial|galpao|kitnet|studio|flat",
  "street": "nome da rua",
  "number": "numero",
  "district": "bairro",
  "city": "cidade",
  "state": "SP",
  "zip": "cep completo com 8 digitos",
  "bedrooms": 0,
  "bathrooms": 0,
  "garage": 0,
  "suites": 0,
  "sale_price": "valor em reais sem formatacao",
  "rental_price": "valor em reais sem formatacao",
  "condo_fee": "condominio",
  "area_util": 0.0,
  "built_area": 0.0,
  "land_area": 0.0,
  "description": "descricao completa",
  "finalidade": "Venda|Locacao|Venda/Locacao",
  "owner_name": "nome do proprietario",
  "owner_mobile": "telefone celular completo com DDD",
  "owner_phone": "telefone fixo completo com DDD",
  "owner_email": "email do proprietario"
}}

Descrição do imóvel: {user_text}"""


# ── Geocoding ──

async def _geocode(data: dict) -> dict | None:
    """Geocode address using Nominatim (OpenStreetMap). Returns {lat, lon, display} or None."""
    street = str(data.get("street", "")).strip() if data.get("street") else ""
    number = str(data.get("number", "")).strip() if data.get("number") else ""
    district = str(data.get("district", "")).strip() if data.get("district") else ""
    city = str(data.get("city", "")).strip() if data.get("city") else ""
    state = str(data.get("state", "SP")).strip()

    # Try with number first, then without (OSM may not have exact number)
    queries = []
    addr_parts = [p for p in [f"{street} {number}".strip(), district, city, state, "Brasil"] if p]
    queries.append(", ".join(addr_parts))
    
    if number:
        addr_parts_no_num = [p for p in [street, district, city, state, "Brasil"] if p]
        queries.append(", ".join(addr_parts_no_num))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for query in queries:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "json", "limit": 1, "countrycodes": "br"},
                    headers={"User-Agent": "AnaImobBot/1.0"},
                )
                if resp.status_code == 200 and resp.json():
                    r = resp.json()[0]
                    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "display": r["display_name"]}
    except Exception as e:
        log.warning("Geocoding falhou: %s", e)
    return None


def _static_map_url(lat: float, lon: float, zoom: int = 16) -> str:
    """Generate OpenStreetMap static map image URL."""
    return (
        f"https://www.openstreetmap.org/export/embed.html"
        f"?bbox={lon-0.004},{lat-0.003},{lon+0.004},{lat+0.003}"
        f"&layer=mapnik&marker={lat},{lon}"
    )

def _is_cadastro_trigger(text: str | None) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    # Flexible patterns matching how people speak in audio transcriptions
    patterns = [
        r'cadast\w*\s+(um\s+)?im[oó]vel',           # "cadastrar/cadastro um imóvel"
        r'novo\s+im[oó]vel',                          # "novo imóvel"
        r'quero\s+cadastr\w*',                         # "quero cadastrar/cadastro"
        r'adicion\w*\s+(um\s+)?im[oó]vel',            # "adicionar um imóvel"
        r'registr\w*\s+(um\s+)?im[oó]vel',            # "registrar um imóvel"
        r'cadastr\w*\s+e\s+um\s+im[oó]vel',           # "cadastro e um imóvel" (spoken)
        r'im[oó]vel\s+(pra|para)\s+cadastr\w*',       # "imóvel pra cadastrar"
        r'inclu[íi]r\s+(um\s+)?im[oó]vel',            # "incluir um imóvel"
        r'lan[cç]\w*\s+(um\s+)?im[oó]vel',            # "lançar um imóvel"
    ]
    return any(re.search(p, text_lower) for p in patterns)


def _is_finalizar_fotos(text: str | None) -> bool:
    return bool(text) and text.lower().strip() in ("pronto", "finalizar", "ok", "fim", "feito", "encerrar")


# ── Handlers FSM ──

@router.message(F.text.func(lambda t: _is_cadastro_trigger(t)),
                ~StateFilter(CadastroImovelStates.aguardando_descricao,
                             CadastroImovelStates.corrigindo,
                             CadastroImovelStates.revisando_preview))
async def cmd_cadastrar_imovel(message: Message, state: FSMContext,
                               conn=None, tenant=None, tenant_conn=None, **kwargs):
    # Feature flag check
    if conn and tenant:
        from overstreet.db.tenants import get_bot_config
        if not get_bot_config(conn, tenant["id"]).get("enable_cadastro", 1):
            await message.answer("Cadastro de imóveis não disponível neste plano.")
            return

    # Se o texto já contém dados completos, extrair direto
    text = message.text or ""
    has_address = bool(re.search(r'rua|av\.|avenida|praça|travessa|alameda|estrada', text, re.I))
    has_details = bool(re.search(r'quarto|dormitório|dorm|suite|banheiro|vaga|preço|aluguel|locação|venda|metro|m²', text, re.I))
    if has_address and has_details:
        await state.set_state(CadastroImovelStates.aguardando_descricao)
        await handle_descricao_imovel(message, text, state, conn=conn, tenant=tenant, tenant_conn=tenant_conn)
        return

    # Dados incompletos → pedir descrição via Ana
    from overstreet.handlers.search import _process_text_message, _handle_action
    result = await _process_text_message(
        message, text, conn=conn, qdrant=None, embedder=None, tenant=tenant
    )
    # Set FSM state: either Ana triggered the action or we set it directly
    if result and result.get("action"):
        await _handle_action(result, state)
    else:
        await state.set_state(CadastroImovelStates.aguardando_descricao)
    await message.answer(
        "🏠 <b>Cadastro de Imóvel</b>\n\n"
        "Descreva o imóvel por texto ou áudio:\n"
        "Tipo, endereço, quartos, banheiros, vagas, área, preço, finalidade...\n\n"
        "<i>Ex: Apartamento 2 dormitórios, 1 vaga, Rua das Flores 123 Santana SP, "
        "área 65m², venda 350 mil.</i>",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroImovelStates.aguardando_descricao), F.text)
async def on_descricao_imovel(message: Message, state: FSMContext,
                               conn=None, tenant=None, tenant_conn=None, **kwargs):
    await handle_descricao_imovel(message, message.text, state, conn=conn, tenant=tenant, tenant_conn=tenant_conn)


async def handle_descricao_imovel(message: Message, text: str, state: FSMContext,
                                   conn=None, tenant: dict | None = None,
                                   tenant_conn=None):
    """Extrai dados do imóvel e mostra preview."""
    await message.answer("<i>Analisando descrição...</i>", parse_mode=ParseMode.HTML)

    from overstreet.ai.nim import nim_extract_json
    prompt = _EXTRACTION_PROMPT.format(user_text=text)
    data = await nim_extract_json(prompt)

    if not data:
        await message.answer(
            "Não consegui extrair os dados. Tente descrever com mais detalhes:\n"
            "<i>Ex: Casa 3 quartos, rua tal, bairro tal, cidade, preço</i>",
            parse_mode=ParseMode.HTML
        )
        return

    # Normalizar
    data.setdefault("situacao", "Disponivel")
    data.setdefault("city", "São Paulo")
    data.setdefault("state", "SP")
    data.setdefault("finalidade", "Venda")

    # ═══ Canonical resolve: grafia correta pt-BR ═══
    # O LLM pode gerar "jacana", "Jaçana", "JACANA" → tudo vira "Jaçanã"
    try:
        from overstreet.db.canonical import resolve
        imovel_db = tenant_conn or conn
        for col in ("city", "district"):
            raw_val = data.get(col, "")
            if raw_val:
                canonical_val = resolve(col, str(raw_val), conn=imovel_db)
                if canonical_val:
                    if str(raw_val) != canonical_val:
                        log.info("Canonical: '%s' → '%s'", raw_val, canonical_val)
                    data[col] = canonical_val
    except Exception as e:
        log.warning("Canonical resolve falhou: %s", e)

    # Geocoding para lat/lon e mapa
    geo = await _geocode(data)
    if geo:
        data["latitude"] = geo["lat"]
        data["longitude"] = geo["lon"]
        log.info("Geocoded: %s → %.6f, %.6f", data.get("street"), geo["lat"], geo["lon"])
    else:
        data["latitude"] = None
        data["longitude"] = None

    # Salvar dados no FSM state
    await state.update_data(imovel_data=data, descricao_original=text)
    await state.set_state(CadastroImovelStates.revisando_preview)

    # Mostrar preview
    preview = format_card(data)
    
    # Enviar card com mapa se geocoded
    if geo:
        lat, lon = geo["lat"], geo["lon"]
        # Mapa estático via OpenStreetMap (thumbnail para preview)
        map_url = f"https://staticmap.openstreetmap.de/staticmap.php?center={lat},{lon}&zoom=16&size=600x300&maptype=mapnik&markers={lat},{lon},red-pushpin"
        # Inline button para abrir no Google Maps
        gmaps_link = f"https://www.google.com/maps?q={lat},{lon}"
        
        try:
            # Tenta enviar foto do mapa + card
            async with httpx.AsyncClient(timeout=15) as client:
                map_resp = await client.get(map_url, headers={"User-Agent": "AnaImobBot/1.0"})
                if map_resp.status_code == 200 and len(map_resp.content) > 1000:
                    await message.answer_photo(
                        photo=map_resp.content,
                        caption=f"📍 <b>Localização do Imóvel</b>\n{geo['display'][:120]}\n\n<b>📋 Preview do Imóvel:</b>\n\n{preview}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [
                                InlineKeyboardButton(text="✅ Confirmar Cadastro", callback_data="imovel_cadastrar"),
                                InlineKeyboardButton(text="❌ Cancelar", callback_data="imovel_cancelar"),
                            ],
                            [InlineKeyboardButton(text="📍 Abrir no Maps", url=gmaps_link)],
                        ])
                    )
                    return
        except Exception as e:
            log.warning("Mapa estático falhou: %s", e)

    # Fallback: card sem mapa
    await message.answer(
        f"<b>📋 Preview do Imóvel:</b>\n\n{preview}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirmar Cadastro", callback_data="imovel_cadastrar"),
                InlineKeyboardButton(text="❌ Cancelar", callback_data="imovel_cancelar"),
            ],
        ])
    )


@router.callback_query(F.data == "imovel_cadastrar",
                        StateFilter(CadastroImovelStates.revisando_preview))
async def on_confirmar_cadastro(callback: CallbackQuery, state: FSMContext,
                                conn=None, tenant_conn=None, tenant=None, **kwargs):
    # Ack callback IMEDIATELY to avoid "query too old" error
    try:
        await callback.answer("Cadastrando...")
    except Exception:
        pass  # callback already expired, continue anyway

    data = await state.get_data()
    imovel_data = data.get("imovel_data", {})

    # tenant_conn = banco de imóveis do tenant; conn = meta.db (metadata global)
    db = tenant_conn or conn
    if not imovel_data or db is None:
        await state.clear()
        try:
            await callback.message.answer("❌ Erro: dados perdidos. Tente novamente.")
        except Exception:
            pass
        return

    from overstreet.db.imoveis import insert_imovel
    tenant_id = tenant["id"] if tenant else None
    log.info("Confirmar cadastro: tenant=%s tenant_conn=%s db_id=%s",
             tenant.get("slug") if tenant else None,
             "YES" if tenant_conn else "NO",
             id(db))
    new_id = insert_imovel(db, imovel_data, tenant_id=tenant_id)
    log.info("Insert retornou: COD %d", new_id)

    # Indexar no Qdrant em background
    import asyncio
    asyncio.create_task(_index_qdrant_bg(new_id, imovel_data, tenant.get("slug")))

    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass  # message may have changed

    # Buscar o imóvel recém-inserido pra mostrar o card completo
    from overstreet.db.imoveis import get_imovel_by_id
    row = get_imovel_by_id(db, new_id)
    if row:
        row["id"] = new_id
        confirmation_card = format_card(row)
        await callback.message.answer(
            f"✅ <b>Imóvel cadastrado com sucesso!</b>\n\n{confirmation_card}\n\n"
            f"Para adicionar fotos, envie as fotos e informe o código <b>{new_id}</b>.",
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.message.answer(
            f"✅ <b>Imóvel cadastrado com sucesso!</b>\n"
            f"Código: <b>COD {new_id}</b>\n\n"
            f"Para adicionar fotos, envie as fotos e informe o código <b>{new_id}</b>.",
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "imovel_corrigir",
                        StateFilter(CadastroImovelStates.revisando_preview))
async def on_corrigir_cadastro(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.set_state(CadastroImovelStates.corrigindo)
    await callback.answer()
    await callback.message.answer(
        "O que precisa corrigir? Descreva a correção:\n"
        "<i>Ex: o bairro é Tucuruvi, não Santana. O preço é 280 mil.</i>",
        parse_mode=ParseMode.HTML
    )


@router.message(StateFilter(CadastroImovelStates.corrigindo), F.text)
async def on_correcao_imovel(message: Message, state: FSMContext,
                              conn=None, tenant=None, tenant_conn=None, **kwargs):
    data = await state.get_data()
    original = data.get("descricao_original", "")
    correcao = message.text

    # Re-extrair com a correção
    texto_completo = f"{original}\n\nCorreção: {correcao}"
    await state.update_data(descricao_original=texto_completo)
    await state.set_state(CadastroImovelStates.aguardando_descricao)
    await handle_descricao_imovel(message, texto_completo, state, conn=conn, tenant=tenant, tenant_conn=tenant_conn)


@router.callback_query(F.data == "imovel_cancelar")
async def on_cancelar_cadastro(callback: CallbackQuery, state: FSMContext, **kwargs):
    await state.clear()
    await callback.answer("Cadastro cancelado.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Cadastro cancelado.")


# ── Upload de fotos ──

@router.message(StateFilter(CadastroImovelStates.aguardando_codigo_foto), F.text)
async def on_codigo_foto(message: Message, state: FSMContext,
                          conn=None, tenant_conn=None, tenant=None, **kwargs):
    """Corretor informou o código do imóvel para cadastrar fotos."""
    text = message.text.strip()
    try:
        imovel_id = int(text)
    except ValueError:
        await message.answer("Por favor, informe apenas o número do código. Ex: <code>270034</code>",
                             parse_mode=ParseMode.HTML)
        return

    # tenant_conn = banco de imóveis do tenant; conn = meta.db
    db = tenant_conn or conn
    if db is None:
        await message.answer("Erro de conexão.")
        await state.clear()
        return

    row = db.execute("SELECT id FROM imoveis WHERE id = ?", (imovel_id,)).fetchone()
    if not row:
        await message.answer(f"Imóvel COD {imovel_id} não encontrado.")
        await state.clear()
        return

    # Cadastrar fotos pendentes (enviadas antes do código)
    data = await state.get_data()
    pending = data.get("pending_photos", [])
    tenant_id = tenant["id"] if tenant else None

    from overstreet.db.imoveis import add_foto, count_fotos
    for i, file_id in enumerate(pending):
        ordem = count_fotos(db, imovel_id) + i
        add_foto(db, imovel_id, file_id, tenant_id=tenant_id, ordem=ordem)

    await state.set_state(CadastroImovelStates.recebendo_fotos)
    await state.update_data(foto_imovel_id=imovel_id, pending_photos=[])

    n = len(pending)
    msg = f"📸 Imóvel COD <b>{imovel_id}</b> selecionado!"
    if n:
        msg += f" {n} foto{'s' if n > 1 else ''} cadastrada{'s' if n > 1 else ''}."
    msg += "\nEnvie mais fotos ou diga <b>pronto</b> para encerrar."
    await message.answer(msg, parse_mode=ParseMode.HTML)


@router.message(StateFilter(CadastroImovelStates.recebendo_fotos), F.text)
async def on_finalizar_fotos(message: Message, state: FSMContext,
                             conn=None, tenant_conn=None, **kwargs):
    if _is_finalizar_fotos(message.text):
        data = await state.get_data()
        imovel_id = data.get("foto_imovel_id")
        db = tenant_conn or conn
        from overstreet.db.imoveis import count_fotos
        n = count_fotos(db, imovel_id) if db and imovel_id else 0
        await state.clear()
        await message.answer(
            f"✅ Fotos salvas! Imóvel <b>COD {imovel_id}</b> agora tem <b>{n} foto(s)</b>.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "Continue enviando fotos ou diga <b>pronto</b> para encerrar.",
            parse_mode=ParseMode.HTML
        )


async def _index_qdrant_bg(imovel_id: int, data: dict, tenant_slug: str | None = None):
    """Indexa novo imóvel no Qdrant em background."""
    try:
        from overstreet.config import COLLECTION_NAME
        from qdrant_client.models import PointStruct
        # Importar singletons
        from overstreet.main import _qdrant, _embedder
        if _qdrant is None or _embedder is None:
            return
        collection = tenant_slug or COLLECTION_NAME
        text = " ".join(str(v) for v in data.values() if v)
        vector = list(_embedder.embed([text]))[0].tolist()
        payload = {k: v for k, v in data.items() if k not in ("full_text",)}
        payload["id"] = imovel_id
        _qdrant.upsert(
            collection_name=collection,
            points=[PointStruct(id=imovel_id, vector=vector, payload=payload)]
        )
        log.info("Qdrant indexed: COD %d → collection '%s'", imovel_id, collection)
    except Exception as e:
        log.warning("Qdrant index falhou para COD %d: %s", imovel_id, e)
