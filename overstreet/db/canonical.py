"""Canonical lookup — grafia correta pt-BR para cidades e bairros.

Este módulo resolve o problema de grafia inconsistente de forma DETERMINÍSTICA:

1. Dicionário canônico: toda cidade/bairro tem UMA grafia oficial
2. Função unaccent(): busca accent-insensitive (Jaçanã = jacana = JACANA)
3. Função resolve(): converte qualquer grafia → canônica (jacana → Jaçanã)
4. Função smart_where(): gera cláusula SQL que encontra sempre o resultado certo

Uso:
    from overstreet.db.canonical import resolve, smart_where, get_cities, get_districts

    # Busca determinística — funciona com qualquer grafia
    sql = f"SELECT * FROM imoveis WHERE {smart_where('city', 'mairipora')}"
    sql = f"SELECT * FROM imoveis WHERE {smart_where('district', 'jacana')}"

    # Resolver grafia canônica
    cidade = resolve('city', 'mairipora')  # → 'Mairiporã'
    bairro = resolve('district', 'jacana') # → 'Jaçanã'

    # Listas para selects/autocomplete
    cidades = get_cities(conn)    # ['Atibaia', 'Guarulhos', ...]
    bairros = get_districts(conn) # ['Jaçanã', 'Santana', ...]
"""
import sqlite3
import logging
from unicodedata import normalize as _unorm

log = logging.getLogger("overstreet.db.canonical")

# ══════════════════════════════════════════════════════════════
# CORE: unaccent + normalization
# ══════════════════════════════════════════════════════════════

def unaccent(s: str) -> str:
    """Remove acentos, cedilha, e normaliza pra busca.
    
    'Jaçanã' → 'jacana'
    'São Paulo' → 'sao paulo'
    'PARQUE PETRÓPOLIS' → 'parque petropolis'
    """
    if not s:
        return ""
    return (
        _unorm('NFKD', str(s))
        .encode('ascii', 'ignore')
        .decode('ascii')
        .lower()
        .strip()
    )


