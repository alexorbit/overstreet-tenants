#!/usr/bin/env python3
"""
Ingest moderno de imóveis a partir de JSON / JSONL → SQLite + FTS5 + Qdrant.

Uso:
    python3 ingest.py <arquivo.json|jsonl> [--no-qdrant] [--batch-size 256] [--reindex]

Detecção automática:
    - Se a primeira linha do arquivo for '[' → trata como JSON array
    - Caso contrário → trata como JSONL (um documento JSON por linha)

Cada item deve ter (no mínimo) o campo `id`. Tipos esperados:
    id: int
    sale_price / rental_price: str   (texto livre: "R$ 1.200,00")
    bedrooms: int

A escrita é idempotente: REPLACE INTO em `imoveis` (PK = id).
Embeddings via fastembed (BAAI/bge-small-en-v1.5, 384 dims) e upsert no Qdrant.
Se o Qdrant estiver indisponível OU --no-qdrant for passado, o pipeline segue
apenas com SQLite + FTS5 (busca sintática continua funcionando).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("ingest")

# ── Schema (deve bater com overstreet/db/schema.py) ─────────────────────
IMOVEIS_COLUMNS: list[str] = [
    "id", "owner_name", "owner_phone", "owner_mobile", "owner_email",
    "street", "number", "district", "city", "state", "zip",
    "bedrooms", "bathrooms", "garage", "land_area", "built_area",
    "sale_price", "rental_price", "condo_fee", "iptu",
    "description", "property_type", "finalidade", "situacao",
    "complement", "reference", "apartment",
    "suites", "salas", "area_util",
    "tipo_imovel_id", "agencia_id",
    "created_at", "updated_at", "full_text",
    "latitude", "longitude",
]
INT_COLS = {"id", "bedrooms", "bathrooms", "garage", "suites", "salas",
            "tipo_imovel_id", "agencia_id"}
FLOAT_COLS = {"land_area", "built_area", "area_util", "latitude", "longitude"}

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
COLLECTION_NAME = "imoveis"

FINALIDADE_MAP = {1: "Venda", 2: "Locacao", 3: "Venda/Locacao"}
SITUACAO_MAP = {47: "Disponivel", 49: "Disponivel", 50: "Reservado", 51: "Vendido/Alugado"}
TIPO_MAP = {72: "Apartamento", 68: "Casa", 47: "Sobrado", 75: "Salao Comercial",
            78: "Sobrado", 83: "Apartamento", 85: "Casa em Condominio", 66: "Terreno"}


# ── Coerção + normalização ──────────────────────────────────────────────
def _c(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _i(v: Any) -> int | None:
    if v in (None, ""): return None
    try: return int(v)
    except (TypeError, ValueError): return None


def _f(v: Any) -> float | None:
    if v in (None, ""): return None
    try: return float(v)
    except (TypeError, ValueError): return None


def normalize_record(raw: dict) -> dict:
    """Extrai campos do JSON bruto e monta o registro do schema `imoveis`."""
    addr = raw.get("address") or {}
    prop = raw.get("property") or {}
    r = raw.get("raw") or {}

    # Texto rico para embedding/BM25
    parts: list[str] = []
    if raw.get("description"):
        parts.append(str(raw["description"]))
    if addr.get("street"):
        parts.append(f"Endereço: {addr['street']}, {addr.get('number', '')}")
    for label, key in [("Bairro", "district"), ("Cidade", "city"), ("Estado", "state")]:
        if addr.get(key):
            parts.append(f"{label}: {addr[key]}")
    for k, unit in [("bedrooms", "dormitórios"), ("bathrooms", "banheiros"),
                    ("garage", "vagas"), ("suites", "suítes")]:
        val = prop.get(k) or r.get(k if k != "garage" else "vagas")
        if val:
            parts.append(f"{val} {unit}")
    if prop.get("sale_price") or r.get("valor_venda"):
        parts.append(f"Valor venda: R$ {prop.get('sale_price') or r.get('valor_venda')}")
    if r.get("valor_locacao"):    parts.append(f"Aluguel: R$ {r['valor_locacao']}")
    if r.get("valor_condominio"): parts.append(f"Condomínio: R$ {r['valor_condominio']}")
    if prop.get("built_area"):    parts.append(f"Área construída: {prop['built_area']} m²")
    if prop.get("land_area"):     parts.append(f"Área terreno: {prop['land_area']} m²")
    if r.get("area_util"):        parts.append(f"Área útil: {r['area_util']} m²")
    if r.get("tipo_imovel_id"):
        parts.append(TIPO_MAP.get(r["tipo_imovel_id"], f"Tipo {r['tipo_imovel_id']}"))

    return {
        "id": _i(raw.get("id")),
        "owner_name": _c(raw.get("owner_name")),
        "owner_phone": _c(raw.get("owner_phone")),
        "owner_mobile": _c(raw.get("owner_mobile")),
        "owner_email": _c(raw.get("owner_email")),
        "street": _c(addr.get("street") or r.get("endereco_rua")),
        "number": _c(addr.get("number") or r.get("endereco_numero")),
        "district": _c(addr.get("district") or r.get("endereco_bairro")),
        "city": _c(addr.get("city") or r.get("endereco_cidade")),
        "state": _c(addr.get("state") or r.get("endereco_uf")),
        "zip": _c(addr.get("zip") or r.get("endereco_cep")),
        "bedrooms": _i(prop.get("bedrooms") or r.get("dormitorios")),
        "bathrooms": _i(prop.get("bathrooms") or r.get("banheiros")),
        "garage": _i(prop.get("garage") or r.get("vagas")),
        "land_area": _f(prop.get("land_area") or r.get("area_terreno")),
        "built_area": _f(prop.get("built_area") or r.get("area_construida")),
        "sale_price": _c(prop.get("sale_price") or r.get("valor_venda")),
        "rental_price": _c(r.get("valor_locacao")),
        "condo_fee": _c(r.get("valor_condominio")),
        "iptu": _c(r.get("valor_iptu")),
        "description": _c(raw.get("description")),
        "property_type": _c(r.get("tipo_imovel_id")),
        "finalidade": FINALIDADE_MAP.get(_i(r.get("finalidade_id")) or -1, ""),
        "situacao": SITUACAO_MAP.get(_i(r.get("situacao_id")) or -1, ""),
        "complement": _c(r.get("endereco_complemento")),
        "reference": _c(r.get("endereco_referencia")),
        "apartment": _c(r.get("endereco_apartamento")),
        "suites": _i(r.get("suites")),
        "salas": _i(r.get("salas")),
        "area_util": _f(r.get("area_util")),
        "tipo_imovel_id": _i(r.get("tipo_imovel_id")),
        "agencia_id": _i(r.get("agencia_id")),
        "created_at": _c(r.get("created_at")),
        "updated_at": _c(r.get("updated_at")),
        "full_text": ". ".join(p for p in parts if p),
        "latitude": _f(r.get("latitude")),
        "longitude": _f(r.get("longitude")),
    }


def validate_record(rec: dict) -> str | None:
    """Validação leve; retorna mensagem de erro ou None se OK."""
    if rec.get("id") is None:
        return "campo 'id' ausente ou inválido"
    if not isinstance(rec["id"], int):
        return f"id deve ser int, recebido {type(rec['id']).__name__}"
    for fld in ("sale_price", "rental_price"):
        if fld in rec and rec[fld] is not None and not isinstance(rec[fld], str):
            return f"{fld} deve ser str, recebido {type(rec[fld]).__name__}"
    if rec.get("bedrooms") is not None and not isinstance(rec["bedrooms"], int):
        return f"bedrooms deve ser int, recebido {type(rec['bedrooms']).__name__}"
    return None


# ── I/O ────────────────────────────────────────────────────────────────
def detect_format(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if s:
                return "json_array" if s.startswith("[") else "jsonl"
    raise ValueError(f"Arquivo vazio: {path}")


def iter_records(path: Path) -> Iterator[dict]:
    fmt = detect_format(path)
    log.info("Formato detectado: %s", fmt)
    if fmt == "json_array":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError("Esperava JSON array.")
        yield from data
    else:
        with open(path, "r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning("Linha %d inválida (%s) — pulando", n, e)


# ── SQLite + FTS5 ──────────────────────────────────────────────────────
def init_schema(db: sqlite3.Connection) -> None:
    cols_ddl = ",\n    ".join(
        "id INTEGER PRIMARY KEY" if c == "id" else f"{c} TEXT" for c in IMOVEIS_COLUMNS
    )
    db.execute(f"CREATE TABLE IF NOT EXISTS imoveis ({cols_ddl})")
    for idx in (
        "idx_imoveis_city", "idx_imoveis_district",
        "idx_imoveis_finalidade", "idx_imoveis_situacao",
    ):
        # idx_<table>_<col>
        col = idx.split("_")[-1]
        db.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON imoveis({col})")
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS imoveis_fts USING fts5(
            id UNINDEXED, content, tokenize="porter unicode61"
        )
    """)


