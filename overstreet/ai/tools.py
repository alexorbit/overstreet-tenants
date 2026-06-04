"""Executores das ferramentas NIM: query_db, search_imoveis, get_imovel."""
import re
import sqlite3
import logging
import asyncio
from overstreet.config import TIPO_GROUPS, TIPO_NAMES, COLLECTION_NAME

log = logging.getLogger("overstreet.ai.tools")

# Tabelas permitidas para query_db (whitelist)
_ALLOWED_TABLES = {"imoveis", "clientes", "cliente_aliases", "cliente_favoritos",
                   "imovel_fotos", "visitas", "followups", "chaves"}


def _query_to_dicts(db: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    """Executa SELECT e retorna lista de dicts sem alterar row_factory da conexão."""
    cursor = db.execute(sql, params)
    cols = [d[0] for d in cursor.description] if cursor.description else []
    rows = cursor.fetchall()
    return [dict(zip(cols, row)) for row in rows]


def exec_query(conn: sqlite3.Connection, sql: str, tenant_conn=None) -> str:
    """Executa SELECT e retorna resultado formatado."""
    db = tenant_conn or conn
    sql = sql.strip().rstrip(";")

    # Garantir que unaccent está registrado na conexão
    try:
        from overstreet.infra import _register_unaccent
        _register_unaccent(db)
    except Exception:
        pass

    # Validação: só SELECT
    if not sql.upper().startswith("SELECT"):
        return "Erro: apenas queries SELECT são permitidas"

    # Validação: bloquear subqueries perigosas e multi-statement
    upper = sql.upper()
    for forbidden in ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE",
                      "EXEC", "ATTACH", "DETACH", "VACUUM", "REINDEX"):
        if forbidden in upper and not any(
            upper.startswith(p) for p in ("SELECT",)
        ):
            return f"Erro: operação {forbidden} não permitida"

    # Validação: tabelas permitidas via FROM
    from_match = re.search(r'\bFROM\s+(\w+)', upper)
    if from_match and from_match.group(1) not in {t.upper() for t in _ALLOWED_TABLES}:
        return f"Erro: tabela '{from_match.group(1)}' não permitida"

    # JOIN também
    for join_match in re.finditer(r'\bJOIN\s+(\w+)', upper):
        table = join_match.group(1)
        if table not in {t.upper() for t in _ALLOWED_TABLES}:
            return f"Erro: tabela '{table}' não permitida em JOIN"

    # ═══ FIX CRÍTICO: Normalizar acentuação via canonical + unaccent ═══
    # O LLM gera city = 'Mairipora' (sem acento) mas o banco tem 'Mairiporã'
    # Estratégia: converter city = 'X' → canonical.smart_where
    TEXT_COLS = {"city", "district", "street", "state", "complement",
                 "finalidade", "situacao", "reference", "description"}

    def _normalize_sql_match(m):
        """Converte col = 'valor' para busca inteligente."""
        col = m.group(1)
        op = m.group(2)
        val_quoted = m.group(3)
        val = val_quoted[1:-1]  # remove aspas

        if col.lower() in TEXT_COLS and op == "=":
            from overstreet.db.canonical import resolve, unaccent as canon_unaccent
            canonical = resolve(col.lower(), val, conn=db)
            if canonical:
                # Match exato na grafia canônica — usa índice
                escaped = canonical.replace("'", "''")
                return f"{col} = '{escaped}'"
            else:
                # Fallback: unaccent LIKE
                norm = canon_unaccent(val)
                norm = norm.replace('%', '\\%').replace('_', '\\_')
                return f"unaccent({col}) LIKE '%'||unaccent('{norm}')||'%'"
        return m.group(0)

    sql = re.sub(
        r'(\w+)\s*(=)\s*(\'[^\']*\'|"[^"]*")',
        _normalize_sql_match,
        sql,
        flags=re.IGNORECASE
    )

    if "LIMIT" not in upper:
        sql += " LIMIT 20"

    try:
        rows = _query_to_dicts(db, sql)
        if not rows:
            return "Nenhum resultado"
        lines = [" | ".join(f"{k}={v}" for k, v in r.items()) for r in rows[:20]]
        return "\n".join(lines)
    except Exception as e:
        return f"Erro SQL: {e}"


