"""Match automático: perfil do cliente ↔ imóveis disponíveis."""
import re
import sqlite3
import logging
from datetime import datetime, timedelta

from overstreet.config import TIPO_GROUPS
from overstreet.db.imoveis import _query_dicts

log = logging.getLogger("overstreet.db.match")


def _parse_perfil(perfil: str) -> dict:
    """Extrai critérios estruturados do perfil_descricao em texto livre.

    Retorna dict com chaves opcionais:
      quartos_min (int), preco_max (int), tipo_ids (list[str]),
      location (str), finalidade (str), vagas_min (int),
      suites_min (int), aluguel_max (int)
    """
    if not perfil:
        return {}

    text = perfil.lower()
    criteria: dict = {}

    # Quartos: "3 quartos", "3 dorm", "3 dormitórios", "minimo 2 quarto"
    m = re.search(r'(\d+)\s*(?:quartos?|dorm|dormit[oó]rios?)', text)
    if m:
        criteria["quartos_min"] = int(m.group(1))

    # Vagas: "2 vagas", "2 garagem"
    m = re.search(r'(\d+)\s*(?:vagas?|garage|garagem)', text)
    if m:
        criteria["vagas_min"] = int(m.group(1))

    # Suítes: "2 suites", "2 suítes"
    m = re.search(r'(\d+)\s*suite?s?', text)
    if m:
        criteria["suites_min"] = int(m.group(1))

    # Preço venda: "até 500k", "até 500 mil", "máximo 300000", "ate 1.5mi"
    preco_patterns = [
        r'at[eé]\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*(?:mil|k)\b',
        r'm[aá]ximo\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*(?:mil|k)\b',
        r'at[eé]\s+r?\$\s*([\d]+(?:[.,][\d]+)?)\b',
        r'm[aá]ximo\s+r?\$\s*([\d]+(?:[.,][\d]+)?)\b',
        r'at[eé]\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*mi\b',
        r'pre[çc]o\s+at[eé]\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*(?:mil|k|mi)\b',
        # Variantes sem acento
        r'ate\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*(?:mil|k)\b',
        r'maximo\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*(?:mil|k)\b',
        r'ate\s+(?:r?\$?\s*)?([\d]+(?:[.,][\d]+)?)\s*mi\b',
    ]
    for pat in preco_patterns:
        m = re.search(pat, text)
        if m:
            val_str = m.group(1)
            # Normalize: treat "." as thousands sep, "," as decimal (Brazilian)
            # or "." as decimal if followed by exactly one digit (e.g. 1.5mi)
            if re.match(r'^[\d]+\.[\d]$', val_str):
                # Likely decimal: 1.5, 2.5
                val = float(val_str)
            else:
                val = float(val_str.replace(".", "").replace(",", "."))
            try:
                val = float(val)
            except ValueError:
                continue
            match_text = m.group(0)
            if re.search(r'\bmi\b', match_text):
                criteria["preco_max"] = int(val * 1_000_000)
            elif re.search(r'(?:mil|k)\b', match_text):
                criteria["preco_max"] = int(val * 1000)
            else:
                criteria["preco_max"] = int(val)
            break

    # Preço aluguel: "aluguel até 2k", "aluguel máximo 3000"
    aluguel_patterns = [
        r'aluguel\s+at[eé]\s+(?:r?\$?\s*)?([\d.,]+)\s*(?:mil|k)\b',
        r'aluguel\s+m[aá]ximo\s+(?:r?\$?\s*)?([\d.,]+)\s*(?:mil|k)\b',
    ]
    for pat in aluguel_patterns:
        m = re.search(pat, text)
        if m:
            val_str = m.group(1).replace(".", "").replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue
            if "mil" in m.group(0) or "k" in m.group(0):
                criteria["aluguel_max"] = int(val * 1000)
            else:
                criteria["aluguel_max"] = int(val)
            break

    # Tipo: "apartamento", "apto", "casa", "sobrado", "terreno", "cobertura", etc.
    for tipo_key in TIPO_GROUPS:
        if tipo_key in text:
            criteria["tipo_ids"] = TIPO_GROUPS[tipo_key]
            break

    # Finalidade: "para alugar", "aluguel", "compra", "venda"
    if re.search(r'alug(?:uel|ar)|loc(?:ação|acao)', text):
        criteria["finalidade"] = "aluguel"
    elif re.search(r'compr(?:a|ar)|venda', text):
        criteria["finalidade"] = "venda"

    # Localização: pega a primeira sequência de palavras depois de "em" que
    # pareça um local (não comuns como "um", "uma", "um/")
    loc_patterns = [
        r'(?:em|na|no)\s+([\wÀ-ÿ][\wÀ-ÿ\s]{1,25}?)(?:\s+(?:at[eé]|com\s|para|quer|de$|ate$|\n|$))',
        r'(?:bairro|regi[aã]o)\s+(?:[\wÀ-ÿ]+\s+)*?([\wÀ-ÿ\s]{2,25}?)(?:\s+(?:at[eé]|com|para|quer))',
        r'(?:em|na|no)\s+([\wÀ-ÿ][\wÀ-ÿ\s]{1,25})$',  # "em Santana" at end of string
    ]
    for pat in loc_patterns:
        m = re.search(pat, text)
        if m:
            loc = m.group(1).strip().title()
            # Filtra ruídos comuns
            if loc not in ("Um", "Uma", "O", "A", "Seu", "Sua"):
                criteria["location"] = loc
                break

    return criteria


