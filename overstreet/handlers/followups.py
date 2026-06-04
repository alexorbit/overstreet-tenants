"""Handler: follow-ups automatizados — comandos /lembrete, /followups, /pendencias, FSM."""
import re
import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
)
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from overstreet.states import FollowupStates
from overstreet.db.followups import (
    insert_followup, get_followup_by_id, mark_done, mark_adiado,
    delete_followup, list_atrasados, list_hoje, list_proximos,
)
from overstreet.db.clientes import resolve_alias, search_clientes
from overstreet.db.imoveis import get_imovel_by_id

log = logging.getLogger("overstreet.handlers.followups")
router = Router()

# ── Dias da semana (segunda=0 → Monday in Python) ──
_DIAS_SEMANA = {
    "dom": 6, "domingo": 6,
    "seg": 0, "segunda": 0,
    "ter": 1, "terça": 1, "terca": 1,
    "qua": 2, "quarta": 2,
    "qui": 3, "quinta": 3,
    "sex": 4, "sexta": 4,
    "sáb": 5, "sab": 5, "sabado": 5,
}

# ── Tipos válidos ──
_TIPOS_VALIDOS = {"ligar", "whatsapp", "email", "visita", "proposta", "lembrete"}

# ── Ícones por tipo ──
_TIPO_ICONS = {
    "ligar": "📞",
    "whatsapp": "💬",
    "email": "📧",
    "visita": "🏠",
    "proposta": "📋",
    "lembrete": "📝",
}


