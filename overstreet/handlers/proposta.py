"""Handler de proposta padronizada para imóveis."""
import re
import sqlite3
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

router = Router()


def _fmt_moeda(val) -> str:
    """Formata valor numérico como moeda pt-BR (R$ 350.000)."""
    if val is None:
        return "R$ ___"
    s = str(val).strip()
    if not s or s in ("", "None", "0", "0.00", "0,00"):
        return "R$ ___"
    # Se já tem pontuação, tentar limpar e reformatar
    digits = re.sub(r'[^\d]', '', s)
    if not digits:
        return "R$ ___"
    try:
        num = int(digits)
    except ValueError:
        return f"R$ {s}"
    if num >= 1_000_000:
        return f"R$ {num / 1_000_000:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".") + " mi"
    elif num >= 1_000:
        return f"R$ {num / 1_000:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".") + ".000"
    else:
        return f"R$ {num}"


def _fmt_area(val) -> str:
    """Formata área numérica."""
    if val is None:
        return "___ m²"
    s = str(val).strip()
    return f"{s} m²" if s and s not in ("", "None", "0", "0.0") else "___ m²"


def gerar_proposta(imovel: dict, cliente: dict | None = None) -> str:
    """Gera texto HTML de proposta padronizada."""
    from overstreet.config import TIPO_MAP

    # Tipo do imóvel
    tipo_id = imovel.get("tipo_imovel_id") or imovel.get("property_type")
    tipo = ""
    if tipo_id:
        try:
            tipo = TIPO_MAP.get(int(tipo_id), "")
        except (ValueError, TypeError):
            pass

    # Endereço
    end_parts = []
    if imovel.get("street"):
        s = imovel["street"]
        num = imovel.get("number", "")
        if num and str(num).strip() not in ("", "None", "0"):
            s += f", {num}"
        end_parts.append(s)
    bairro = imovel.get("district", "")
    if bairro and str(bairro).strip() not in ("", "None"):
        end_parts.append(bairro)
    cidade = imovel.get("city", "")
    if cidade and str(cidade).strip() not in ("", "None"):
        end_parts.append(cidade)
    estado = imovel.get("state", "SP")
    if estado and str(estado).strip() not in ("", "None"):
        end_parts.append(estado)
    endereco = ", ".join(end_parts) if end_parts else "___"

    # Dormitórios
    dorms = imovel.get("bedrooms")
    dorm_str = str(dorms) if dorms is not None else "___"

    # Área
    area = imovel.get("area_util") or imovel.get("built_area")
    area_str = _fmt_area(area)

    # Preço venda
    preco_venda = _fmt_moeda(imovel.get("sale_price"))

    # Cliente
    if cliente:
        cliente_nome = cliente.get("nome", "___")
    else:
        cliente_nome = "___"

    # Montar proposta
    L = []
    L.append("📝 <b>PROPOSTA DE IMÓVEL</b>")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("")
    L.append("<b>DADOS DO IMÓVEL</b>")
    L.append(f"  Endereço: {endereco}")
    L.append(f"  Tipo: {tipo or '___'}")
    L.append(f"  Dormitórios: {dorm_str}")
    L.append(f"  Área útil: {area_str}")
    L.append(f"  Preço de venda: {preco_venda}")
    L.append("")
    L.append("<b>VALORES ADICIONAIS</b>")
    L.append(f"  Condomínio: R$ ___")
    L.append(f"  IPTU: R$ ___")
    L.append("")
    L.append("<b>CONDIÇÕES DE PAGAMENTO</b>")
    L.append(f"  Forma de pagamento: ___")
    L.append(f"  Valor da entrada: R$ ___")
    L.append(f"  Financiamento: ___")
    L.append("")
    L.append("<b>PARTES ENVOLVIDAS</b>")
    L.append(f"  Cliente: {cliente_nome}")
    L.append(f"  Corretor: ___")
    L.append(f"  CRECI: ___")
    L.append("")
    L.append(f"  Imobiliária: ___")
    L.append(f"  Data: ___/___/______")
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    L.append("<i>Assinatura do Corretor: _________________</i>")
    L.append("")
    L.append("<i>Assinatura do Cliente: _________________</i>")

    return "\n".join(L)


@router.message(Command("proposta"))
async def cmd_proposta(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Gera proposta padronizada para um imóvel. Uso: /proposta COD ou /proposta COD cliente Nome."""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    from overstreet.db.imoveis import _query_dict
    from overstreet.db.clientes import list_clientes

    args = message.text.removeprefix("/proposta").strip()
    parts = args.split(maxsplit=1)

    if not parts or not parts[0]:
        await message.answer(
            "📝 Use: <code>/proposta COD</code> ou <code>/proposta COD cliente Nome</code>\n\n"
            "Exemplo:\n"
            "  /proposta 270034\n"
            "  /proposta 270034 cliente João Silva",
            parse_mode="HTML"
        )
        return

    try:
        codigo = int(parts[0].strip())
    except ValueError:
        await message.answer("❌ Código inválido. Use apenas números.")
        return

    # Buscar imóvel
    imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (codigo,))
    if not imovel:
        await message.answer(f"❌ Imóvel COD {codigo} não encontrado.")
        return

    # Buscar cliente se especificado
    cliente = None
    if len(parts) > 1:
        resto = parts[1].strip()
        # Formato: "cliente Nome do Cliente"
        if resto.lower().startswith("cliente"):
            nome_busca = resto[len("cliente"):].strip()
            if nome_busca:
                clientes = list_clientes(tenant_conn)
                # Buscar por nome parcial (case insensitive)
                match = None
                for c in clientes:
                    if nome_busca.lower() in c["nome"].lower():
                        match = c
                        break
                if match:
                    cliente = match
                else:
                    await message.answer(
                        f"⚠️ Cliente '{nome_busca}' não encontrado. "
                        f"Gerando proposta sem dados do cliente."
                        )

    texto = gerar_proposta(dict(imovel), cliente)
    await message.answer(texto, parse_mode="HTML")


@router.message(F.text.func(lambda t: any(
    kw in t.lower() for kw in [
        "proposta de venda", "proposta de imóvel", "proposta de imovel",
        "preencher proposta", "gerar proposta", "fazer proposta",
        "proposta venda", "proposta imovel",
    ]
)))
async def cmd_proposta_natural(message: Message, state: FSMContext,
                                tenant_conn=None, tenant=None, **kwargs):
    """Gera proposta a partir de linguagem natural. Pede COD se necessário."""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    from overstreet.db.imoveis import _query_dict
    from overstreet.db.clientes import list_clientes

    text = message.text.lower()

    # Tentar extrair COD do texto
    import re
    cod_match = re.search(r'\b(\d{5,})\b', text)

    if cod_match:
        codigo = int(cod_match.group(1))
        imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (codigo,))
        if not imovel:
            await message.answer(f"❌ Imóvel COD {codigo} não encontrado.")
            return
        texto = gerar_proposta(dict(imovel))
        await message.answer(texto, parse_mode="HTML")
    else:
        # Sem COD — pedir ao corretor
        await message.answer(
            "📝 Qual é o código do imóvel?\n\n"
            "Exemplo: <code>270034</code>",
            parse_mode="HTML"
        )
        await state.set_state("aguardando_proposta_cod")
        await state.update_data(ultimo_comando="proposta")