def _coerce_row(rec: dict) -> tuple:
    """Converte campos para o tipo certo antes do INSERT (id, ints, floats)."""
    out = []
    for c in IMOVEIS_COLUMNS:
        v = rec.get(c)
        if c in INT_COLS and v is not None:
            try: v = int(v)
            except (TypeError, ValueError): v = None
        elif c in FLOAT_COLS and v is not None:
            try: v = float(v)
            except (TypeError, ValueError): v = None
        out.append(v)
    return tuple(out)


def insert_batch_sqlite(db: sqlite3.Connection, batch: list[dict]) -> None:
    placeholders = ",".join(["?"] * len(IMOVEIS_COLUMNS))
    cols = ",".join(IMOVEIS_COLUMNS)
    db.executemany(
        f"REPLACE INTO imoveis ({cols}) VALUES ({placeholders})",
        [_coerce_row(r) for r in batch],
    )


def update_fts_batch(db: sqlite3.Connection, batch: list[dict]) -> None:
    """Upsert FTS5: deleta id antigo, insere conteúdo novo."""
    for r in batch:
        fields = [
            r.get("full_text"), r.get("street"), r.get("number"), r.get("district"),
            r.get("city"), r.get("state"), r.get("zip"),
            r.get("bedrooms"), r.get("bathrooms"), r.get("garage"),
            r.get("sale_price"), r.get("rental_price"), r.get("built_area"),
            r.get("area_util"), r.get("suites"), r.get("description"),
            r.get("finalidade"), r.get("complement"), r.get("reference"),
            r.get("property_type"),
        ]
        parts = [
            str(v) for v in fields
            if v is not None and str(v).strip() not in ("", "None", "0", "0.00", "0,01")
        ]
        content = " ".join(parts)
        db.execute("DELETE FROM imoveis_fts WHERE id = ?", (r["id"],))
        db.execute("INSERT INTO imoveis_fts (id, content) VALUES (?, ?)", (r["id"], content))


