"""Formatação de cards de imóveis e teclados inline."""
import re
from urllib.parse import quote
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from overstreet.config import TIPO_MAP


def _v(val, default="—") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return default if s in ("", "None", "null", "0", "0.00", "0,01") else s


def clean_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone)
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def build_address_query(row: dict) -> str:
    parts = []
    if row.get("street"):
        s = row["street"]
        if _v(row.get("number")) != "—":
            s += f", {row['number']}"
        parts.append(s)
    if row.get("district") and _v(row["district"]) != "—":
        parts.append(row["district"])
    if row.get("city") and _v(row["city"]) != "—":
        parts.append(row["city"])
    if row.get("zip") and _v(row["zip"]) != "—":
        parts.append(f"CEP {row['zip']}")
    return ", ".join(parts) if parts else "São Paulo, SP, Brasil"


def parse_codigo(text: str) -> int | None:
    text = text.strip()
    m = re.match(r'^(?:codigo|cod|cód)[\s:]+(\d{4,})$', text.lower())
    if m:
        return int(m.group(1))
    if re.match(r'^\d{4,}$', text):
        return int(text)
    return None


def parse_location_query(text: str) -> dict | None:
    m = re.search(
        r'im[oó]veis?\s+(?:em|perto\s+de|próximo\s+(?:a|de|ao?))\s+(.+?)(?:\s+no\s+raio|\s+num\s+raio|$)',
        text.lower()
    )
    if not m:
        return None
    place = m.group(1).strip()
    radius_match = re.search(r'(\d+(?:\.\d+)?)\s*km', text.lower())
    radius = float(radius_match.group(1)) if radius_match else 5.0
    return {"place": place, "radius_km": radius}


def format_card(row: dict, score: float | None = None) -> str:
    """Card HTML completo para Telegram (HTML parse mode)."""
    L = []

    # Header
    cod = row.get("id", "")
    tipo_id = row.get("tipo_imovel_id") or row.get("property_type")
    tipo = ""
    if tipo_id:
        try:
            tipo = TIPO_MAP.get(int(tipo_id), "")
        except (ValueError, TypeError):
            pass
    fin = row.get("finalidade", "")
    sit = row.get("situacao", "")
    h = f"<b>COD {cod}</b>"
    if tipo:
        h += f" · <b>{tipo}</b>"
    if fin:
        h += f" · <b>{fin}</b>"
    if sit:
        h += f" · <b>{sit}</b>"
    L.append(h)
    L.append("───────────────")

    # Endereço
    end = []
    if row.get("street"):
        s = row["street"]
        if _v(row.get("number")) != "—":
            s += f", {row['number']}"
        end.append(s)
    if row.get("complement") and _v(row["complement"]) != "—":
        end.append(f"Comp {row['complement']}")
    if row.get("apartment") and _v(row["apartment"]) != "—":
        end.append(f"Apto {row['apartment']}")
    bairro = row.get("district", "")
    if bairro and _v(bairro) != "—":
        end.append(bairro)
    loc = []
    if row.get("city") and _v(row["city"]) != "—":
        loc.append(row["city"])
    if row.get("state") and _v(row["state"]) != "—":
        loc.append(row["state"])
    if loc:
        end.append(" - ".join(loc))
    cep = row.get("zip", "")
    if cep and _v(cep) != "—":
        end.append(f"CEP {cep}")
    ref = row.get("reference", "")
    if ref and _v(ref) != "—":
        end.append(f"Ref {ref}")
    if end:
        L.append(f"<b>End</b> {' · '.join(end)}")

    # Técnicos
    t = []
    if row.get("bedrooms") is not None:
        t.append(f"<b>Dorm</b> {row['bedrooms']}")
    if row.get("suites") is not None:
        t.append(f"<b>Suítes</b> {row['suites']}")
    if row.get("bathrooms") is not None:
        t.append(f"<b>Banh</b> {row['bathrooms']}")
    if row.get("salas") is not None:
        t.append(f"<b>Salas</b> {row['salas']}")
    if row.get("garage") is not None:
        t.append(f"<b>Vagas</b> {row['garage']}")
    if _v(row.get("area_util")) != "—":
        t.append(f"<b>Útil</b> {row['area_util']}m²")
    if _v(row.get("built_area")) != "—":
        t.append(f"<b>Constr</b> {row['built_area']}m²")
    if _v(row.get("land_area")) != "—":
        t.append(f"<b>Terreno</b> {row['land_area']}m²")
    if t:
        L.append(f"<b>Tec</b> {' · '.join(str(x) for x in t)}")

    # Valores
    v = []
    if _v(row.get("sale_price")) != "—":
        v.append(f"<b>Venda</b> R$ {row['sale_price']}")
    if _v(row.get("rental_price")) != "—":
        v.append(f"<b>Aluguel</b> R$ {row['rental_price']}")
    if _v(row.get("condo_fee")) != "—":
        v.append(f"<b>Cond</b> R$ {row['condo_fee']}")
    if _v(row.get("iptu")) != "—":
        v.append(f"<b>IPTU</b> R$ {row['iptu']}")
    if v:
        L.append(f"$$ {' · '.join(v)}")

    # Proprietário
    p = []
    if _v(row.get("owner_name")) != "—":
        p.append(f"<i>{row['owner_name']}</i>")
    phones = []
    if _v(row.get("owner_phone")) != "—":
        phones.append(f"<i>{row['owner_phone']}</i>")
    if _v(row.get("owner_mobile")) != "—":
        phones.append(f"<i>{row['owner_mobile']}</i>")
    if phones:
        p.append(" · ".join(phones))
    if _v(row.get("owner_email")) != "—":
        p.append(f"<i>{row['owner_email']}</i>")
    if p:
        L.append(f"<b>Prop</b> {' · '.join(p)}")

    # Descrição
    desc = row.get("description", "")
    if desc and _v(desc) != "—":
        dc = desc.replace("\r\n", " ").replace("\n", " ").strip()
        if len(dc) > 300:
            dc = dc[:300] + "…"
        L.append(f"<i>{dc}</i>")

    # Meta
    meta = []
    if row.get("agencia_id"):
        meta.append(f"Ag {row['agencia_id']}")
    if _v(row.get("created_at")) != "—":
        meta.append(f"Cad {row['created_at']}")
    if _v(row.get("updated_at")) != "—":
        meta.append(f"Atual {row['updated_at']}")
    if meta:
        L.append(f"<i>{' · '.join(meta)}</i>")

    if score is not None:
        L.append(f"<i>Relevância {score:.0%}</i>")

    return "\n".join(L)