def match_imoveis_to_cliente(
    conn: sqlite3.Connection,
    cliente: dict,
    limit: int = 10,
    **kwargs,
) -> list[dict]:
    """Busca imóveis compatíveis com o perfil de um cliente.

    Single-tenant: aceita `tenant_id=` em kwargs por compat (ignorado).
    Retorna lista de dicts com os campos do imóvel + '_score' (0.0 a 1.0).
    Ordena por score descendente.
    """
    perfil = (cliente.get("perfil_descricao") or "").strip()
    if not perfil:
        return []

    criteria = _parse_perfil(perfil)
    if not criteria:
        # Perfil sem critérios identificáveis — retorna nada
        return []

    conditions = []
    params: list = []
    total_criteria = 0

    # Quartos
    if "quartos_min" in criteria:
        conditions.append("bedrooms >= ?")
        params.append(criteria["quartos_min"])
        total_criteria += 1

    # Suítes
    if "suites_min" in criteria:
        conditions.append("suites >= ?")
        params.append(criteria["suites_min"])
        total_criteria += 1

    # Vagas
    if "vagas_min" in criteria:
        conditions.append("garage >= ?")
        params.append(criteria["vagas_min"])
        total_criteria += 1

    # Tipo
    if "tipo_ids" in criteria:
        tipo_ids = criteria["tipo_ids"]
        placeholders = ",".join(["?"] * len(tipo_ids))
        conditions.append(f"property_type IN ({placeholders})")
        params.extend(tipo_ids)
        total_criteria += 1

    # Localização (bairro/cidade)
    if "location" in criteria:
        loc = f"%{criteria['location']}%"
        conditions.append("(district LIKE ? OR city LIKE ? OR street LIKE ? OR reference LIKE ?)")
        params.extend([loc, loc, loc, loc])
        total_criteria += 1

    # Preço venda
    if "preco_max" in criteria:
        conditions.append(
            "(sale_price IS NOT NULL AND sale_price != '' AND "
            "CAST(REPLACE(REPLACE(REPLACE(sale_price, '.', ''), ',', ''), 'R$', '') AS INTEGER) <= ?)"
        )
        params.append(criteria["preco_max"])
        total_criteria += 1

    # Preço aluguel
    if "aluguel_max" in criteria:
        conditions.append(
            "(rental_price IS NOT NULL AND rental_price != '' AND "
            "CAST(REPLACE(REPLACE(REPLACE(rental_price, '.', ''), ',', ''), 'R$', '') AS INTEGER) <= ?)"
        )
        params.append(criteria["aluguel_max"])
        total_criteria += 1

    # Finalidade
    if "finalidade" in criteria:
        fin = criteria["finalidade"]
        if fin == "aluguel":
            conditions.append("finalidade LIKE ?")
            params.append("%aluguel%")
        else:
            conditions.append("finalidade LIKE ?")
            params.append("%venda%")
        total_criteria += 1

    # Sempre filtrar disponíveis
    conditions.append("(situacao = 'Disponivel' OR situacao IS NULL)")

    if not conditions:
        return []

    where = " AND ".join(conditions)
    sql = f"SELECT * FROM imoveis WHERE {where} ORDER BY id DESC LIMIT ?"
    rows = _query_dicts(conn, sql, params + [limit * 3])

    # Scoring: quantos critérios o imóvel atende
    scored = []
    for row in rows:
        score = _calc_score(row, criteria)
        if score > 0:
            row["_score"] = score
            scored.append(row)

    scored.sort(key=lambda r: r["_score"], reverse=True)
    return scored[:limit]