# ── Qdrant ─────────────────────────────────────────────────────────────
def init_qdrant(url: str, api_key: str | None, dim: int) -> tuple[Any, bool]:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        client = QdrantClient(url=url, api_key=api_key or None, timeout=10.0)
        client.get_collections()
        if client.collection_exists(COLLECTION_NAME):
            client.delete_collection(COLLECTION_NAME)
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        log.info("Qdrant OK: %s, collection '%s' (dim=%d)", url, COLLECTION_NAME, dim)
        return client, True
    except Exception as e:
        log.warning("Qdrant indisponível (%s) — seguindo sem embeddings", e)
        return None, False


def upsert_qdrant_batch(client: Any, batch: list[dict], vectors) -> None:
    from qdrant_client.models import PointStruct
    points = []
    for r, v in zip(batch, vectors):
        points.append(PointStruct(
            id=r["id"],
            vector=v.tolist() if hasattr(v, "tolist") else list(v),
            payload={
                "id": r["id"],
                "street": r.get("street") or "",
                "number": r.get("number") or "",
                "district": r.get("district") or "",
                "city": r.get("city") or "",
                "state": r.get("state") or "",
                "zip": r.get("zip") or "",
                "bedrooms": r.get("bedrooms"),
                "bathrooms": r.get("bathrooms"),
                "garage": r.get("garage"),
                "sale_price": r.get("sale_price") or "",
                "rental_price": r.get("rental_price") or "",
                "condo_fee": r.get("condo_fee") or "",
                "built_area": r.get("built_area"),
                "area_util": r.get("area_util"),
                "suites": r.get("suites"),
                "description": r.get("description") or "",
                "finalidade": r.get("finalidade") or "",
                "situacao": r.get("situacao") or "",
                "complement": r.get("complement") or "",
                "property_type": r.get("property_type") or "",
            },
        ))
    client.upsert(collection_name=COLLECTION_NAME, points=points)


