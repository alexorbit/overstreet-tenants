"""Handler de busca por proximidade GPS — Haversine."""
import math
import sqlite3
import logging
from aiogram import Router, F
from aiogram.types import Message

router = Router()
log = logging.getLogger("overstreet.handlers.proximidade")

# Raio padrão em km
DEFAULT_RADIUS_KM = 5.0

# Raio da Terra em km
EARTH_RADIUS_KM = 6371.0

# Coeficiente de conversão graus -> km (aprox para lat/lon em São Paulo ~-23.5)
DEG_TO_KM = 111.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância em km entre dois pontos (Haversine)."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


def _busca_por_coordenadas(conn: sqlite3.Connection, lat: float, lon: float,
                            radius_km: float = DEFAULT_RADIUS_KM,
                            limit: int = 5) -> list[dict]:
    """Busca imóveis próximos usando fórmula Haversine no SQL."""
    # Converter raio km para graus decimais (bounding box primeiro, depois Haversine)
    lat_delta = radius_km / DEG_TO_KM
    lon_delta = radius_km / (DEG_TO_KM * math.cos(math.radians(lat)))

    sql = """
        SELECT *,
               (? * 2 * ASIN(
                   SQRT(
                       POW(SIN(RADIANS(latitude - ?) / 2), 2) +
                       COS(RADIANS(?)) * COS(RADIANS(latitude)) *
                       POW(SIN(RADIANS(longitude - ?) / 2), 2)
                   )
               )) AS distancia_km
        FROM imoveis
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          AND latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
          AND situacao = 'Disponivel'
        ORDER BY distancia_km ASC
        LIMIT ?
    """
    params = (
        EARTH_RADIUS_KM, lat, lat, lon,
        lat - lat_delta, lat + lat_delta,
        lon - lon_delta, lon + lon_delta,
        limit,
    )

    cursor = conn.execute(sql, params)
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _busca_por_bairro(conn: sqlite3.Connection, lat: float, lon: float,
                       limit: int = 5) -> list[dict] | None:
    """Fallback: reverse geocoding via Nominatim e busca por bairro/cidade."""
    import urllib.request
    import json

    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=pt-BR"
        req = urllib.request.Request(url, headers={"User-Agent": "OverStreet-Bot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())

        address = data.get("address", {})
        bairro = address.get("suburb") or address.get("neighbourhood") or ""
        cidade = address.get("city") or address.get("town") or address.get("municipality") or ""

        where_parts = []
        params = []
        if bairro:
            where_parts.append("district LIKE ?")
            params.append(f"%{bairro}%")
        if cidade:
            where_parts.append("city LIKE ?")
            params.append(f"%{cidade}%")

        if not where_parts:
            return None

        where = " AND ".join(where_parts)
        params.append(limit)

        sql = f"""
            SELECT * FROM imoveis
            WHERE ({where}) AND situacao = 'Disponivel'
            ORDER BY RANDOM()
            LIMIT ?
        """
        cursor = conn.execute(sql, params)
        if not cursor.description:
            return []
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    except Exception as e:
        log.warning("Reverse geocoding falhou: %s", e)
        return None


def _format_resumo(row: dict, distancia: float | None = None) -> str:
    """Card resumido com distância."""
    from overstreet.config import TIPO_MAP
    from overstreet.formatters.card import _v

    cod = row.get("id", "")
    tipo_id = row.get("tipo_imovel_id") or row.get("property_type")
    tipo = ""
    if tipo_id:
        try:
            tipo = TIPO_MAP.get(int(tipo_id), "")
        except (ValueError, TypeError):
            pass

    L = [f"<b>COD {cod}</b>"]
    if tipo:
        L.append(f"  Tipo: {tipo}")

    end = []
    if row.get("street"):
        s = row["street"]
        if _v(row.get("number")) != "—":
            s += f", {row['number']}"
        end.append(s)
    if row.get("district") and _v(row["district"]) != "—":
        end.append(row["district"])
    if row.get("city") and _v(row["city"]) != "—":
        end.append(row["city"])
    if end:
        L.append(f"  📍 {' - '.join(end)}")

    if row.get("bedrooms") is not None:
        L.append(f"  🛏️ {row['bedrooms']} dorm")

    if _v(row.get("sale_price")) != "—":
        L.append(f"  💰 R$ {row['sale_price']}")

    if distancia is not None:
        L.append(f"  📏 {distancia:.1f} km de você")

    L.append(f"  <code>{cod}</code>")
    return "\n".join(L)


# NOTE: F.location handler is in search.py (on_location) — avoids duplicate registration


@router.message(F.text.func(lambda t: any(
    kw in t.lower() for kw in [
        "imóveis próximos", "imoveis proximos", "proximos",
        "próximo a mim", "proximo a mim", "perto de mim",
        "imóveis perto", "imoveis perto", "buscar próximos",
    ]
)))
async def on_texto_proximos(message: Message, tenant_conn=None, **kwargs):
    """Busca imóveis próximos usando a última localização salva."""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    from overstreet.formatters.messages import get_user_location
    loc = get_user_location(message.from_user.id)
    if not loc:
        await message.answer(
            "📍 Envie sua localização primeiro (clipe no ícone de 📎 → Localização).\n"
            "Ou ative o GPS e envie aqui."
        )
        return

    lat, lon = loc
    # Detectar raio no texto
    import re
    text = message.text.lower()
    radius = DEFAULT_RADIUS_KM
    m = re.search(r'(\d+)\s*km', text)
    if m:
        radius = min(float(m.group(1)), 50)  # max 50km

    resultados = _busca_por_coordenadas(tenant_conn, lat, lon, radius, 5)
    if not resultados:
        resultados_fallback = _busca_por_bairro(tenant_conn, lat, lon, 5)
        if not resultados_fallback:
            await message.answer(f"😔 Nenhum imóvel encontrado num raio de {radius:.0f}km.")
            return
        resultados = resultados_fallback

    lines = [f"📍 <b>Imóveis próximos (raio {radius:.0f}km):</b>", ""]
    for r in resultados:
        dist = r.get("distancia_km")
        lines.append(_format_resumo(r, dist))
        lines.append("──────────")
    lines.append(f"<i>Mostrando {len(resultados)} imóveis mais próximos</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")
