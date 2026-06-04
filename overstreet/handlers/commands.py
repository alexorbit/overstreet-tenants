"""Handlers de comandos: /start, /ajuda, /bairro, /cidade, /cep, /quartos, /stats."""
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram import F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode

router = Router()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Localização", request_location=True)],
            [KeyboardButton(text="🔍 Buscar"), KeyboardButton(text="🎯 Filtros"), KeyboardButton(text="🎤 Áudio")],
            [KeyboardButton(text="🏡 Aptos"), KeyboardButton(text="🏠 Casas")],
            [KeyboardButton(text="📋 Ajuda")],
        ],
        resize_keyboard=True,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, conn=None, tenant=None, **kwargs):
    agente = tenant.get("agente_nome", "Ana") if tenant else "Ana"
    nome_imob = tenant["nome"] if tenant else "OverStreet"

    welcome = ""
    if conn and tenant:
        from overstreet.db.tenants import get_bot_config
        cfg = get_bot_config(conn, tenant["id"])
        welcome = cfg.get("welcome_message", "")

    if welcome:
        await message.answer(welcome, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())
    else:
        await message.answer(
            f"Olá! Eu sou a <b>{agente}</b> da <b>{nome_imob}</b>, sua companheira corretora! 🏡\n\n"
            "Posso buscar imóveis, cadastrar novos imóveis, gerenciar seus clientes e muito mais.\n\n"
            "Use /ajuda para ver tudo que posso fazer!\n\n"
            "<i>Bora trabalhar!</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard()
        )


