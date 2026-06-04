"""Handler de relatório de imóvel para proprietário e versão resumida."""
import logging
import re
from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

from overstreet.db.imoveis import _query_dict, _query_dicts
from overstreet.config import TIPO_MAP

log = logging.getLogger("overstreet.handlers.relatorio")
router = Router()


# ── Helpers de formatação ───────────────────────────────────────────────

def _fmt_moeda(val) -> str:
    """Formata valor numérico como moeda pt-BR."""
    if val is None:
        return "s/dados"
    try:
        num = float(str(val).replace(",", ".").replace(".", "", str(val).count(".") - 1) if "," in str(val) else float(val))
    except (ValueError, TypeError):
        return "s/dados"
    if num >= 1_000_000:
        return f"R$ {num / 1_000_000:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " mi"
    elif num >= 1_000:
        return f"R$ {num:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        return f"R$ {num:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_data(val: str) -> str:
    """Formata data ISO para DD/MM/YYYY."""
    if not val:
        return "s/dados"
    try:
        # Aceita formatos: YYYY-MM-DD HH:MM:SS ou YYYY-MM-DD
        val_clean = val[:19]
        dt = datetime.strptime(val_clean, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        try:
            dt = datetime.strptime(val[:10], "%Y-%m-%d")
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            return val[:10]


def _parse_sale_price(price_val) -> float | None:
    """Tenta converter sale_price para float. Aceita string com separadores pt-BR."""
    if price_val is None:
        return None
    s = str(price_val).strip()
    if not s or s in ("None", "0", ""):
        return None
    # Remove R$, espaços
    s = s.replace("R$", "").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        pass
    # Remove pontos de milhar e troca vírgula decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _dias_entre(data_iso: str) -> int:
    """Calcula dias entre data ISO e hoje."""
    if not data_iso:
        return 0
    try:
        dt = datetime.strptime(data_iso[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(data_iso[:10], "%Y-%m-%d")
        except ValueError:
            return 0
    delta = datetime.now() - dt
    return max(0, delta.days)


def _tipo_label(tipo_id) -> str:
    """Retorna nome do tipo de imóvel."""
    if tipo_id is None:
        return "Imóvel"
    try:
        return TIPO_MAP.get(int(tipo_id), "Imóvel")
    except (ValueError, TypeError):
        return "Imóvel"


# ── Queries de métricas ─────────────────────────────────────────────────

def _count_visitas(conn, imovel_id: int, status: str | None = None) -> int:
    """Conta visitas por status (ou todas se status=None)."""
    if status:
        row = conn.execute(
            "SELECT COUNT(*) FROM visitas WHERE imovel_id = ? AND status = ?",
            (imovel_id, status),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM visitas WHERE imovel_id = ?",
            (imovel_id,),
        ).fetchone()
    return row[0] if row else 0


def _count_favoritos(conn, imovel_id: int) -> int:
    """Conta quantos favoritos o imóvel tem."""
    row = conn.execute(
        "SELECT COUNT(*) FROM cliente_favoritos WHERE imovel_id = ?",
        (imovel_id,),
    ).fetchone()
    return row[0] if row else 0


def _avg_price_bairro(conn, district: str) -> float | None:
    """Preço médio de venda no bairro."""
    if not district:
        return None
    row = conn.execute(
        "SELECT AVG(sale_price) FROM imoveis "
        "WHERE district = ? AND sale_price IS NOT NULL "
        "AND sale_price != '' AND CAST(sale_price AS REAL) > 0",
        (district,),
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return None


# ── Geração de relatórios ───────────────────────────────────────────────

def _build_relatorio_proprietario(conn, imovel: dict) -> str:
    """Relatório completo para proprietário (com análise de mercado)."""
    imovel_id = imovel["id"]
    tipo = _tipo_label(imovel.get("tipo_imovel_id"))
    street = imovel.get("street") or imovel.get("reference") or "Sem endereço"
    neighborhood = imovel.get("district") or ""
    created_at = imovel.get("created_at", "")
    sale_price_raw = imovel.get("sale_price", "")
    owner_name = imovel.get("owner_name") or "Não informado"
    owner_phone = imovel.get("owner_phone") or ""

    # Métricas
    visitas_agendadas = _count_visitas(conn, imovel_id, "agendada")
    visitas_realizadas = _count_visitas(conn, imovel_id, "realizada")
    favoritos = _count_favoritos(conn, imovel_id)
    dias_mercado = _dias_entre(created_at)

    # Análise de mercado
    district_val = imovel.get("district")
    avg_bairro = _avg_price_bairro(conn, district_val) if district_val else None

    sale_price_num = _parse_sale_price(sale_price_raw)

    # Percentual de diferença vs média
    diff_pct_str = ""
    if sale_price_num and avg_bairro and avg_bairro > 0:
        diff = ((sale_price_num - avg_bairro) / avg_bairro) * 100
        if diff > 0:
            diff_pct_str = f" 🔺 +{diff:.1f}%"
        elif diff < 0:
            diff_pct_str = f" 🔻 {diff:.1f}%"
        else:
            diff_pct_str = " ➡️ 0%"

    lines = [
        f"📊 <b>RELATÓRIO DO IMÓVEL — COD {imovel_id}</b>",
        "",
        f"🏢 {tipo} — {street}, {neighborhood}" if neighborhood else f"🏢 {tipo} — {street}",
        f"📅 Cadastrado em: {_fmt_data(created_at)}",
        "",
        "<b>📈 DESEMPENHO:</b>",
        f"  📅 Visitas agendadas: {visitas_agendadas}",
        f"  ✅ Visitas realizadas: {visitas_realizadas}",
        f"  ⭐ Favoritos: {favoritos}",
        "",
        "<b>💡 ANÁLISE:</b>",
        f"  ⏱ Tempo no mercado: {dias_mercado} dias",
        f"  💰 Preço solicitado: {_fmt_moeda(sale_price_raw)}",
    ]

    if avg_bairro is not None:
        lines.append(f"  📊 Preço médio bairro: {_fmt_moeda(avg_bairro)}{diff_pct_str}")
    else:
        lines.append("  📊 Preço médio bairro: s/dados")

    lines.append("")
    lines.append("<b>📨 PROPRIETÁRIO:</b>")
    lines.append(f"  👤 {owner_name}")
    if owner_phone:
        lines.append(f"  📞 Tel: {owner_phone}")
    else:
        lines.append("  📞 Tel: não cadastrado")

    return "\n".join(lines)


def _build_relatorio_resumido(conn, imovel: dict) -> str:
    """Relatório resumido (sem análise de mercado)."""
    imovel_id = imovel["id"]
    tipo = _tipo_label(imovel.get("tipo_imovel_id"))
    street = imovel.get("street") or imovel.get("reference") or "Sem endereço"
    neighborhood = imovel.get("district") or ""
    created_at = imovel.get("created_at", "")

    visitas_total = _count_visitas(conn, imovel_id)
    visitas_agendadas = _count_visitas(conn, imovel_id, "agendada")
    visitas_realizadas = _count_visitas(conn, imovel_id, "realizada")
    favoritos = _count_favoritos(conn, imovel_id)
    dias_mercado = _dias_entre(created_at)

    lines = [
        f"📊 <b>RESUMO — COD {imovel_id}</b>",
        "",
        f"🏢 {tipo} — {street}, {neighborhood}" if neighborhood else f"🏢 {tipo} — {street}",
        "",
        "<b>📈 MÉTRICAS:</b>",
        f"  📅 Visitas agendadas: {visitas_agendadas}",
        f"  ✅ Visitas realizadas: {visitas_realizadas}",
        f"  📋 Total visitas: {visitas_total}",
        f"  ⭐ Favoritos: {favoritos}",
        f"  ⏱ Tempo no mercado: {dias_mercado} dias",
    ]

    return "\n".join(lines)


# ── Comandos ────────────────────────────────────────────────────────────

@router.message(Command("relatorio"))
async def cmd_relatorio(message: Message, tenant_conn=None, **kwargs):
    """Relatório de imóvel. Uso:
    /relatorio COD          → resumido
    /relatorio proprietario → lista imóveis com proprietário
    /relatorio proprietario COD → relatório completo para proprietário
    """
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    args = message.text.removeprefix("/relatorio").strip()
    parts = args.split(maxsplit=1)

    # ── /relatorio proprietario ──
    if parts and parts[0].lower() == "proprietario":
        if len(parts) >= 2 and parts[1].strip().isdigit():
            # /relatorio proprietario COD → relatório completo
            imovel_id = int(parts[1].strip())
            imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
            if not imovel:
                await message.answer(f"❌ Imóvel COD <b>{imovel_id}</b> não encontrado.",
                                    parse_mode=ParseMode.HTML)
                return
            texto = _build_relatorio_proprietario(tenant_conn, imovel)
            await message.answer(texto, parse_mode=ParseMode.HTML)
        else:
            # /relatorio proprietario → listar imóveis com owner_phone
            rows = _query_dicts(
                tenant_conn,
                "SELECT id, street, district, owner_name, owner_phone "
                "FROM imoveis WHERE owner_phone IS NOT NULL AND owner_phone != '' "
                "ORDER BY id DESC LIMIT 20",
            )
            if not rows:
                await message.answer(
                    "📋 Nenhum imóvel com telefone do proprietário cadastrado."
                )
                return

            buttons = []
            for r in rows:
                name = r.get("owner_name") or "Proprietário"
                addr = (r.get("street") or "")[:25]
                dist = r.get("district", "")[:15]
                label = f"COD {r['id']} — {name}"
                if addr or dist:
                    label += f" ({addr}, {dist})".rstrip(", ")
                if len(label) > 60:
                    label = label[:57] + "..."
                buttons.append([InlineKeyboardButton(
                    text=label,
                    callback_data=f"relatorio_{r['id']}",
                )])

            buttons.append([InlineKeyboardButton(
                text="❌ Cancelar",
                callback_data="relatorio_cancelar",
            )])

            await message.answer(
                "📋 <b>Imóveis com proprietário cadastrado:</b>\n\n"
                "Selecione para gerar relatório:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                parse_mode=ParseMode.HTML,
            )
        return

    # ── /relatorio COD → resumido ──
    if parts and parts[0].isdigit():
        imovel_id = int(parts[0])
        imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
        if not imovel:
            await message.answer(f"❌ Imóvel COD <b>{imovel_id}</b> não encontrado.",
                                parse_mode=ParseMode.HTML)
            return
        texto = _build_relatorio_resumido(tenant_conn, imovel)
        await message.answer(texto, parse_mode=ParseMode.HTML)
        return

    # ── /relatorio sem argumentos → ajuda ──
    await message.answer(
        "📊 <b>Relatório de Imóvel</b>\n\n"
        "Uso:\n"
        "• <code>/relatorio COD</code> — resumo rápido\n"
        "• <code>/relatorio proprietario</code> — listar imóveis com proprietário\n"
        "• <code>/relatorio proprietario COD</code> — relatório completo",
        parse_mode=ParseMode.HTML,
    )


# ── Callbacks ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("relatorio_"))
async def on_relatorio_callback(callback: CallbackQuery, tenant_conn=None, **kwargs):
    """Callback para gerar relatório via botão inline."""
    data = callback.data

    if data == "relatorio_cancelar":
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.answer("Cancelado.")
        return

    # relatorio_{imovel_id} → relatório completo para proprietário
    try:
        imovel_id = int(data.removeprefix("relatorio_"))
    except ValueError:
        await callback.answer("Erro: dados inválidos.", show_alert=True)
        return

    if not tenant_conn:
        await callback.answer("❌ Erro de conexão.", show_alert=True)
        return

    imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (imovel_id,))
    if not imovel:
        await callback.answer("Imóvel não encontrado.", show_alert=True)
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    texto = _build_relatorio_proprietario(tenant_conn, imovel)
    await callback.message.answer(texto, parse_mode=ParseMode.HTML)
    await callback.answer("✅ Relatório gerado!")