def _calc_score(row: dict, criteria: dict) -> float:
    """Calcula score 0.0–1.0 baseado em quantos critérios o imóvel atende."""
    met = 0
    total = len(criteria)

    # Quartos
    if "quartos_min" in criteria:
        br = row.get("bedrooms")
        if br is not None and br >= criteria["quartos_min"]:
            met += 1

    # Suítes
    if "suites_min" in criteria:
        su = row.get("suites")
        if su is not None and su >= criteria["suites_min"]:
            met += 1

    # Vagas
    if "vagas_min" in criteria:
        ga = row.get("garage")
        if ga is not None and ga >= criteria["vagas_min"]:
            met += 1

    # Tipo
    if "tipo_ids" in criteria:
        pt = str(row.get("property_type", ""))
        if pt in criteria["tipo_ids"]:
            met += 1

    # Localização
    if "location" in criteria:
        loc_upper = criteria["location"].upper()
        for field in ("district", "city", "street", "reference"):
            val = str(row.get(field, "")).upper()
            if loc_upper in val:
                met += 1
                break

    # Preço venda
    if "preco_max" in criteria:
        sp = row.get("sale_price")
        if sp and str(sp).strip() not in ("", "None"):
            try:
                num = int(re.sub(r"[^\d]", "", str(sp)))
                if num <= criteria["preco_max"]:
                    met += 1
            except (ValueError, TypeError):
                pass

    # Preço aluguel
    if "aluguel_max" in criteria:
        rp = row.get("rental_price")
        if rp and str(rp).strip() not in ("", "None"):
            try:
                num = int(re.sub(r"[^\d]", "", str(rp)))
                if num <= criteria["aluguel_max"]:
                    met += 1
            except (ValueError, TypeError):
                pass

    # Finalidade
    if "finalidade" in criteria:
        fin = str(row.get("finalidade", "")).lower()
        if criteria["finalidade"] in fin:
            met += 1

    if total == 0:
        return 0.0
    return met / total


def get_recent_imoveis(
    conn: sqlite3.Connection,
    days: int = 7,
    limit: int = 10,
    **kwargs,
) -> list[dict]:
    """Imóveis recentemente cadastrados (últimos N dias) que NÃO foram favoritados por nenhum cliente.

    Single-tenant: aceita `tenant_id=` em kwargs por compat (ignorado).
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    sql = """
        SELECT * FROM imoveis
        WHERE (situacao = 'Disponivel' OR situacao IS NULL)
          AND created_at IS NOT NULL
          AND created_at >= ?
          AND id NOT IN (
              SELECT DISTINCT imovel_id FROM cliente_favoritos
          )
        ORDER BY created_at DESC
        LIMIT ?
    """
    return _query_dicts(conn, sql, (cutoff, limit))