def exec_search_imoveis(conn: sqlite3.Connection, qdrant, embedder,
                        tipo: str | None = None, bairro: str | None = None,
                        quartos_min: int | None = None, suites_min: int | None = None,
                        vagas_min: int | None = None, preco_max: int | None = None,
                        aluguel_max: int | None = None, finalidade: str | None = None,
                        limit: int = 5, texto_livre: str | None = None,
                        tenant_conn=None, collection_name: str = "imoveis") -> str:
    """Busca híbrida: SQLite (filtros) + Qdrant (semântico)."""
    db = tenant_conn or conn

    log.info("exec_search_imoveis: tipo=%s bairro=%s quartos=%s limit=%s tenant_conn=%s conn=%s",
             tipo, bairro, quartos_min, limit,
             "YES" if tenant_conn else "NO",
             "meta_db" if conn else "NONE")

    conditions = []
    params = []

    if tipo:
        tipo_key = tipo.lower().strip()
        tipo_ids = TIPO_GROUPS.get(tipo_key, [tipo_key])
        placeholders = ",".join(["?"] * len(tipo_ids))
        conditions.append(f"property_type IN ({placeholders})")
        params.extend(tipo_ids)

    if bairro:
        conditions.append("(district LIKE ? OR street LIKE ? OR city LIKE ? OR reference LIKE ?)")
        b = f"%{bairro}%"
        params.extend([b, b, b, b])

    if quartos_min:
        conditions.append("bedrooms >= ?")
        params.append(quartos_min)

    if suites_min:
        conditions.append("suites >= ?")
        params.append(suites_min)

    if vagas_min:
        conditions.append("garage >= ?")
        params.append(vagas_min)

    if preco_max:
        conditions.append(
            "sale_price IS NOT NULL AND sale_price != '' AND "
            "CAST(REPLACE(REPLACE(REPLACE(sale_price,'.',''),',',''),'R$','') AS INTEGER) <= ?"
        )
        params.append(preco_max)

    if aluguel_max:
        conditions.append(
            "rental_price IS NOT NULL AND rental_price != '' AND "
            "CAST(REPLACE(REPLACE(REPLACE(rental_price,'.',''),',',''),'R$','') AS INTEGER) <= ?"
        )
        params.append(aluguel_max)

    if finalidade:
        if "alug" in finalidade.lower():
            conditions.append("finalidade LIKE ?")
            params.append("%aluguel%")
        else:
            conditions.append("finalidade LIKE ?")
            params.append("%venda%")

    conditions.append("(situacao = 'Disponivel' OR situacao IS NULL)")

    sql_results = []
    if conditions:
        where = " AND ".join(conditions)
        try:
            rows = _query_to_dicts(
                db,
                f"SELECT * FROM imoveis WHERE {where} ORDER BY id DESC LIMIT ?",
                params + [limit * 2]
            )
            sql_results = rows
            log.info("exec_search_imoveis: SQL returned %d rows", len(sql_results))
        except Exception as e:
            log.warning("Erro SQL search: %s", e)

    # Complementar com Qdrant se necessário
    qdrant_results = []
    if qdrant is not None and embedder is not None:
        query_parts = [p for p in [tipo, bairro,
                                    f"{quartos_min} dormitorios" if quartos_min else None,
                                    texto_livre] if p]
        qdrant_query = " ".join(query_parts) if query_parts else None

        if qdrant_query and len(sql_results) < limit:
            from overstreet.search.semantic import busca_semantica
            sem = busca_semantica(db, qdrant, embedder, qdrant_query, limit=limit * 2,
                                 collection_name=collection_name)
            for r in sem:
                if any(sr["id"] == r["id"] for sr in sql_results):
                    continue
                if tipo:
                    tipo_ids = TIPO_GROUPS.get(tipo.lower().strip(), [tipo.lower().strip()])
                    if str(r.get("property_type", "")) not in tipo_ids:
                        continue
                if quartos_min and r.get("bedrooms") is not None and r["bedrooms"] < quartos_min:
                    continue
                qdrant_results.append(r)

    all_results = (sql_results + qdrant_results)[:limit]

    if not all_results:
        return "Nenhum imóvel encontrado com esses filtros."

    lines = []
    for rd in all_results:
        tipo_nome = TIPO_NAMES.get(str(rd.get("property_type", "")), "")
        parts = [f"COD {rd['id']}"]
        if tipo_nome:
            parts.append(tipo_nome)
        if rd.get("street"):
            parts.append(f"End: {rd['street']}")
        if rd.get("district") and str(rd.get("district", "")) not in ("None", ""):
            parts.append(f"Bairro: {rd['district']}")
        if rd.get("city") and str(rd.get("city", "")) not in ("None", ""):
            parts.append(f"Cidade: {rd['city']}")
        if rd.get("bedrooms") is not None:
            parts.append(f"{rd['bedrooms']} dorm")
        if rd.get("suites") is not None:
            parts.append(f"{rd['suites']} suite")
        if rd.get("garage") is not None:
            parts.append(f"{rd['garage']} vagas")
        if rd.get("sale_price") and str(rd.get("sale_price", "")) not in ("None", ""):
            parts.append(f"Venda R${rd['sale_price']}")
        if rd.get("rental_price") and str(rd.get("rental_price", "")) not in ("None", ""):
            parts.append(f"Aluguel R${rd['rental_price']}")
        if rd.get("owner_name") and str(rd.get("owner_name", "")) not in ("None", ""):
            parts.append(f"Prop: {rd['owner_name']}")
        lines.append(" | ".join(parts))

    return f"{len(all_results)} imóveis encontrados:\n" + "\n".join(lines)


