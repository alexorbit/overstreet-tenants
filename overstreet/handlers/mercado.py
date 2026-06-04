"""Handler de inteligência de mercado — estatísticas por bairro."""
import sqlite3
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


def _fmt_moeda(val) -> str:
    """Formata valor numérico como moeda pt-BR (R$ 350.000)."""
    if val is None:
        return "—"
    try:
        num = float(val)
    except (ValueError, TypeError):
        return "—"
    if num >= 1_000_000:
        return f"R$ {num / 1_000_000:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " mi"
    elif num >= 1_000:
        formatted = f"{num / 1_000:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {formatted}.000"
    else:
        return f"R$ {num:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_m2(val) -> str:
    """Formata preço por m²."""
    if val is None:
        return "—"
    try:
        num = float(val)
    except (ValueError, TypeError):
        return "—"
    if num >= 10_000:
        return f"R$ {num / 1_000:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".") + ".000/m²"
    else:
        return f"R$ {num:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".") + "/m²"


def _mercado_bairro(conn: sqlite3.Connection, bairro: str) -> str:
    """Estatísticas de mercado para um bairro específico."""
    like_bairro = f"%{bairro}%"

    # Contagem total no bairro
    row = conn.execute(
        "SELECT COUNT(*) FROM imoveis WHERE district LIKE ?", (like_bairro,)
    ).fetchone()
    total = row[0] if row else 0

    if total == 0:
        return f"📊 Nenhum imóvel encontrado para o bairro <b>{bairro}</b>."

    lines: list[str] = []
    lines.append(f"📊 <b>Inteligência de Mercado — {bairro.title()}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📦 Total de imóveis: <b>{total:,}</b>".replace(",", "."))
    lines.append("")

    # Contagem por tipo
    tipos = conn.execute(
        "SELECT tipo_imovel_id, COUNT(*) as cnt FROM imoveis "
        "WHERE district LIKE ? GROUP BY tipo_imovel_id ORDER BY cnt DESC LIMIT 5",
        (like_bairro,)
    ).fetchall()
    if tipos:
        from overstreet.config import TIPO_MAP
        lines.append("<b>🏢 Por tipo:</b>")
        for tid, cnt in tipos:
            name = TIPO_MAP.get(tid, f"Tipo {tid}")
            pct = cnt * 100 // total
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            lines.append(f"  {name}: {cnt:,} ({pct}%) {bar}".replace(",", "."))
        lines.append("")

    # Preço de venda — faixa
    venda_stats = conn.execute(
        "SELECT MIN(sale_price), MAX(sale_price), AVG(sale_price) "
        "FROM imoveis WHERE district LIKE ? AND sale_price IS NOT NULL "
        "AND sale_price != '' AND CAST(sale_price AS REAL) > 0",
        (like_bairro,)
    ).fetchone()
    if venda_stats and venda_stats[0] is not None:
        lines.append("<b>💰 Venda:</b>")
        lines.append(f"  Mínimo: {_fmt_moeda(venda_stats[0])}")
        lines.append(f"  Máximo: {_fmt_moeda(venda_stats[1])}")
        lines.append(f"  Média: {_fmt_moeda(venda_stats[2])}")
        lines.append("")

    # Preço de locação — faixa
    aluguel_stats = conn.execute(
        "SELECT MIN(rental_price), MAX(rental_price), AVG(rental_price) "
        "FROM imoveis WHERE district LIKE ? AND rental_price IS NOT NULL "
        "AND rental_price != '' AND CAST(rental_price AS REAL) > 0",
        (like_bairro,)
    ).fetchone()
    if aluguel_stats and aluguel_stats[0] is not None:
        lines.append("<b>🔑 Locação:</b>")
        lines.append(f"  Mínimo: {_fmt_moeda(aluguel_stats[0])}")
        lines.append(f"  Máximo: {_fmt_moeda(aluguel_stats[1])}")
        lines.append(f"  Média: {_fmt_moeda(aluguel_stats[2])}")
        lines.append("")

    # Preço por m² (venda)
    m2_venda = conn.execute(
        "SELECT AVG(CAST(sale_price AS REAL) / CASE WHEN area_util > 0 THEN area_util "
        "WHEN built_area > 0 THEN built_area ELSE NULL END) "
        "FROM imoveis WHERE district LIKE ? AND sale_price IS NOT NULL "
        "AND sale_price != '' AND CAST(sale_price AS REAL) > 0 "
        "AND (area_util > 0 OR built_area > 0)",
        (like_bairro,)
    ).fetchone()
    if m2_venda and m2_venda[0] is not None:
        lines.append(f"<b>📐 Preço/m² venda:</b> {_fmt_m2(m2_venda[0])}")
        lines.append("")

    # Preço por m² (locação)
    m2_aluguel = conn.execute(
        "SELECT AVG(CAST(rental_price AS REAL) / CASE WHEN area_util > 0 THEN area_util "
        "WHEN built_area > 0 THEN built_area ELSE NULL END) "
        "FROM imoveis WHERE district LIKE ? AND rental_price IS NOT NULL "
        "AND rental_price != '' AND CAST(rental_price AS REAL) > 0 "
        "AND (area_util > 0 OR built_area > 0)",
        (like_bairro,)
    ).fetchone()
    if m2_aluguel and m2_aluguel[0] is not None:
        lines.append(f"<b>📐 Preço/m² locação:</b> {_fmt_m2(m2_aluguel[0])}")
        lines.append("")

    # Comparar com média geral da cidade
    cidade_row = conn.execute(
        "SELECT city FROM imoveis WHERE district LIKE ? "
        "AND city IS NOT NULL AND city != '' LIMIT 1",
        (like_bairro,)
    ).fetchone()
    if cidade_row and cidade_row[0]:
        cidade = cidade_row[0]
        like_cidade = f"%{cidade}%"

        cidade_avg = conn.execute(
            "SELECT AVG(sale_price) FROM imoveis WHERE city LIKE ? "
            "AND sale_price IS NOT NULL AND sale_price != '' "
            "AND CAST(sale_price AS REAL) > 0",
            (like_cidade,)
        ).fetchone()

        if cidade_avg and cidade_avg[0] is not None and venda_stats and venda_stats[2] is not None:
            bairro_avg = float(venda_stats[2])
            cidade_avg_val = float(cidade_avg[0])
            diff_pct = ((bairro_avg - cidade_avg_val) / cidade_avg_val) * 100 if cidade_avg_val > 0 else 0
            if diff_pct > 0:
                comparison = f"📈 <b>+{diff_pct:.1f}%</b> acima da média de {cidade}"
            elif diff_pct < 0:
                comparison = f"📉 <b>{diff_pct:.1f}%</b> abaixo da média de {cidade}"
            else:
                comparison = f"➡️ Na média de {cidade}"
            lines.append(f"<b>Comparativo:</b> {comparison}")
            lines.append("")

    lines.append(f"<i>Dados do bairro {bairro.title()}</i>")
    return "\n".join(lines)