def _title_case_pt(s: str) -> str:
    """Title case pt-BR — respeita preposições e abreviações."""
    if not s:
        return s
    preps = {'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'na', 'no', 'com', 'para'}
    parts = s.split()
    result = []
    for i, p in enumerate(parts):
        if len(p) > 1 and p.endswith('.'):
            result.append(p[0].upper() + p[1:])
        elif p.lower() in preps and i > 0:
            result.append(p.lower())
        else:
            result.append(p[0].upper() + p[1:].lower() if len(p) > 1 else p.upper())
    return ' '.join(result)


# ══════════════════════════════════════════════════════════════
# CANONICAL DICTIONARY — per-tenant cache keyed by DB path
# ══════════════════════════════════════════════════════════════

# Cache: db_path → {"city": {unaccent_key: canonical_val}, "district": {...}}
_tenant_cache: dict[str, dict[str, dict[str, str]]] = {}


def _db_identity(conn: sqlite3.Connection) -> str:
    """Get a stable identity for a connection (its file path)."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        if row:
            return row[2]  # file path
    except Exception:
        pass
    return str(id(conn))


def load_canonical(conn: sqlite3.Connection):
    """Carrega dicionário canônico do banco (roda uma vez por tenant no startup)."""
    db_id = _db_identity(conn)
    if db_id in _tenant_cache:
        return  # already loaded for this tenant

    cache: dict[str, dict[str, str]] = {"city": {}, "district": {}}

    for col in ("city", "district"):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT {col} FROM imoveis "
                f"WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}"
            ).fetchall()
        except Exception:
            rows = []

        for (val,) in rows:
            key = unaccent(val)
            if key in cache[col]:
                existing = cache[col][key]
                if _is_better_canonical(val, existing):
                    cache[col][key] = val
            else:
                cache[col][key] = val

    _tenant_cache[db_id] = cache
    log.info(
        "Canonical carregado [%s]: %d cidades, %d bairros",
        db_id, len(cache["city"]), len(cache["district"]),
    )


def _get_cache(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Get canonical cache for a connection, loading if needed."""
    db_id = _db_identity(conn)
    if db_id not in _tenant_cache:
        load_canonical(conn)
    return _tenant_cache.get(db_id, {"city": {}, "district": {}})


def _is_better_canonical(candidate: str, existing: str) -> bool:
    """Decide se candidate é grafia 'melhor' que existing.
    
    Prioridade: acentuada > title-case > ALL CAPS > all lower
    """
    import re
    _ACCENT_RE = re.compile(r'[àáâãäèéêëìíîïòóôõöùúûüçÀÁÂÃÄÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÇ]')
    c_accent = bool(_ACCENT_RE.search(candidate))
    e_accent = bool(_ACCENT_RE.search(existing))
    
    if c_accent and not e_accent:
        return True
    if not c_accent and e_accent:
        return False
    
    c_tc = candidate == _title_case_pt(candidate)
    e_tc = existing == _title_case_pt(existing)
    
    if c_tc and not e_tc:
        return True
    
    return False


def resolve(column: str, value: str, conn: sqlite3.Connection | None = None) -> str | None:
    """Converte qualquer grafia → canônica.
    
    resolve('city', 'mairipora') → 'Mairiporã'
    resolve('district', 'jacana') → 'Jaçanã'
    resolve('district', 'JACANA') → 'Jaçanã'
    resolve('city', 'xyz') → None  (não encontrado)
    """
    key = unaccent(value)
    
    # Se temos conn, usar cache por-tenant
    if conn is not None:
        cache = _get_cache(conn)
        return cache.get(column, {}).get(key)
    
    # Fallback: tentar todos os caches (ordem arbitrária)
    for cache in _tenant_cache.values():
        result = cache.get(column, {}).get(key)
        if result:
            return result
    
    return None


def smart_where(column: str, value: str, conn: sqlite3.Connection | None = None) -> tuple[str, list]:
    """Gera cláusula SQL WHERE inteligente para busca de texto.
    
    Estratégia (em ordem de tentativa):
    1. Match exato canônico (city = 'Mairiporã')
    2. unaccent LIKE (unaccent(city) LIKE '%mairipor%')
    
    Retorna (sql_fragment, params).
    """
    canonical = resolve(column, value, conn=conn)
    
    if canonical:
        return f"{column} = ?", [canonical]
    
    norm = unaccent(value)
    norm = norm.replace('%', '\\%').replace('_', '\\_')
    return f"unaccent({column}) LIKE '%' || ? || '%'", [norm]


def get_cities(conn: sqlite3.Connection) -> list[str]:
    """Lista todas as cidades canônicas (para select/autocomplete)."""
    cache = _get_cache(conn)
    if cache.get("city"):
        return sorted(cache["city"].values())
    rows = conn.execute(
        "SELECT DISTINCT city FROM imoveis WHERE city IS NOT NULL AND city != '' ORDER BY city"
    ).fetchall()
    return [r[0] for r in rows]


def get_districts(conn: sqlite3.Connection, city: str | None = None) -> list[str]:
    """Lista bairros canônicos, opcionalmente filtrados por cidade."""
    if city:
        canonical_city = resolve("city", city, conn=conn) or city
        rows = conn.execute(
            "SELECT DISTINCT district FROM imoveis "
            "WHERE city = ? AND district IS NOT NULL AND district != '' ORDER BY district",
            (canonical_city,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT district FROM imoveis "
            "WHERE district IS NOT NULL AND district != '' ORDER BY district"
        ).fetchall()
    return [r[0] for r in rows]


# ══════════════════════════════════════════════════════════════
# AUTO-FIX: normalizar grafias no banco (rodar após import de dados)
# ══════════════════════════════════════════════════════════════

def normalize_all(conn: sqlite3.Connection):
    """Normaliza TODAS as grafias de city e district no banco.
    
    Roda uma vez após import de dados JSONL. Depois o banco fica consistente.
    """
    from collections import defaultdict
    
    for col in ("city", "district"):
        rows = conn.execute(
            f"SELECT {col}, COUNT(*) as c FROM imoveis "
            f"WHERE {col} IS NOT NULL AND {col} != '' GROUP BY {col}"
        ).fetchall()
        
        by_norm = defaultdict(list)
        for val, count in rows:
            by_norm[unaccent(val)].append((val, count))
        
        fixes = 0
        for norm_key, variants in by_norm.items():
            if len(variants) <= 1:
                val, count = variants[0]
                expected = _title_case_pt(val)
                if expected != val:
                    cur = conn.execute(
                        f"UPDATE imoveis SET {col} = ? WHERE {col} = ?",
                        (expected, val),
                    )
                    fixes += cur.rowcount
                    log.info("Normalizado: '%s' → '%s' (%d registros)", val, expected, cur.rowcount)
                continue
            
            # Múltiplas grafias — escolher canônica
            canonical = max(variants, key=lambda x: x[1])[0]
            for name, count in sorted(variants, key=lambda x: -x[1]):
                if _is_better_canonical(name, canonical):
                    canonical = name
            
            for name, count in variants:
                if name != canonical:
                    cur = conn.execute(
                        f"UPDATE imoveis SET {col} = ? WHERE {col} = ?",
                        (canonical, name),
                    )
                    fixes += cur.rowcount
                    log.info("Corrigido: '%s' → '%s' (%d registros)", name, canonical, cur.rowcount)
        
        conn.commit()
        log.info("Coluna '%s': %d registros normalizados", col, fixes)
    
    # Limpar cache pra forçar reload
    db_id = _db_identity(conn)
    _tenant_cache.pop(db_id, None)
    load_canonical(conn)