def exec_get_imovel(conn: sqlite3.Connection, codigo: int, tenant_conn=None) -> str:
    """Busca imóvel exato por código."""
    db = tenant_conn or conn
    rows = _query_to_dicts(db, "SELECT * FROM imoveis WHERE id = ?", (codigo,))
    if not rows:
        return f"Imóvel COD {codigo} não encontrado no sistema."
    rd = rows[0]
    tipo_nome = TIPO_NAMES.get(str(rd.get("property_type", "")), "")
    parts = [f"COD {rd['id']}"]
    for label, key in [("", "tipo_nome"), ("End", "street"), ("Bairro", "district"),
                        ("Cidade", "city")]:
        val = tipo_nome if key == "tipo_nome" else rd.get(key, "")
        if val and str(val) not in ("None", ""):
            parts.append(f"{label}: {val}" if label else str(val))
    if rd.get("finalidade"):
        parts.append(rd["finalidade"])
    if rd.get("situacao"):
        parts.append(rd["situacao"])
    if rd.get("bedrooms") is not None:
        parts.append(f"{rd['bedrooms']} dorm")
    if rd.get("suites") is not None:
        parts.append(f"{rd['suites']} suite")
    if rd.get("bathrooms") is not None:
        parts.append(f"{rd['bathrooms']} banh")
    if rd.get("garage") is not None:
        parts.append(f"{rd['garage']} vagas")
    for label, key in [("Venda R$", "sale_price"), ("Aluguel R$", "rental_price"),
                        ("Cond R$", "condo_fee")]:
        val = rd.get(key, "")
        if val and str(val) not in ("None", ""):
            parts.append(f"{label}{val}")
    for label, key in [("Prop", "owner_name"), ("Tel", "owner_mobile"), ("Email", "owner_email")]:
        val = rd.get(key, "")
        if val and str(val) not in ("None", ""):
            parts.append(f"{label}: {val}")
    if rd.get("description") and str(rd.get("description", "")) not in ("None", ""):
        parts.append(f"Desc: {rd['description'][:200]}")
    return " | ".join(parts)


def exec_match_imoveis(conn: sqlite3.Connection, nome_cliente: str,
                       tenant_conn=None) -> str:
    """Tool: busca imóveis compatíveis com o perfil de um cliente."""
    from overstreet.db.clientes import resolve_alias, list_clientes
    from overstreet.db.match import match_imoveis_to_cliente

    db = tenant_conn or conn
    cliente = resolve_alias(db, 0, nome_cliente)
    if not cliente:
        # Tentar via list_clientes com LIKE
        clientes = list_clientes(db, 0)
        for c in clientes:
            if nome_cliente.lower() in c["nome"].lower():
                cliente = c
                break

    if not cliente:
        return f"Cliente '{nome_cliente}' não encontrado no sistema."

    perfil = (cliente.get("perfil_descricao") or "").strip()
    if not perfil:
        return (f"O cliente '{cliente['nome']}' não tem perfil de preferências cadastrado. "
                "Peça ao corretor para editar o cliente e adicionar preferências.")

    matches = match_imoveis_to_cliente(db, cliente, limit=5)

    if not matches:
        return (f"Nenhum imóvel compatível com o perfil de '{cliente['nome']}' "
                f"(perfil: {perfil[:100]}).")

    from overstreet.config import TIPO_NAMES
    lines = [f"{len(matches)} imóveis compatíveis com {cliente['nome']}:"]
    for m in matches:
        score = m.pop("_score", None)
        tipo_nome = TIPO_NAMES.get(str(m.get("property_type", "")), "")
        parts = [f"COD {m['id']}"]
        if tipo_nome:
            parts.append(tipo_nome)
        if m.get("district") and str(m.get("district", "")) not in ("None", ""):
            parts.append(f"Bairro: {m['district']}")
        if m.get("city") and str(m.get("city", "")) not in ("None", ""):
            parts.append(f"Cidade: {m['city']}")
        if m.get("bedrooms") is not None:
            parts.append(f"{m['bedrooms']} dorm")
        if m.get("sale_price") and str(m.get("sale_price", "")) not in ("None", ""):
            parts.append(f"Venda R${m['sale_price']}")
        if m.get("rental_price") and str(m.get("rental_price", "")) not in ("None", ""):
            parts.append(f"Aluguel R${m['rental_price']}")
        if score is not None:
            parts.append(f"Match {score:.0%}")
        lines.append(" | ".join(parts))

    return "\n".join(lines)


def is_factual_question(text: str) -> bool:
    patterns = [
        r'quantos?\b', r'quanto[s]?\b', r'\btem\b.*\bimov', r'\bexiste\b',
        r'\bquais\b', r'\blista\b', r'\bmostre?\b.*\btipo', r'\bcontar\b',
        r'\btotal\b', r'\bcatalogo\b', r'\bestatistic', r'\bmaior\b',
        r'\bmenor\b', r'\bmais caro\b', r'\bmais barato\b', r'\bpreco\b.*\bmedio\b',
        r'\bmedia\b', r'\bquantidade\b',
    ]
    return any(re.search(p, text.lower()) for p in patterns)