def parse_natural_date(text: str) -> str | None:
    """Parse natural language date to 'YYYY-MM-DD'. Returns None on failure."""
    text = text.strip().lower()
    now = datetime.now()
    date: datetime | None = None

    # Pattern: "amanhã"
    if "amanhã" in text or "amanha" in text:
        date = now + timedelta(days=1)
    # Pattern: "hoje"
    elif "hoje" in text:
        date = now
    # Pattern: dia da semana
    else:
        for word, weekday in _DIAS_SEMANA.items():
            if re.search(r'\b' + re.escape(word) + r'\b', text):
                days_ahead = (weekday - now.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                date = now + timedelta(days=days_ahead)
                break

    # Pattern: DD/MM ou DD/MM/YYYY
    if date is None:
        dm_match = re.search(r'(\d{1,2})/(\d{2})(?:/(\d{4}))?', text)
        if dm_match:
            day = int(dm_match.group(1))
            month = int(dm_match.group(2))
            year = int(dm_match.group(3)) if dm_match.group(3) else now.year
            try:
                date = datetime(year, month, day)
            except ValueError:
                return None

    # Pattern: YYYY-MM-DD
    if date is None:
        iso_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
        if iso_match:
            try:
                date = datetime(
                    int(iso_match.group(1)),
                    int(iso_match.group(2)),
                    int(iso_match.group(3)),
                )
            except ValueError:
                return None

    if date is None:
        return None

    # Don't allow past dates
    if date.date() < now.date():
        return None

    return date.strftime("%Y-%m-%d")


def _extract_codigo_imovel(text: str) -> int | None:
    """Extrai COD de imóvel (ex: COD 270034 ou cod 404227)."""
    m = re.search(r'(?:cod|código|codigo)\s*(\d{4,})', text.lower())
    if m:
        return int(m.group(1))
    return None


def _guess_tipo(text: str) -> str:
    """Infere o tipo do follow-up pelo texto. Default: 'lembrete'."""
    text_lower = text.lower()
    if "ligar" in text_lower or "telefon" in text_lower:
        return "ligar"
    if "whatsapp" in text_lower or "zap" in text_lower:
        return "whatsapp"
    if "email" in text_lower or "e-mail" in text_lower:
        return "email"
    if "visita" in text_lower or "visitar" in text_lower:
        return "visita"
    if "proposta" in text_lower or "propor" in text_lower:
        return "proposta"
    return "lembrete"


def _resolve_cliente(db, text: str) -> tuple[int | None, str | None]:
    """Tenta encontrar um cliente mencionado no texto."""
    if not db or not tenant_id:
        return None, None

    # Try each word as a potential name lookup
    words = re.findall(r'\b[A-ZÀ-ÿ][a-zà-ÿ]+\b', text)
    for word in words:
        cliente = resolve_alias(db, word)
        if cliente:
            return cliente["id"], cliente["nome"]

    # Try full text search
    clientes = search_clientes(db, tenant_id, text)
    if len(clientes) == 1:
        return clientes[0]["id"], clientes[0]["nome"]

    return None, None


def _build_descricao(data_prazo: str, rest: str, tipo: str) -> str:
    """Monta descrição limpa removendo data e COD do texto original."""
    desc = rest
    # Remove a data parseada
    now = datetime.now()
    # Remove palavras de data
    for word in list(_DIAS_SEMANA.keys()) + ["amanhã", "amanha", "hoje"]:
        desc = re.sub(r'\b' + re.escape(word) + r'\b', '', desc, flags=re.IGNORECASE)
    # Remove DD/MM patterns
    desc = re.sub(r'\b\d{1,2}/\d{2}(?:/\d{4})?\b', '', desc)
    # Remove YYYY-MM-DD
    desc = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', desc)
    # Remove COD references
    desc = re.sub(r'\b(?:cod|código|codigo)\s*\d{4,}\b', '', desc, flags=re.IGNORECASE)
    # Clean up whitespace
    desc = re.sub(r'\s+', ' ', desc).strip().strip(" —-.,;")

    if not desc:
        return f"Follow-up de {tipo}"
    return desc


# ── Comando /lembrete ──────────────────────────────────────────────────────

@router.message(Command("lembrete"))
async def cmd_lembrete(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Cria follow-up a partir do comando /lembrete."""
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        return

    text = message.text.removeprefix("/lembrete").strip()
    if not text:
        await message.answer(
            "📝 <b>Criar Lembrete</b>\n\n"
            "Informe a <b>data</b> e a <b>descrição</b>:\n\n"
            "<i>Exemplos:\n"
            "• <code>/lembrete amanhã ligar para João sobre apto Santana</code>\n"
            "• <code>/lembrete 05/06 enviar proposta COD 270034 para Maria</code>\n"
            "• <code>/lembrete segunda visitar imóvel COD 404227 com Pedro</code></i>",
            parse_mode=ParseMode.HTML,
        )
        return

    await _process_lembrete(message, text, db, tenant)


# ── Comando /followups e /pendencias ────────────────────────────────────────

@router.message(Command("followups"))
@router.message(Command("pendencias"))
async def cmd_followups(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Lista follow-ups pendentes, agrupados por data."""
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        return

    atrasados = list_atrasados(db)
    hoje = list_hoje(db)
    proximos = list_proximos(db)

    if not atrasados and not hoje and not proximos:
        await message.answer(
            "✅ <b>Sem pendências!</b>\n\n"
            "Nenhum follow-up pendente no momento.\n\n"
            "<i>Use <code>/lembrete</code> para criar um novo.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = ["📋 <b>FOLLOW-UPS PENDENTES</b>", ""]

    # Atrasados
    if atrasados:
        lines.append(f"⚡ <b>Atrasados ({len(atrasados)}):</b>")
        for f in atrasados:
            lines.append(_format_followup_line(f))
            lines.append(_format_done_button(f))
        lines.append("")

    # Hoje
    if hoje:
        lines.append(f"📌 <b>Hoje ({len(hoje)}):</b>")
        for f in hoje:
            lines.append(_format_followup_line(f))
            lines.append(_format_done_button(f))
        lines.append("")

    # Próximos 7 dias
    if proximos:
        lines.append(f"📅 <b>Próximos 7 dias ({len(proximos)}):</b>")
        for f in proximos:
            lines.append(_format_followup_line(f))
            lines.append(_format_done_button(f))
        lines.append("")

    lines.append("<i>Use <code>/lembrete</code> para criar um novo follow-up.</i>")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


def _format_followup_line(f: dict) -> str:
    """Formata uma linha de follow-up."""
    icon = _TIPO_ICONS.get(f["tipo"], "📌")
    # Format date
    try:
        dt = datetime.strptime(f["data_prazo"], "%Y-%m-%d")
        date_str = dt.strftime("%d/%m")
        weekday = dt.strftime("%a")
        date_str += f" ({weekday})"
    except ValueError:
        date_str = f["data_prazo"]

    cliente = f.get("cliente_nome") or ""
    desc = f.get("descricao") or "—"
    imovel_ref = ""

    # If has imovel, show short ref
    if f.get("imovel_id"):
        imovel_ref = f" [COD {f['imovel_id']}]"

    line = f"  {icon} <code>{f['id']}</code> — {date_str} — {f['tipo']}"
    line += f" — {desc}{imovel_ref}"
    if cliente:
        line += f" — {cliente}"
    return line


def _format_done_button(f: dict) -> str:
    """Formata referência de botão inline para follow-up (usado no texto)."""
    return f"  <code>[</code>✅ Feito<code>]</code>"


# ── Comando /followup done [id] ─────────────────────────────────────────────

@router.message(Command("followup"))
async def cmd_followup_action(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Ações sobre follow-up: /followup done ID, /followup adiar ID."""
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        return

    parts = message.text.removeprefix("/followup").strip().split(None, 1)
    if not parts:
        await message.answer(
            "Use:\n"
            "• <code>/followup done ID</code> — marcar como concluído\n"
            "• <code>/followup adiar ID</code> — adiar para amanhã\n"
            "• <code>/followups</code> — listar pendências",
            parse_mode=ParseMode.HTML,
        )
        return

    action = parts[0].lower()
    if action not in ("done", "adiar", "delete"):
        await message.answer(
            f"❌ Ação desconhecida: <b>{action}</b>\n\n"
            "Ações disponíveis: <code>done</code>, <code>adiar</code>, <code>delete</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.answer(f"❌ Informe o ID do follow-up.\nEx: <code>/followup {action} 5</code>",
                             parse_mode=ParseMode.HTML)
        return

    followup_id = int(parts[1].strip())
    followup = get_followup_by_id(db, followup_id)

    if not followup:
        await message.answer(f"❌ Follow-up <b>#{followup_id}</b> não encontrado.",
                             parse_mode=ParseMode.HTML)
        return

    if action == "done":
        ok = mark_done(db, followup_id)
        if ok:
            desc = followup.get("descricao") or "—"
            await message.answer(
                f"✅ Follow-up <b>#{followup_id}</b> marcado como <b>feito</b>!\n\n"
                f"📝 {desc}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(f"⚠️ Follow-up <b>#{followup_id}</b> já estava concluído.",
                                 parse_mode=ParseMode.HTML)

    elif action == "adiar":
        amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        ok = mark_adiado(db, followup_id, amanha)
        if ok:
            await message.answer(
                f"🔄 Follow-up <b>#{followup_id}</b> adiado para <b>amanhã</b>.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer("❌ Erro ao adiar follow-up.", parse_mode=ParseMode.HTML)

    elif action == "delete":
        ok = delete_followup(db, followup_id)
        if ok:
            await message.answer(f"🗑️ Follow-up <b>#{followup_id}</b> removido.",
                                 parse_mode=ParseMode.HTML)
        else:
            await message.answer("❌ Erro ao remover follow-up.", parse_mode=ParseMode.HTML)


# ── Callbacks inline ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("fu_done_"))
async def on_followup_done_callback(callback: CallbackQuery, tenant_conn=None, **kwargs):
    """Callback: marca follow-up como concluído via botão inline."""
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    followup_id = int(callback.data.removeprefix("fu_done_"))
    ok = mark_done(db, followup_id)

    if ok:
        desc = ""
        followup = get_followup_by_id(db, followup_id)
        if followup:
            desc = followup.get("descricao") or ""
        await callback.answer(
            f"✅ Follow-up #{followup_id} concluído!{' — ' + desc if desc else ''}",
            show_alert=True,
        )
    else:
        await callback.answer(f"⚠️ Follow-up #{followup_id} já estava concluído.", show_alert=True)


@router.callback_query(F.data.startswith("fu_del_"))
async def on_followup_delete_callback(callback: CallbackQuery, tenant_conn=None, **kwargs):
    """Callback: remove follow-up via botão inline."""
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    followup_id = int(callback.data.removeprefix("fu_del_"))
    ok = delete_followup(db, followup_id)

    if ok:
        await callback.answer(f"🗑️ Follow-up #{followup_id} removido.", show_alert=True)
    else:
        await callback.answer("❌ Erro ao remover.", show_alert=True)


# ── /followups com botões inline ──────────────────────────────────────────

@router.message(Command("followups_btn"))
@router.message(Command("pendencias_btn"))
async def cmd_followups_buttons(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Lista follow-ups pendentes com botões inline de ação."""
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        return

    atrasados = list_atrasados(db)
    hoje = list_hoje(db)
    proximos = list_proximos(db)

    all_items = atrasados + hoje + proximos

    if not all_items:
        await message.answer(
            "✅ <b>Sem pendências!</b>\n\n"
            "Nenhum follow-up pendente no momento.\n\n"
            "<i>Use <code>/lembrete</code> para criar um novo.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Send grouped text
    lines = ["📋 <b>FOLLOW-UPS PENDENTES</b>", ""]

    if atrasados:
        lines.append(f"⚡ <b>Atrasados ({len(atrasados)}):</b>")
        for f in atrasados:
            lines.append(_format_followup_line(f))
        lines.append("")

    if hoje:
        lines.append(f"📌 <b>Hoje ({len(hoje)}):</b>")
        for f in hoje:
            lines.append(_format_followup_line(f))
        lines.append("")

    if proximos:
        lines.append(f"📅 <b>Próximos 7 dias ({len(proximos)}):</b>")
        for f in proximos:
            lines.append(_format_followup_line(f))
        lines.append("")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    # Send action buttons for each item (max 10)
    items = all_items[:10]
    for f in items:
        icon = _TIPO_ICONS.get(f["tipo"], "📌")
        desc = f.get("descricao") or "—"
        cliente = f.get("cliente_nome") or ""

        try:
            dt = datetime.strptime(f["data_prazo"], "%Y-%m-%d")
            date_str = dt.strftime("%d/%m")
        except ValueError:
            date_str = f["data_prazo"]

        label = f"{icon} #{f['id']} {date_str} — {desc}"
        if cliente:
            label += f" — {cliente}"
        if len(label) > 64:
            label = label[:61] + "..."

        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Feito",
                    callback_data=f"fu_done_{f['id']}",
                ),
                InlineKeyboardButton(
                    text="🗑️ Remover",
                    callback_data=f"fu_del_{f['id']}",
                ),
            ],
        ])
        await message.answer(label, reply_markup=buttons)

    if len(all_items) > 10:
        await message.answer(
            f"📊 ...e mais <b>{len(all_items) - 10}</b> pendências.\n"
            "Use <code>/followup done ID</code> para concluir.",
            parse_mode=ParseMode.HTML,
        )


# ── FSM: trigger por texto livre ────────────────────────────────────────────

def _is_followup_trigger(text: str | None) -> bool:
    """Detecta se o texto é um trigger para criar follow-up via FSM."""
    if not text:
        return False
    text_lower = text.lower().strip()
    patterns = [
        r'\blembrar\b', r'\bfollow[\s-]?up\b', r'\banotar\b',
        r'\bpreciso\s+lembrar\b', r'\bnão\s+esquecer\b',
        r'\blesse\s+lembrete\b',
    ]
    return any(re.search(p, text_lower) for p in patterns)


@router.message(
    F.text.func(lambda t: _is_followup_trigger(t)),
    ~StateFilter(FollowupStates.aguardando_descricao),
)
async def on_followup_trigger(message: Message, state: FSMContext, **kwargs):
    """Inicia FSM de follow-up ao detectar trigger no texto."""
    await state.set_state(FollowupStates.aguardando_descricao)
    await message.answer(
        "📝 <b>Follow-up</b>\n\n"
        "O que você precisa lembrar e <b>quando</b>?\n\n"
        "<i>Exemplos:\n"
        "• amanhã ligar para João sobre apto Santana\n"
        "• 05/06 enviar proposta COD 270034 para Maria\n"
        "• segunda visitar imóvel COD 404227 com Pedro</i>\n\n"
        "<i>Envie <code>/cancelar</code> para sair.</i>",
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(FollowupStates.aguardando_descricao), F.text)
async def on_followup_descricao(message: Message, state: FSMContext,
                                tenant_conn=None, tenant=None, **kwargs):
    """Processa a descrição do follow-up na FSM."""
    db = tenant_conn
    text = message.text.strip()

    # Allow cancel
    if text.lower() in ("/cancelar", "/cancel"):
        await state.clear()
        await message.answer("❌ Criação de follow-up cancelada.")
        return

    if not db or not tenant:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        await state.clear()
        return

    await _process_lembrete(message, text, db, tenant)
    await state.clear()


# ── Processamento compartilhado de lembrete ────────────────────────────────

async def _process_lembrete(
    target: Message,
    text: str,
    db,
    tenant: dict,
):
    """Processa texto de lembrete e cria follow-up no banco."""
    # Parse date
    data_prazo = parse_natural_date(text)
    if not data_prazo:
        await target.answer(
            "❌ Não consegui entender a <b>data</b>. Tente:\n\n"
            "<i>• amanhã\n"
            "• segunda / terça / quarta...\n"
            "• 05/06\n"
            "• 2025-07-15</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Guess tipo
    tipo = _guess_tipo(text)

    # Resolve cliente
    cliente_id, cliente_nome = _resolve_cliente(db, tenant_id, text)

    # Resolve imóvel
    imovel_id = _extract_codigo_imovel(text)

    # Build clean description
    descricao = _build_descricao(data_prazo, text, tipo)

    # Insert
    followup_id = insert_followup(db, tipo=tipo,
        data_prazo=data_prazo,
        descricao=descricao,
        cliente_id=cliente_id,
        cliente_nome=cliente_nome,
        imovel_id=imovel_id,
    )

    # Format date for display
    try:
        dt = datetime.strptime(data_prazo, "%Y-%m-%d")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        if dt.date() == today.date():
            date_label = "Hoje"
        elif dt.date() == tomorrow.date():
            date_label = "Amanhã"
        else:
            date_label = dt.strftime("%d/%m (%a)")
    except ValueError:
        date_label = data_prazo

    icon = _TIPO_ICONS.get(tipo, "📌")
    response = f"{icon} <b>Lembrete criado!</b>\n\n"
    response += f"📅 <b>Quando:</b> {date_label}\n"
    response += f"📌 <b>Tipo:</b> {tipo}\n"
    response += f"📝 <b>O que:</b> {descricao}\n"
    if cliente_nome:
        response += f"👤 <b>Cliente:</b> {cliente_nome}\n"
    if imovel_id:
        response += f"🏠 <b>Imóvel:</b> COD {imovel_id}\n"
    response += f"\n📋 <b>ID:</b> <code>{followup_id}</code>"
    response += "\n\n💡 Use <code>/followups</code> para ver pendências."

    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Marcar como feito", callback_data=f"fu_done_{followup_id}"),
        ],
    ])
    await target.answer(response, parse_mode=ParseMode.HTML, reply_markup=buttons)
