"""Two-tier memory system (single-tenant):

  Tier 1 (shared_db)   — cross-session learnings (shared_insights, knowledge_entries)
  Tier 2 (memory_db)   — private memory (messages, user_profiles)

Em single-tenant não há `tenant_id`: o `memory_db` é único e todos os
usuários compartilham o mesmo banco. As funções de alto nível são
diretamente operáveis.
"""
import json
import time
import logging
from collections import Counter

log = logging.getLogger("overstreet.db.memory")


# ── DB getters (single-tenant) ───────────────────────────────────────────

def _get_memory_db():
    from overstreet.infra import get_memory_db
    return get_memory_db()


def _get_shared_db():
    from overstreet.infra import get_shared_db
    return get_shared_db()


# ═══════════════════════════════════════════════════════════════════════
#  TIER 2: PRIVATE MEMORY — messages, user profiles (sem tenant_id)
# ═══════════════════════════════════════════════════════════════════════

def save_message(user_id: int, role: str, content: str) -> None:
    """Persiste uma mensagem (user/assistant) na memória privada."""
    db = _get_memory_db()
    db.execute(
        "INSERT INTO messages (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
        (user_id, role, content[:4000], time.time())
    )
    db.commit()


def load_history(user_id: int, limit: int = 40) -> list[dict]:
    """Retorna as últimas `limit` mensagens do usuário (em ordem cronológica)."""
    db = _get_memory_db()
    rows = db.execute(
        "SELECT role, content FROM messages WHERE user_id=? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def get_or_create_profile(user_id: int, nome: str = "") -> dict:
    """Busca perfil do usuário ou cria um novo. Schema single-tenant.

    Campos retornados: user_id, nome, preferencias, contexto_resumo,
    total_mensagens, ultima_atividade.
    """
    db = _get_memory_db()
    cols = ["user_id", "nome", "preferencias", "contexto_resumo",
            "total_mensagens", "ultima_atividade"]
    row = db.execute(
        "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        now = time.time()
        db.execute(
            "INSERT INTO user_profiles (user_id, nome, ultima_atividade) VALUES (?, ?, ?)",
            (user_id, nome, now)
        )
        db.commit()
        return {
            "user_id": user_id, "nome": nome,
            "preferencias": "{}", "contexto_resumo": "",
            "total_mensagens": 0, "ultima_atividade": now,
        }
    d = dict(zip(cols, row))
    if nome and not d.get("nome"):
        db.execute(
            "UPDATE user_profiles SET nome=? WHERE user_id=?",
            (nome, user_id)
        )
        db.commit()
        d["nome"] = nome
    return d


def update_profile(user_id: int, **fields) -> None:
    """Atualiza campos do perfil (preferencias, contexto_resumo, etc)."""
    if not fields:
        return
    db = _get_memory_db()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    db.execute(
        f"UPDATE user_profiles SET {set_clause}, ultima_atividade=? WHERE user_id=?",
        list(fields.values()) + [time.time(), user_id]
    )
    db.commit()


def merge_search_preferences(user_id: int, search_args: dict) -> None:
    """Mescla preferências de busca no perfil (bairros, tipos, quartos, preços)."""
    db = _get_memory_db()
    row = db.execute(
        "SELECT preferencias FROM user_profiles WHERE user_id=?",
        (user_id,)
    ).fetchone()
    if not row:
        return
    try:
        prefs = json.loads(row[0] or "{}")
    except Exception:
        prefs = {}

    for k, v in search_args.items():
        if v is None:
            continue
        if k in ("bairro", "tipo"):
            bucket = f"{k}s_buscados"
            prefs.setdefault(bucket, {})
            key = str(v).lower()
            prefs[bucket][key] = prefs[bucket].get(key, 0) + 1
        elif k == "quartos_min":
            prefs.setdefault("quartos_buscados", [])
            prefs["quartos_buscados"].append(int(v))
            prefs["quartos_buscados"] = prefs["quartos_buscados"][-20:]
        elif k in ("preco_max", "aluguel_max"):
            bucket = "precos_buscados"
            prefs.setdefault(bucket, [])
            prefs[bucket].append(int(v))
            prefs[bucket] = prefs[bucket][-20:]

    db.execute(
        "UPDATE user_profiles SET preferencias=?, total_mensagens=total_mensagens+1 "
        "WHERE user_id=?",
        (json.dumps(prefs, ensure_ascii=False), user_id)
    )
    db.commit()


def format_preferences(profile: dict) -> str:
    """Formata preferências do perfil como string curta para o system prompt."""
    try:
        prefs = json.loads(profile.get("preferencias") or "{}")
    except Exception:
        return ""

    parts: list[str] = []

    bairros = prefs.get("bairros_buscados", {})
    if bairros:
        top = sorted(bairros.items(), key=lambda x: x[1], reverse=True)[:3]
        parts.append("bairros: " + ", ".join(b for b, _ in top))

    tipos = prefs.get("tipos_buscados", {})
    if tipos:
        top = sorted(tipos.items(), key=lambda x: x[1], reverse=True)[:2]
        parts.append("tipos: " + ", ".join(t for t, _ in top))

    quartos = prefs.get("quartos_buscados", [])
    if quartos:
        c = Counter(quartos)
        parts.append(f"{c.most_common(1)[0][0]} dorms mais buscados")

    precos = prefs.get("precos_buscados", [])
    if precos:
        avg = int(sum(precos) / len(precos))
        parts.append(f"faixa ~R${avg:,}".replace(",", "."))

    return " · ".join(parts) if parts else ""


# ═══════════════════════════════════════════════════════════════════════
#  TIER 1: SHARED MEMORY (global) — self-improvement
# ═══════════════════════════════════════════════════════════════════════

def add_shared_insight(category: str, content: str,
                       quality_score: float = 0.5) -> None:
    """Adiciona um insight aprendido à memória compartilhada (self-improvement)."""
    db = _get_shared_db()
    try:
        db.execute(
            "INSERT INTO shared_insights (category, content, quality_score, created_at) "
            "VALUES (?, ?, ?, ?)",
            (category, content[:2000], quality_score, time.time())
        )
        db.commit()
    except Exception as e:
        log.warning("Shared insight error: %s", e)


def add_knowledge(topic: str, fact: str, confidence: float = 0.8,
                  source: str = "learned") -> None:
    """Adiciona uma entrada de conhecimento à memória compartilhada."""
    db = _get_shared_db()
    try:
        db.execute(
            "INSERT INTO knowledge_entries (topic, fact, confidence, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (topic, fact[:2000], confidence, source, time.time())
        )
        db.commit()
    except Exception as e:
        log.warning("Knowledge entry error: %s", e)


def get_shared_insights_for_prompt(limit: int = 10) -> str:
    """Formata top insights compartilhados para injeção no system prompt.

    Retorna string vazia se não houver insights qualificados.
    """
    db = _get_shared_db()
    try:
        rows = db.execute(
            "SELECT category, content FROM shared_insights "
            "WHERE quality_score >= 0.6 "
            "ORDER BY quality_score DESC, times_used DESC LIMIT ?",
            (limit,)
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return ""

    lines = [f"[{cat}] {content}" for cat, content in rows]
    return "CONHECIMENTO COMPARTILHADO (aprendido em sessões anteriores):\n" + "\n".join(lines)


def get_shared_knowledge_for_prompt(limit: int = 8) -> str:
    """Formata entradas de conhecimento para o system prompt."""
    db = _get_shared_db()
    try:
        rows = db.execute(
            "SELECT topic, fact FROM knowledge_entries "
            "WHERE confidence >= 0.7 "
            "ORDER BY confidence DESC LIMIT ?",
            (limit,)
        ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return ""

    lines = [f"• {topic}: {fact}" for topic, fact in rows]
    return "BASE DE CONHECIMENTO IMOBILIÁRIO:\n" + "\n".join(lines)