# ── Pipeline ───────────────────────────────────────────────────────────
def preview(records: list[dict], n: int = 3) -> None:
    log.info("=" * 60)
    log.info("PREVIEW (primeiros %d de %d):", min(n, len(records)), len(records))
    for i, r in enumerate(records[:n], 1):
        log.info("  [%d] id=%s | %s, %s | R$ %s | %d dorm",
                 i, r.get("id"), r.get("street") or "?", r.get("district") or "?",
                 r.get("sale_price") or "?", r.get("bedrooms") or 0)
    log.info("=" * 60)


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest de imóveis (JSON/JSONL)")
    p.add_argument("file", type=Path, help="Arquivo .json ou .jsonl")
    p.add_argument("--no-qdrant", action="store_true", help="Pula Qdrant/embeddings")
    p.add_argument("--batch-size", type=int, default=256, help="Tamanho do batch")
    p.add_argument("--reindex", action="store_true", help="Limpa FTS5 antes de indexar")
    args = p.parse_args()
    t0 = time.time()

    if not args.file.exists():
        log.error("Arquivo não encontrado: %s", args.file)
        return 2
    log.info("Iniciando ingest: %s (no_qdrant=%s, reindex=%s, batch=%d)",
             args.file, args.no_qdrant, args.reindex, args.batch_size)

    # 1. Parse + validação
    log.info("[1/4] Lendo e validando registros...")
    valid: list[dict] = []
    errors: list[tuple[int, str]] = []
    total_read = 0
    for i, raw in enumerate(iter_records(args.file), 1):
        total_read += 1
        try:
            if "id" not in raw:
                raise ValueError("campo 'id' ausente")
            rec = normalize_record(raw)
            err = validate_record(rec)
            if err:
                raise ValueError(err)
            valid.append(rec)
        except Exception as e:
            errors.append((i, str(e)))
    log.info("  Lidos: %d | Válidos: %d | Erros: %d", total_read, len(valid), len(errors))
    if errors[:3]:
        log.warning("  Exemplos de erro: %s", errors[:3])
    if not valid:
        log.error("Nenhum registro válido para importar.")
        return 3
    preview(valid, n=3)

    # 2. SQLite + FTS5
    log.info("[2/4] Inserindo no SQLite + FTS5...")
    db_path = Path(os.getenv("IMOVEIS_DB", "data/tenant/imoveis.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    init_schema(db)
    if args.reindex:
        log.info("  --reindex: limpando FTS5")
        db.execute("DELETE FROM imoveis_fts")
        db.commit()
    inserted = 0
    for i in tqdm(range(0, len(valid), args.batch_size), desc="  SQLite", unit="batch"):
        batch = valid[i:i + args.batch_size]
        insert_batch_sqlite(db, batch)
        update_fts_batch(db, batch)
        db.commit()
        inserted += len(batch)
    db.close()
    log.info("  SQLite: %d registros inseridos/atualizados", inserted)

    # 3. Qdrant (opcional)
    qdrant_ok = False
    if not args.no_qdrant:
        log.info("[3/4] Gerando embeddings + Qdrant...")
        try:
            from fastembed import TextEmbedding
            log.info("  Carregando modelo %s (pode baixar na 1ª vez)", EMBEDDING_MODEL)
            model = TextEmbedding(model_name=EMBEDDING_MODEL)
            sample_dim = len(list(model.embed(["x"]))[0])
            client, ok = init_qdrant(
                os.getenv("QDRANT_URL", "http://localhost:6333"),
                os.getenv("QDRANT_API_KEY") or None,
                sample_dim,
            )
            if ok:
                qdrant_ok = True
                for i in tqdm(range(0, len(valid), args.batch_size),
                              desc="  Embed+Qdrant", unit="batch"):
                    batch = valid[i:i + args.batch_size]
                    texts = [r.get("full_text") or "imóvel sem descrição" for r in batch]
                    vectors = list(model.embed(texts, batch_size=args.batch_size))
                    upsert_qdrant_batch(client, batch, vectors)
        except ImportError as e:
            log.warning("fastembed ausente (%s) — pulando Qdrant", e)
        except Exception as e:
            log.warning("Falha embeddings/Qdrant: %s", e)
    else:
        log.info("[3/4] --no-qdrant: pulando embeddings")

    # 4. Relatório
    elapsed = time.time() - t0
    log.info("[4/4] Relatório final:")
    log.info("  Arquivo        : %s", args.file)
    log.info("  Total lidos    : %d", total_read)
    log.info("  Válidos        : %d", len(valid))
    log.info("  Inseridos      : %d", inserted)
    log.info("  Erros          : %d", len(errors))
    log.info("  Qdrant         : %s", "OK" if qdrant_ok else "pulado/falhou")
    log.info("  SQLite         : %s", db_path)
    log.info("  Tempo total    : %.2fs", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