def format_card_plain(row: dict) -> str:
    """Versão sem HTML para WhatsApp/email (texto plano, truncado)."""
    parts = []
    cod = row.get("id", "")
    tipo_id = row.get("tipo_imovel_id") or row.get("property_type")
    tipo = ""
    if tipo_id:
        try:
            tipo = TIPO_MAP.get(int(tipo_id), "")
        except (ValueError, TypeError):
            pass
    header = f"COD {cod}"
    if tipo:
        header += f" · {tipo}"
    if row.get("finalidade"):
        header += f" · {row['finalidade']}"
    parts.append(header)

    if row.get("street"):
        addr = row["street"]
        if _v(row.get("number")) != "—":
            addr += f", {row['number']}"
        if row.get("district") and _v(row["district"]) != "—":
            addr += f" - {row['district']}"
        if row.get("city") and _v(row["city"]) != "—":
            addr += f" - {row['city']}"
        parts.append(addr)

    specs = []
    if row.get("bedrooms") is not None:
        specs.append(f"{row['bedrooms']} dorm")
    if row.get("suites") is not None:
        specs.append(f"{row['suites']} suítes")
    if row.get("garage") is not None:
        specs.append(f"{row['garage']} vagas")
    if specs:
        parts.append(" · ".join(specs))

    if _v(row.get("sale_price")) != "—":
        parts.append(f"Venda: R$ {row['sale_price']}")
    if _v(row.get("rental_price")) != "—":
        parts.append(f"Aluguel: R$ {row['rental_price']}")

    desc = row.get("description", "")
    if desc and _v(desc) != "—":
        dc = desc.replace("\r\n", " ").replace("\n", " ").strip()
        parts.append(dc[:200] + "…" if len(dc) > 200 else dc)

    return "\n".join(parts)