def _mercado_top(conn: sqlite3.Connection) -> str:
    """Visão geral: top 10 bairros com mais imóveis e preço médio."""
    lines: list[str] = []
    lines.append("📊 <b>Inteligência de Mercado — Visão Geral</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Total geral
    total_row = conn.execute("SELECT COUNT(*) FROM imoveis").fetchone()
    total = total_row[0] if total_row else 0
    if total == 0:
        return "📊 Nenhum imóvel cadastrado para análise de mercado."

    lines.append(f"📦 Total no portfólio: <b>{total:,}</b>".replace(",", "."))
    lines.append("")

    # Top 10 bairros
    bairros = conn.execute(
        "SELECT district, COUNT(*) as cnt, "
        "AVG(CASE WHEN sale_price IS NOT NULL AND sale_price != '' "
        "AND CAST(sale_price AS REAL) > 0 THEN CAST(sale_price AS REAL) END) as avg_venda, "
        "AVG(CASE WHEN rental_price IS NOT NULL AND rental_price != '' "
        "AND CAST(rental_price AS REAL) > 0 THEN CAST(rental_price AS REAL) END) as avg_aluguel "
        "FROM imoveis WHERE district IS NOT NULL AND district != '' "
        "GROUP BY district ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    if bairros:
        lines.append("<b>🏆 Top 10 Bairros:</b>")
        lines.append("")
        for i, (bairro, cnt, avg_v, avg_a) in enumerate(bairros, 1):
            name = bairro.title() if bairro else "—"
            venda_str = _fmt_moeda(avg_v) if avg_v else "—"
            aluguel_str = _fmt_moeda(avg_a) if avg_a else "—"
            pct = cnt * 100 // total
            lines.append(
                f"  <b>{i}.</b> {name}\n"
                f"     📦 {cnt:,} imóveis ({pct}%)\n"
                f"     💰 Venda média: {venda_str}\n"
                f"     🔑 Aluguel médio: {aluguel_str}"
            )
            lines.append("")

    # Preço/m² geral
    m2_geral = conn.execute(
        "SELECT AVG(CAST(sale_price AS REAL) / CASE WHEN area_util > 0 THEN area_util "
        "WHEN built_area > 0 THEN built_area ELSE NULL END) "
        "FROM imoveis WHERE sale_price IS NOT NULL AND sale_price != '' "
        "AND CAST(sale_price AS REAL) > 0 AND (area_util > 0 OR built_area > 0)"
    ).fetchone()
    if m2_geral and m2_geral[0] is not None:
        lines.append(f"<b>📐 Preço/m² médio geral:</b> {_fmt_m2(m2_geral[0])}")
        lines.append("")

    lines.append("<i>Use /mercado <bairro> para detalhes específicos</i>")
    return "\n".join(lines)


@router.message(Command("mercado"))
async def cmd_mercado(message: Message, tenant_conn=None, **kwargs):
    """Inteligência de mercado. Uso: /mercado [bairro]"""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    args = message.text.removeprefix("/mercado").strip()
    if not args:
        texto = _mercado_top(tenant_conn)
    else:
        texto = _mercado_bairro(tenant_conn, args)

    await message.answer(texto, parse_mode="HTML")
