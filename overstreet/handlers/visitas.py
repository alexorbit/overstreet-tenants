"""Handler FSM: agendamento de visitas + comandos /visitas e /visita COD."""
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
from overstreet.states import AgendamentoVisitaStates
from overstreet.db.imoveis import get_imovel_by_id, _query_dict, _query_dicts
from overstreet.db.clientes import (
    list_clientes, search_clientes, resolve_alias, get_cliente_by_id,
)
from overstreet.db.visitas import (
    insert_visita, get_visita_by_id, list_visitas_upcoming,
    update_visita_status, update_visita_data,
)

log = logging.getLogger("overstreet.handlers.visitas")
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


# ── Natural date parser ─────────────────────────────────────────────────────

def parse_natural_date(text: str) -> str | None:
    """Parse natural language date to 'YYYY-MM-DD HH:MM'. Returns None on failure."""
    text = text.strip().lower()
    now = datetime.now()
    date: datetime | None = None
    hour = None

    # Extract hour: "14h", "14:00", "as 14", "às 14", "às 14h", "as 14:00"
    time_match = re.search(
        r'(?:às|as)\s*(\d{1,2})[:h]?(\d{2})?|'
        r'(\d{1,2})[:h](\d{2})\s*$|'
        r'(\d{1,2})h\s*$',
        text,
    )
    if time_match:
        h = time_match.group(1) or time_match.group(3) or time_match.group(5)
        m = time_match.group(2) or time_match.group(4) or "00"
        try:
            hour = (int(h), int(m))
        except (ValueError, TypeError):
            hour = None

    # Pattern: "amanhã [às HHh]"
    if "amanhã" in text or "amanha" in text:
        date = now + timedelta(days=1)
    # Pattern: "hoje [às HHh]"
    elif "hoje" in text:
        date = now
    # Pattern: dia da semana
    else:
        for word, weekday in _DIAS_SEMANA.items():
            if re.search(r'\b' + re.escape(word) + r'\b', text):
                days_ahead = (weekday - now.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7  # se for o próprio dia, vamos para a próxima semana
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

    if date is None:
        return None

    if hour:
        date = date.replace(hour=hour[0], minute=hour[1], second=0, microsecond=0)
    else:
        # Default: 10:00
        date = date.replace(hour=10, minute=0, second=0, microsecond=0)

    # Don't allow past times (unless it's today and still within the hour)
    if date < now and date.date() < now.date():
        return None

    return date.strftime("%Y-%m-%d %H:%M")


def format_visita_datetime(dt_str: str) -> str:
    """Formata 'YYYY-MM-DD HH:MM' para 'DD/MM HHh' ou 'DD/MM às HHh'."""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        return dt.strftime("%d/%m %Hh")
    except ValueError:
        return dt_str


def format_visita_date_label(dt_str: str) -> str:
    """Retorna 'Hoje', 'Amanhã' ou 'DD/MM'."""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        if dt.date() == today.date():
            return "Hoje"
        elif dt.date() == tomorrow.date():
            return "Amanhã"
        else:
            return dt.strftime("%d/%m")
    except ValueError:
        return dt_str


# ── Trigger detection ──────────────────────────────────────────────────────

def _is_agendar_trigger(text: str | None) -> bool:
    if not text:
        return False
    text_lower = text.lower().strip()
    # Exclude "/visita COD" pattern (command with a number argument)
    if re.match(r'^/visita\s+\d+\b', text_lower):
        return False
    # Exclude "/visitas" (plural — listing command)
    if text_lower in ("/visitas",):
        return False
    patterns = [
        r'agendar\s+visita', r'marcar\s+visita', r'agendar\s+uma\s+visita',
        r'marcar\s+uma\s+visita', r'visita\s+agendada', r'agendar\s+um?\s+im[oó]vel',
    ]
    # Also match bare "/visita" without number
    if text_lower == "/visita":
        return True
    return any(re.search(p, text_lower) for p in patterns)


# ── Helpers ────────────────────────────────────────────────────────────────

def _format_imovel_summary(imovel: dict) -> str:
    """Resume do imóvel em 1-2 linhas."""
    street = imovel.get("street") or imovel.get("reference") or "Sem endereço"
    district = imovel.get("district") or ""
    if district:
        street += f" — {district}"
    price = imovel.get("sale_price") or imovel.get("rental_price")
    if price:
        street += f" — R$ {price}"
    return f"COD {imovel['id']}, {street}"


def _get_recent_clientes(db, limit: int = 6) -> list[dict]:
    """Retorna últimos clientes cadastrados."""
    try:
        cursor = db.execute(
            "SELECT * FROM clientes WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
            (tenant_id, limit),
        )
        if not cursor.description:
            return []
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception:
        return []


# ── FSM: Agendamento de Visita ─────────────────────────────────────────────

@router.message(F.text.func(lambda t: _is_agendar_trigger(t)),
                ~StateFilter(AgendamentoVisitaStates.aguardando_imovel,
                             AgendamentoVisitaStates.aguardando_data,
                             AgendamentoVisitaStates.aguardando_cliente,
                             AgendamentoVisitaStates.confirmar))
async def cmd_agendar_visita(message: Message, state: FSMContext, **kwargs):
    await state.set_state(AgendamentoVisitaStates.aguardando_imovel)
    await message.answer(
        "📅 <b>Agendar Visita</b>\n\n"
        "Informe o <b>código do imóvel</b> (ex: <code>270034</code>) "
        "ou descreva o imóvel que deseja visitar:\n\n"
        "<i>Ex: 270034  ou  apartamento Santana 3 quartos</i>",
        parse_mode=ParseMode.HTML,
    )


@router.message(StateFilter(AgendamentoVisitaStates.aguardando_imovel), F.text)
async def on_aguardando_imovel(message: Message, state: FSMContext,
                               tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    if not db:
        await message.answer("❌ Erro de conexão com o banco de dados.")
        await state.clear()
        return

    text = message.text.strip()

    # Se o texto for um número, busca direto
    if text.isdigit():
        imovel = get_imovel_by_id(db, int(text))
        if not imovel:
            await message.answer(f"❌ Imóvel COD <b>{text}</b> não encontrado.\n"
                                 "Tente outro código ou descreva o imóvel.",
                                 parse_mode=ParseMode.HTML)
            return
    else:
        # Busca por texto
        try:
            rows = _query_dicts(
                db,
                "SELECT * FROM imoveis WHERE full_text LIKE ? OR street LIKE ? "
                "OR district LIKE ? LIMIT 5",
                (f"%{text}%", f"%{text}%", f"%{text}%"),
            )
            if not rows:
                await message.answer("❌ Nenhum imóvel encontrado com essa descrição.\n"
                                     "Tente o código ou outra descrição.",
                                     parse_mode=ParseMode.HTML)
                return
            if len(rows) == 1:
                imovel = rows[0]
            else:
                # Múltiplos resultados — mostrar botões para escolher
                buttons = []
                row = []
                for r in rows:
                    summary = _format_imovel_summary(r)
                    # Truncate long text for button label (max 64 chars)
                    if len(summary) > 64:
                        summary = summary[:61] + "..."
                    row.append(InlineKeyboardButton(
                        text=summary, callback_data=f"visita_sel_{r['id']}",
                    ))
                    if len(row) == 1:
                        buttons.append(row)
                        row = []
                if row:
                    buttons.append(row)
                buttons.append([InlineKeyboardButton(text="❌ Cancelar", callback_data="visita_cancelar")])
                await state.update_data(pending_imoveis=rows)
                await message.answer(
                    "Encontrei mais de um imóvel. Escolha:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    parse_mode=ParseMode.HTML,
                )
                return
        except Exception as e:
            log.warning("Busca imóvel falhou: %s", e)
            await message.answer("❌ Erro ao buscar imóvel. Tente novamente.")
            return

    # Encontrou o imóvel → avançar para data
    await state.update_data(visita_imovel=imovel)
    await state.set_state(AgendamentoVisitaStates.aguardando_data)

    summary = _format_imovel_summary(imovel)
    await message.answer(
        f"🏠 <b>Imóvel selecionado:</b>\n{summary}\n\n"
        "📅 Qual a <b>data e horário</b> da visita?\n\n"
        "<i>Exemplos:\n"
        "• amanhã às 14h\n"
        "• segunda 10:00\n"
        "• 25/06 às 16h\n"
        "• 03/07</i>",
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("visita_sel_"),
                       StateFilter(AgendamentoVisitaStates.aguardando_imovel))
async def on_select_imovel(callback: CallbackQuery, state: FSMContext,
                           tenant_conn=None, **kwargs):
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro de conexão.", show_alert=True)
        return

    imovel_id = int(callback.data.removeprefix("visita_sel_"))
    imovel = get_imovel_by_id(db, imovel_id)
    if not imovel:
        await callback.answer("Imóvel não encontrado!", show_alert=True)
        return

    await state.update_data(visita_imovel=imovel, pending_imoveis=[])
    await state.set_state(AgendamentoVisitaStates.aguardando_data)

    summary = _format_imovel_summary(imovel)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"🏠 <b>Imóvel selecionado:</b>\n{summary}\n\n"
        "📅 Qual a <b>data e horário</b> da visita?\n\n"
        "<i>Exemplos:\n"
        "• amanhã às 14h\n"
        "• segunda 10:00\n"
        "• 25/06 às 16h\n"
        "• 03/07</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "visita_cancelar")
async def on_visita_cancelar_early(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Agendamento cancelado.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.message(StateFilter(AgendamentoVisitaStates.aguardando_data), F.text)
async def on_aguardando_data(message: Message, state: FSMContext,
                             tenant_conn=None, tenant=None, **kwargs):
    text = message.text.strip()
    dt_str = parse_natural_date(text)

    if not dt_str:
        await message.answer(
            "❌ Não consegui entender a data. Tente:\n\n"
            "<i>• amanhã às 14h\n"
            "• segunda 10:00\n"
            "• 25/06 às 16h\n"
            "• 03/07</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Check if this is a reschedule flow
    data = await state.get_data()
    remarcar_id = data.get("visita_remarcando_id")
    if remarcar_id:
        db = tenant_conn
        if not db:
            await message.answer("❌ Erro de conexão.")
            await state.clear()
            return
        formatted = format_visita_datetime(dt_str)
        update_visita_data(db, remarcar_id, dt_str)
        await state.clear()
        await message.answer(
            f"🔄 Visita <b>#{remarcar_id}</b> remarcada para <b>{formatted}</b>!",
            parse_mode=ParseMode.HTML,
        )
        return

    # Normal flow: new visit scheduling
    await state.update_data(visita_data=dt_str)
    await state.set_state(AgendamentoVisitaStates.aguardando_cliente)

    formatted = format_visita_datetime(dt_str)
    db = tenant_conn

    # Mostrar últimos clientes como botões
    recent = _get_recent_clientes(db, limit=6) if db and tenant_id else []

    buttons = []
    row = []
    for c in recent:
        row.append(InlineKeyboardButton(
            text=c["nome"], callback_data=f"visita_cli_{c['id']}|{c['nome']}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if recent:
        msg = (
            f"📅 Data: <b>{formatted}</b>\n\n"
            "👤 Para qual <b>cliente</b>? Selecione abaixo ou digite o nome:"
        )
    else:
        msg = (
            f"📅 Data: <b>{formatted}</b>\n\n"
            "👤 Para qual <b>cliente</b>? Digite o nome ou /visita para agendar sem cliente:"
        )

    buttons.append([InlineKeyboardButton(
        text="⏭️ Sem cliente", callback_data="visita_cli_0|Sem cliente",
    )])

    await message.answer(msg, parse_mode=ParseMode.HTML,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("visita_cli_"),
                       StateFilter(AgendamentoVisitaStates.aguardando_cliente))
async def on_select_cliente(callback: CallbackQuery, state: FSMContext,
                            tenant_conn=None, **kwargs):
    db = tenant_conn
    raw = callback.data.removeprefix("visita_cli_")
    try:
        cliente_id, cliente_nome = raw.split("|", 1)
        cliente_id = int(cliente_id)
    except ValueError:
        await callback.answer("Erro.", show_alert=True)
        return

    await state.update_data(
        visita_cliente_id=cliente_id if cliente_id > 0 else None,
        visita_cliente_nome=cliente_nome if cliente_id > 0 else None,
    )
    await state.set_state(AgendamentoVisitaStates.confirmar)
    await _show_preview(callback.message, state, db)
    await callback.answer()


@router.message(StateFilter(AgendamentoVisitaStates.aguardando_cliente), F.text)
async def on_aguardando_cliente_text(message: Message, state: FSMContext,
                                    tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    text = message.text.strip()
    # Tenta resolver por alias ou nome
    if db and tenant_id:
        cliente = resolve_alias(db, text)
        if cliente:
            await state.update_data(
                visita_cliente_id=cliente["id"],
                visita_cliente_nome=cliente["nome"],
            )
            await state.set_state(AgendamentoVisitaStates.confirmar)
            await _show_preview(message, state, db)
            return

    # Não encontrou — aceita nome digitado
    await state.update_data(visita_cliente_id=None, visita_cliente_nome=text)
    await state.set_state(AgendamentoVisitaStates.confirmar)
    await _show_preview(message, state, db)


async def _show_preview(target: Message | CallbackQuery, state: FSMContext, db):
    """Monta e exibe o preview de confirmação da visita."""
    data = await state.get_data()
    imovel = data.get("visita_imovel", {})
    dt_str = data.get("visita_data", "")
    cliente_nome = data.get("visita_cliente_nome")

    summary = _format_imovel_summary(imovel)
    formatted_dt = format_visita_datetime(dt_str)

    lines = [
        "📅 <b>Confirmar Agendamento</b>",
        "",
        f"🏠 <b>Imóvel:</b> {summary}",
        f"🕐 <b>Data:</b> {formatted_dt}",
    ]
    if cliente_nome:
        lines.append(f"👤 <b>Cliente:</b> {cliente_nome}")
    else:
        lines.append("👤 <b>Cliente:</b> —")
    lines.append("")

    text = "\n".join(lines)

    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirmar", callback_data="visita_confirmar"),
            InlineKeyboardButton(text="✏️ Alterar data", callback_data="visita_alterar_data"),
        ],
        [
            InlineKeyboardButton(text="✏️ Alterar cliente", callback_data="visita_alterar_cliente"),
            InlineKeyboardButton(text="❌ Cancelar", callback_data="visita_cancelar_final"),
        ],
    ])

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await target.message.answer(text, parse_mode=ParseMode.HTML, reply_markup=buttons)
    else:
        await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=buttons)


# ── Callbacks de confirmação/alteração ────────────────────────────────────

@router.callback_query(F.data == "visita_confirmar",
                       StateFilter(AgendamentoVisitaStates.confirmar))
async def on_confirmar_visita(callback: CallbackQuery, state: FSMContext,
                              tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    if not db or not tenant:
        await callback.answer("❌ Erro interno.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    imovel = data.get("visita_imovel", {})
    dt_str = data.get("visita_data", "")
    cliente_id = data.get("visita_cliente_id")
    cliente_nome = data.get("visita_cliente_nome")
    visita_id = insert_visita(db, imovel_id=imovel["id"],
        data_visita=dt_str,
        cliente_id=cliente_id,
        cliente_nome=cliente_nome,
    )

    formatted_dt = format_visita_datetime(dt_str)
    summary = _format_imovel_summary(imovel)

    # Auto-create follow-up for the visita
    from overstreet.db.followups import insert_followup
    try:
        insert_followup(db, tipo="visita",
            data_prazo=dt_str.split(" ")[0],  # date only (YYYY-MM-DD)
            descricao=f"Visita: {summary}",
            cliente_id=cliente_id,
            cliente_nome=cliente_nome,
            imovel_id=imovel["id"],
        )
        log.info("Follow-up automático criado para visita #%d", visita_id)
    except Exception as e:
        log.warning("Erro ao criar follow-up automático para visita: %s", e)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    msg = (
        f"✅ <b>Visita agendada!</b>\n\n"
        f"📅 <b>{formatted_dt}</b>\n"
        f"🏠 {summary}\n"
    )
    if cliente_nome:
        msg += f"👤 {cliente_nome}\n"
    msg += f"\n📋 Código da visita: <code>{visita_id}</code>\n"
    msg += f"\n💡 Use <code>/visita {visita_id}</code> para ver detalhes e gerenciar."

    await callback.message.answer(msg, parse_mode=ParseMode.HTML)
    await state.clear()
    await callback.answer("✅ Visita agendada!")


@router.callback_query(F.data == "visita_alterar_data",
                       StateFilter(AgendamentoVisitaStates.confirmar))
async def on_alterar_data(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AgendamentoVisitaStates.aguardando_data)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "📅 Qual a <b>nova data e horário</b>?\n\n"
        "<i>Ex: amanhã às 14h, segunda 10:00, 25/06 às 16h</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "visita_alterar_cliente",
                       StateFilter(AgendamentoVisitaStates.confirmar))
async def on_alterar_cliente(callback: CallbackQuery, state: FSMContext,
                             tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    await state.set_state(AgendamentoVisitaStates.aguardando_cliente)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    recent = _get_recent_clientes(db, limit=6) if db and tenant_id else []

    buttons = []
    row = []
    for c in recent:
        row.append(InlineKeyboardButton(
            text=c["nome"], callback_data=f"visita_cli_{c['id']}|{c['nome']}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(
        text="⏭️ Sem cliente", callback_data="visita_cli_0|Sem cliente",
    )])

    await callback.message.answer(
        "👤 Para qual <b>cliente</b>? Selecione ou digite o nome:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "visita_cancelar_final")
async def on_cancelar_final(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer("❌ Agendamento cancelado.", parse_mode=ParseMode.HTML)
    await callback.answer()


# ── Comando /visitas — lista visitas agendadas ─────────────────────────────

@router.message(Command("visitas"))
async def cmd_visitas(message: Message, tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    visitas = list_visitas_upcoming(db, days=7)

    if not visitas:
        await message.answer(
            "📅 <b>Visitas Agendadas</b>\n\n"
            "Nenhuma visita agendada para os próximos 7 dias.\n\n"
            "<i>Use \"agendar visita\" para criar uma!</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Agrupar por dia
    grupos: dict[str, list[dict]] = {}
    for v in visitas:
        label = format_visita_date_label(v["data_visita"])
        grupos.setdefault(label, []).append(v)

    lines = ["📅 <b>VISITAS AGENDADAS</b>", ""]

    for label in ["Hoje", "Amanhã"]:
        if label in grupos:
            lines.append(f"📌 {label}:")
            for v in grupos[label]:
                imovel = get_imovel_by_id(db, v["imovel_id"])
                imovel_summary = _format_imovel_summary(imovel) if imovel else f"COD {v['imovel_id']}"
                time_str = v["data_visita"].split(" ")[1] if " " in v["data_visita"] else "??:??"
                cliente = v.get("cliente_nome") or "—"
                lines.append(f"  {time_str} — {imovel_summary} — {cliente}")
            lines.append("")
            del grupos[label]

    # Remaining days
    remaining = {k: grupos[k] for k in sorted(grupos.keys())}
    if remaining:
        lines.append("📌 <b>Próximos dias:</b>")
        for label, items in remaining.items():
            for v in items:
                imovel = get_imovel_by_id(db, v["imovel_id"])
                imovel_summary = _format_imovel_summary(imovel) if imovel else f"COD {v['imovel_id']}"
                dt_str = format_visita_datetime(v["data_visita"])
                cliente = v.get("cliente_nome") or "—"
                lines.append(f"  {dt_str} — {imovel_summary} — {cliente}")
        lines.append("")

    lines.append("<i>Use /visita CÓDIGO para detalhes.</i>")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


# ── Comando /visita COD — detalhes de uma visita ───────────────────────────

@router.message(Command("visita"))
async def cmd_visita_details(message: Message, tenant_conn=None, tenant=None, **kwargs):
    db = tenant_conn
    if not db or not tenant:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    args = message.text.removeprefix("/visita").strip()
    if not args.isdigit():
        await message.answer(
            "Use <code>/visita CÓDIGO</code> para ver detalhes.\n"
            "Ex: <code>/visita 12</code>\n\n"
            "Ou diga <b>agendar visita</b> para criar uma nova.",
            parse_mode=ParseMode.HTML,
        )
        return

    visita_id = int(args)
    visita = get_visita_by_id(db, visita_id)

    if not visita:
        await message.answer(f"❌ Visita <b>{visita_id}</b> não encontrada.",
                             parse_mode=ParseMode.HTML)
        return

    imovel = get_imovel_by_id(db, visita["imovel_id"])
    imovel_summary = _format_imovel_summary(imovel) if imovel else f"COD {visita['imovel_id']}"

    status_icons = {
        "agendada": "🟡",
        "realizada": "🟢",
        "cancelada": "🔴",
        "remarcada": "🟠",
    }
    icon = status_icons.get(visita["status"], "⚪")

    lines = [
        f"{icon} <b>Visita #{visita['id']}</b>",
        "",
        f"🏠 <b>Imóvel:</b> {imovel_summary}",
        f"🕐 <b>Data:</b> {format_visita_datetime(visita['data_visita'])}",
        f"📊 <b>Status:</b> {visita['status'].title()}",
    ]
    if visita.get("cliente_nome"):
        lines.append(f"👤 <b>Cliente:</b> {visita['cliente_nome']}")
    if visita.get("notas"):
        lines.append(f"📝 <b>Notas:</b> {visita['notas']}")
    lines.append(f"\n📋 Criada em: {visita.get('criada_em', '—')}")

    text = "\n".join(lines)

    buttons = []
    if visita["status"] == "agendada":
        buttons.append([
            InlineKeyboardButton(text="✅ Confirmar realização",
                                  callback_data=f"visita_done_{visita_id}"),
            InlineKeyboardButton(text="🔄 Remarcar",
                                  callback_data=f"visita_remarcar_{visita_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="❌ Cancelar",
                                  callback_data=f"visita_cancel_{visita_id}"),
        ])

    if visita.get("cliente_nome"):
        buttons.append([
            InlineKeyboardButton(text="📲 Lembrar cliente",
                                  callback_data=f"visita_lembrete_{visita_id}"),
        ])

    await message.answer(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


# ── Callbacks de ações sobre visita existente ───────────────────────────────

@router.callback_query(F.data.startswith("visita_done_"))
async def on_visita_done(callback: CallbackQuery, tenant_conn=None, **kwargs):
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    visita_id = int(callback.data.removeprefix("visita_done_"))
    update_visita_status(db, visita_id, "realizada")

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"✅ Visita <b>#{visita_id}</b> marcada como <b>realizada</b>! 🎉",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("✅ Realizada!")


@router.callback_query(F.data.startswith("visita_cancel_"))
async def on_visita_cancel(callback: CallbackQuery, tenant_conn=None, **kwargs):
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    visita_id = int(callback.data.removeprefix("visita_cancel_"))
    update_visita_status(db, visita_id, "cancelada")

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        f"❌ Visita <b>#{visita_id}</b> <b>cancelada</b>.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("❌ Cancelada.")


@router.callback_query(F.data.startswith("visita_remarcar_"))
async def on_visita_remarcar(callback: CallbackQuery, state: FSMContext,
                             tenant_conn=None, **kwargs):
    visita_id = int(callback.data.removeprefix("visita_remarcar_"))
    db = tenant_conn

    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    visita = get_visita_by_id(db, visita_id)
    if not visita:
        await callback.answer("Visita não encontrada.", show_alert=True)
        return

    # Enter FSM to ask new date
    await state.set_state(AgendamentoVisitaStates.aguardando_data)
    await state.update_data(visita_remarcando_id=visita_id, visita_imovel=None)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer(
        f"🔄 <b>Remarcar visita #{visita_id}</b>\n\n"
        "📅 Qual a <b>nova data e horário</b>?\n\n"
        "<i>Ex: amanhã às 14h, segunda 10:00, 25/06 às 16h</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("visita_lembrete_"))
async def on_visita_lembrete(callback: CallbackQuery, tenant_conn=None, **kwargs):
    db = tenant_conn
    if not db:
        await callback.answer("❌ Erro interno.", show_alert=True)
        return

    visita_id = int(callback.data.removeprefix("visita_lembrete_"))
    visita = get_visita_by_id(db, visita_id)
    if not visita:
        await callback.answer("Visita não encontrada.", show_alert=True)
        return

    imovel = get_imovel_by_id(db, visita["imovel_id"])
    cliente_nome = visita.get("cliente_nome", "")

    # Montar mensagem de lembrete
    imovel_summary = _format_imovel_summary(imovel) if imovel else f"COD {visita['imovel_id']}"
    dt_formatted = format_visita_datetime(visita["data_visita"])

    lembrete = (
        f"📅 <b>Lembrete de Visita</b>\n\n"
        f"Olá{f' {cliente_nome}' if cliente_nome else ''}! 😊\n\n"
        f"Lembrete: visita agendada para <b>{dt_formatted}</b>\n"
        f"🏠 Imóvel: {imovel_summary}\n\n"
        f"Até lá! 👋"
    )

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Enviar mensagem para o corretor encaminhar ao cliente
    await callback.message.answer(
        f"📲 <b>Mensagem de lembrete para {cliente_nome}:</b>\n\n"
        "Copie e envie ao cliente:\n\n"
        f"{'━' * 20}\n",
        parse_mode=ParseMode.HTML,
    )
    await callback.message.answer(lembrete, parse_mode=ParseMode.HTML)
    await callback.message.answer("━" * 20)
    await callback.answer("📲 Mensagem de lembrete gerada!")