def format_card_shareable(row: dict) -> str:
    """Versão do card para compartilhamento: sem dados sensíveis do proprietário."""
    L = []

    # Header
    cod = row.get("id", "")
    tipo_id = row.get("tipo_imovel_id") or row.get("property_type")
    tipo = ""
    if tipo_id:
        try:
            tipo = TIPO_MAP.get(int(tipo_id), "")
        except (ValueError, TypeError):
            pass
    fin = row.get("finalidade", "")
    sit = row.get("situacao", "")
    h = f"<b>COD {cod}</b>"
    if tipo:
        h += f" · <b>{tipo}</b>"
    if fin:
        h += f" · <b>{fin}</b>"
    L.append(h)
    L.append("───────────────")

    # Endereço
    end = []
    if row.get("street"):
        s = row["street"]
        if _v(row.get("number")) != "—":
            s += f", {row['number']}"
        end.append(s)
    bairro = row.get("district", "")
    if bairro and _v(bairro) != "—":
        end.append(bairro)
    loc = []
    if row.get("city") and _v(row["city"]) != "—":
        loc.append(row["city"])
    if row.get("state") and _v(row["state"]) != "—":
        loc.append(row["state"])
    if loc:
        end.append(" - ".join(loc))
    if end:
        L.append(f"{' · '.join(end)}")

    # Técnicos
    t = []
    if row.get("bedrooms") is not None:
        t.append(f"{row['bedrooms']} dorm")
    if row.get("suites") is not None:
        t.append(f"{row['suites']} suítes")
    if row.get("bathrooms") is not None:
        t.append(f"{row['bathrooms']} banh")
    if row.get("garage") is not None:
        t.append(f"{row['garage']} vagas")
    if _v(row.get("area_util")) != "—":
        t.append(f"{row['area_util']}m² útil")
    if _v(row.get("built_area")) != "—":
        t.append(f"{row['built_area']}m² constr")
    if t:
        L.append(" · ".join(t))

    # Valores
    v = []
    if _v(row.get("sale_price")) != "—":
        v.append(f"<b>Venda</b> R$ {row['sale_price']}")
    if _v(row.get("rental_price")) != "—":
        v.append(f"<b>Aluguel</b> R$ {row['rental_price']}")
    if _v(row.get("condo_fee")) != "—":
        v.append(f"Condomínio R$ {row['condo_fee']}")
    if v:
        L.append(" · ".join(v))

    # Mapa link
    addr = build_address_query(row)
    encoded_addr = quote(addr)
    L.append(f'<a href="https://www.google.com/maps/search/?api=1&query={encoded_addr}">📍 Ver no Google Maps</a>')

    return "\n".join(L)


def build_card_keyboard(row: dict, user_id: int,
                        _user_locations: dict | None = None) -> InlineKeyboardMarkup:
    """Botões inline: WhatsApp, Email, Mapa, Rota."""
    buttons = []
    imovel_id = row.get("id", "")
    addr_query = build_address_query(row)
    encoded_addr = quote(addr_query)

    row1 = []
    phone = row.get("owner_mobile") or row.get("owner_phone") or ""
    wa_number = clean_phone(phone)
    if wa_number:
        row1.append(InlineKeyboardButton(
            text="WhatsApp",
            url=f"https://wa.me/{wa_number}"
        ))
    email = row.get("owner_email", "")
    if email and _v(email) != "—":
        row1.append(InlineKeyboardButton(
            text="Email",
            callback_data=f"email_{imovel_id}"
        ))
    if row1:
        buttons.append(row1)

    row2 = []
    row2.append(InlineKeyboardButton(
        text="Mapa",
        url=f"https://www.google.com/maps/search/?api=1&query={encoded_addr}"
    ))
    user_loc = (_user_locations or {}).get(user_id)
    if user_loc:
        lat, lon = user_loc
        row2.append(InlineKeyboardButton(
            text="Rota",
            url=f"https://www.google.com/maps/dir/?api=1&origin={lat},{lon}&destination={encoded_addr}"
        ))
    else:
        row2.append(InlineKeyboardButton(
            text="Rota",
            callback_data=f"route_{imovel_id}"
        ))
    buttons.append(row2)

    row3 = []
    row3.append(InlineKeyboardButton(
        text="💾 Salvar",
        callback_data=f"save_{imovel_id}"
    ))
    row3.append(InlineKeyboardButton(
        text="📝 Proposta",
        callback_data=f"proposta_{imovel_id}"
    ))
    row3.append(InlineKeyboardButton(
        text="📤 Compartilhar",
        callback_data=f"share_{imovel_id}"
    ))
    buttons.append(row3)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_client_send_keyboard(imovel: dict, cliente: dict,
                                agente_nome: str, agente_telefone: str = "") -> InlineKeyboardMarkup:
    """Botões para envio de imóvel ao cliente: WhatsApp link."""
    buttons = []

    # WhatsApp ao cliente
    wa_client = clean_phone(cliente.get("whatsapp", ""))
    if wa_client:
        card_plain = format_card_plain(imovel)
        wa_text = (
            f"Olá! A {agente_nome} separou este imóvel especialmente para você!\n\n"
            f"{card_plain[:300]}"
        )
        if agente_telefone:
            digits = clean_phone(agente_telefone)
            wa_text += f"\n\nFale com {agente_nome}: wa.me/{digits}"
        encoded = quote(wa_text[:1500])
        buttons.append([InlineKeyboardButton(
            text=f"📱 Enviar WhatsApp para {cliente['nome']}",
            url=f"https://wa.me/{wa_client}?text={encoded}"
        )])

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