@router.message(Command("ajuda"))
@router.message(Command("help"))
async def cmd_ajuda(message: Message, tenant=None, **kwargs):
    agente = tenant.get("agente_nome", "Ana") if tenant else "Ana"
    nome_imob = tenant["nome"] if tenant else "OverStreet"
    text = (
        f"<b>Olá! Eu sou a {agente} da {nome_imob}, sua companheira corretora! 🏡</b>\n\n"
        "Aqui está tudo que posso fazer por você:\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🔍 BUSCAR IMÓVEIS</b>\n"
        "• Código: <code>270034</code>\n"
        "• Texto: <code>apto 2 quartos Santana</code>\n"
        "• Áudio: grave e envie!\n"
        "• GPS: envie sua localização\n\n"
        "<b>🏠 CADASTRAR IMÓVEL</b>\n"
        "• <code>cadastrar imóvel</code> ou <code>novo imóvel</code>\n"
        "• Descreva por áudio ou texto\n"
        "• Veja o preview e confirme\n\n"
        "<b>📸 FOTOS DO IMÓVEL</b>\n"
        "• Envie fotos diretamente\n"
        "• Vou perguntar o código do imóvel\n"
        "• Cards com fotos mostram carrossel 📷\n\n"
        "<b>👥 CLIENTES</b>\n"
        "• <code>novo cliente João Silva, WhatsApp 11999999999</code>\n"
        "• <code>salvar imóvel 270034 para João</code>\n"
        "• <code>imóveis do João</code> — ver favoritos\n"
        "• <code>enviar imóvel 270034 para João</code> — envia WhatsApp + email\n\n"
        "<b>🏢 ADMIN</b>\n"
        "• <code>cadastrar imobiliária</code> — criar tenant (admin)\n"
        "• <code>cadastrar corretor</code> — corretor solo (admin)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📌 COMANDOS</b>\n"
        "/bairro Santana — busca por bairro\n"
        "/cidade São Paulo — busca por cidade\n"
        "/cep 02615 — busca por CEP\n"
        "/quartos 3 — mínimo de dormitórios\n"
        "/match João — imóveis compatíveis com o cliente\n"
        "/novidades — imóveis novos dos últimos 7 dias\n"
        "/stats — estatísticas do portfólio\n"
        "/stats apartamentos — stats por tipo\n"
        "/stats Santana — stats por bairro\n"
        "/ajuda — esta mensagem\n\n"
        "<i>Estou sempre aprendendo com você! 🤖</i>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


@router.message(Command("bairro"))
async def cmd_bairro(message: Message, conn=None, qdrant=None, embedder=None,
                     tenant=None, **kwargs):
    args = message.text.removeprefix("/bairro").strip()
    if not args:
        await message.answer("Manda o bairro! Ex: /bairro Santana")
        return
    from overstreet.handlers.search import _process_text_message
    await _process_text_message(message, f"imóveis no bairro {args}",
                                conn=conn, qdrant=qdrant, embedder=embedder, tenant=tenant)


@router.message(Command("cidade"))
async def cmd_cidade(message: Message, conn=None, qdrant=None, embedder=None,
                     tenant=None, **kwargs):
    args = message.text.removeprefix("/cidade").strip()
    if not args:
        await message.answer("Manda a cidade! Ex: /cidade Guarulhos")
        return
    from overstreet.handlers.search import _process_text_message
    await _process_text_message(message, f"imóveis na cidade {args}",
                                conn=conn, qdrant=qdrant, embedder=embedder, tenant=tenant)


@router.message(Command("cep"))
async def cmd_cep(message: Message, conn=None, **kwargs):
    args = message.text.removeprefix("/cep").strip()
    if not args:
        await message.answer("Manda o CEP! Ex: /cep 02615")
        return
    from overstreet.search.hybrid import busca_por_filtros
    from overstreet.formatters.messages import send_fichas
    results = busca_por_filtros(conn, cep=args)
    if results:
        await send_fichas(message, results,
                          f"<b>Imóveis com CEP {args}:</b>", conn=conn)
    else:
        await message.answer(f"Nenhum imóvel com CEP {args}.")


@router.message(Command("quartos"))
async def cmd_quartos(message: Message, conn=None, **kwargs):
    args = message.text.removeprefix("/quartos").strip()
    try:
        n = int(args)
    except ValueError:
        await message.answer("Manda o número! Ex: /quartos 3")
        return
    from overstreet.search.hybrid import busca_por_filtros
    from overstreet.formatters.messages import send_fichas
    results = busca_por_filtros(conn, quartos=n)
    if results:
        await send_fichas(message, results,
                          f"<b>Imóveis com {n}+ dormitórios:</b>", conn=conn)
    else:
        await message.answer(f"Nenhum imóvel com {n}+ dormitórios.")


@router.message(Command("stats"))
async def cmd_stats(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Estatísticas rápidas do portfólio. Suporta filtro: /stats apartamentos, /stats Santana."""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados do portfólio.")
        return

    from overstreet.config import TIPO_GROUPS, TIPO_MAP

    # ── Parse filtro ──
    args = message.text.removeprefix("/stats").strip().lower()
    where = ""
    filter_label = ""
    params: list = []

    if args:
        # Tentar mapear como tipo de imóvel
        tipo_ids = TIPO_GROUPS.get(args)
        if tipo_ids:
            placeholders = ",".join(["?"] * len(tipo_ids))
            where = f"WHERE tipo_imovel_id IN ({placeholders})"
            params = tipo_ids
            # Descobrir nome amigável
            tipo_name = TIPO_MAP.get(int(tipo_ids[0]), args.title())
            filter_label = f" — {tipo_name}s"
        else:
            # Tratar como bairro (LIKE)
            where = "WHERE district LIKE ?"
            params = [f"%{args}%"]
            filter_label = f" — {args.title()}"

    # ── Queries (COUNT / AVG com índices — rápidos) ──
    wh = where  # base WHERE clause

    # 1) Total
    row = tenant_conn.execute(f"SELECT COUNT(*) FROM imoveis {wh}", params).fetchone()
    total = row[0] if row else 0
    if total == 0:
        await message.answer(f"📊 Portfólio{filter_label}: nenhum imóvel encontrado.")
        return

    lines: list[str] = []
    lines.append(f"<b>📊 Estatísticas do Portfólio{filter_label}</b>")
    lines.append(f"━━━━━━━━━━━━━━━━━━")
    lines.append(f"📦 Total de imóveis: <b>{total:,}</b>".replace(",", "."))
    lines.append("")

    # 2) Por tipo (top 5)
    tipos = tenant_conn.execute(
        f"SELECT tipo_imovel_id, COUNT(*) as cnt FROM imoveis {wh} "
        f"GROUP BY tipo_imovel_id ORDER BY cnt DESC LIMIT 5", params
    ).fetchall()
    if tipos:
        lines.append("<b>🏢 Por tipo:</b>")
        for tid, cnt in tipos:
            name = TIPO_MAP.get(tid, f"Tipo {tid}")
            pct = cnt * 100 // total
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            lines.append(f"  {name}: {cnt:,} ({pct}%) {bar}".replace(",", "."))
        lines.append("")

    # 3) Por finalidade (venda x locação)
    fins = tenant_conn.execute(
        f"SELECT finalidade, COUNT(*) as cnt FROM imoveis {wh} "
        f"GROUP BY finalidade ORDER BY cnt DESC", params
    ).fetchall()
    if fins:
        lines.append("<b>📍 Por finalidade:</b>")
        fin_labels = {"venda": "🛒 Venda", "aluguel": "🔑 Aluguel",
                      "ambos": "🤝 Ambos", "temporada": "🏖️ Temporada"}
        for fin, cnt in fins:
            fl = fin_labels.get(fin.lower(), fin.title() if fin else "—")
            lines.append(f"  {fl}: {cnt:,}".replace(",", "."))
        lines.append("")

    # 4) Distribuição de dormitórios
    dorms = tenant_conn.execute(
        f"SELECT CASE "
        f"  WHEN bedrooms IS NULL OR bedrooms = 0 THEN '0 (Studio/Kit)' "
        f"  WHEN bedrooms = 1 THEN '1' "
        f"  WHEN bedrooms = 2 THEN '2' "
        f"  WHEN bedrooms = 3 THEN '3' "
        f"  WHEN bedrooms >= 4 THEN '4+' "
        f"  ELSE 'N/I' END as label, COUNT(*) as cnt "
        f"FROM imoveis {wh} GROUP BY label ORDER BY MIN(bedrooms)", params
    ).fetchall()
    if dorms:
        lines.append("<b>🛏️ Dormitórios:</b>")
        for label, cnt in dorms:
            lines.append(f"  {label} dorm(s): {cnt:,}".replace(",", "."))
        lines.append("")

    # 5) Top 5 bairros
    bairros = tenant_conn.execute(
        f"SELECT district, COUNT(*) as cnt FROM imoveis {wh} "
        f"GROUP BY district ORDER BY cnt DESC LIMIT 5", params
    ).fetchall()
    if bairros:
        lines.append("<b>📍 Top 5 bairros:</b>")
        for i, (bairro, cnt) in enumerate(bairros, 1):
            name = bairro.title() if bairro else "—"
            lines.append(f"  {i}. {name}: {cnt:,}".replace(",", "."))
        lines.append("")

    # 6) Preço médio de venda e locação
    venda_where = f"{wh} AND sale_price > 0" if wh else "WHERE sale_price > 0"
    aluguel_where = f"{wh} AND rental_price > 0" if wh else "WHERE rental_price > 0"
    avg_venda = tenant_conn.execute(
        f"SELECT AVG(sale_price) FROM imoveis {venda_where}", params
    ).fetchone()[0]
    avg_aluguel = tenant_conn.execute(
        f"SELECT AVG(rental_price) FROM imoveis {aluguel_where}", params
    ).fetchone()[0]

    lines.append("<b>💰 Preço médio:</b>")
    if avg_venda:
        lines.append(f"  🛒 Venda: <b>R$ {avg_venda:,.0f}</b>".replace(",", "X").replace(".", ",").replace("X", "."))
    else:
        lines.append(f"  🛒 Venda: s/dados")
    if avg_aluguel:
        lines.append(f"  🔑 Aluguel: <b>R$ {avg_aluguel:,.0f}</b>".replace(",", "X").replace(".", ",").replace("X", "."))
    else:
        lines.append(f"  🔑 Aluguel: s/dados")

    lines.append("")
    lines.append(f"<i>Use /stats apartamentos, /stats Santana…</i>")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.message(F.text == "🎯 Filtros")
async def cmd_filtros(message: Message):
    await message.answer(
        "Escolha um filtro para refinar sua busca:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📍 Bairro", callback_data="quiz_bairro"),
                InlineKeyboardButton(text="🏷️ Tipo", callback_data="quiz_tipo"),
            ],
            [
                InlineKeyboardButton(text="💰 Preço Venda", callback_data="quiz_preco"),
                InlineKeyboardButton(text="🛏️ Dormitórios", callback_data="quiz_quartos"),
            ],
        ]),
    )
