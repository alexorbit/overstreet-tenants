"""OverStreet Admin Dashboard - FastAPI + Jinja2 + Tailwind (single-tenant).

Rotas:
- /onboarding (wizard inicial)
- /login, /logout
- / (overview com stats)
- /imoveis (lista), /imoveis/novo, /imoveis/{id}, /imoveis/{id}/editar, /imoveis/{id}/deletar
- /imoveis/upload (JSON/JSONL), /imoveis/reindex
- /clientes (lista), /clientes/{id}
- /visitas, /visitas/nova
- /followups, /followups/novo, /followups/{id}/concluir
- /settings
- /api/stats, /api/imoveis/search
"""
import json
import os
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from dashboard import auth
from dashboard.db import get_db, init_dashboard_tables, get_setting, set_setting
from overstreet.db import imoveis as db_imoveis
from overstreet.db import clientes as db_clientes
from overstreet.db import visitas as db_visitas
from overstreet.db import followups as db_followups

log = logging.getLogger("dashboard")

# ── App setup ───────────────────────────────────────────────────────────
app = FastAPI(title="OverStreet Admin", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SECRET_KEY,
    max_age=86400,
    https_only=False,
    same_site="lax",
)

TPL_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TPL_DIR))


# ── Jinja2 globals ──────────────────────────────────────────────────────
def _fmt_date(v):
    if not v:
        return "—"
    try:
        return datetime.fromtimestamp(float(v)).strftime("%d/%m/%Y")
    except Exception:
        return str(v)


def _fmt_brl(v):
    if v is None or v == "":
        return "—"
    try:
        return f"R$ {float(v):,.0f}".replace(",", ".")
    except Exception:
        return str(v)


templates.env.globals["fmt_date"] = _fmt_date
templates.env.globals["fmt_brl"] = _fmt_brl
templates.env.globals["now"] = lambda: datetime.now().strftime("%d/%m/%Y %H:%M")


# ── Flash helpers ───────────────────────────────────────────────────────
def flash(request: Request, message: str, kind: str = "success"):
    request.session["flash"] = {"kind": kind, "message": message}


def pop_flash(request: Request):
    return request.session.pop("flash", None)


# ── Guard helper ────────────────────────────────────────────────────────
def _g(request: Request):
    return auth.require_auth(request)


# ── Startup ─────────────────────────────────────────────────────────────
@app.on_event("startup")
def _startup():
    log.warning("DASHBOARD_PASSWORD loaded: %r (len=%d)", auth.DASHBOARD_PASSWORD, len(auth.DASHBOARD_PASSWORD))
    db = get_db()
    init_dashboard_tables(db)
    log.info("Dashboard ready.")


# ════════════════════════════════════════════════════════════════════════
#  ONBOARDING
# ════════════════════════════════════════════════════════════════════════
ONBOARD_DONE_KEY = "onboarding_complete"


def _onboarded(db) -> bool:
    return get_setting(db, ONBOARD_DONE_KEY, "0") == "1"


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_get(request: Request):
    db = get_db()
    if _onboarded(db) and auth.is_authed(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "onboarding.html",
        {
            "done": _onboarded(db),
            "error": None,
            "prefill": {
                "agente_nome": get_setting(db, "agente_nome", "Ana"),
                "max_results": get_setting(db, "max_results", "5"),
                "mensagem_boas_vindas": get_setting(
                    db, "mensagem_boas_vindas",
                    "Olá! Sou a Ana, sua assistente imobiliária. Como posso ajudar?"
                ),
                "telegram_id_admin": get_setting(db, "telegram_id_admin", ""),
            },
        }
    )


@app.post("/onboarding")
async def onboarding_post(
    request: Request,
    agente_nome: str = Form(...),
    max_results: str = Form("5"),
    mensagem_boas_vindas: str = Form(""),
    telegram_id_admin: str = Form(""),
):
    db = get_db()
    set_setting(db, "agente_nome", agente_nome.strip() or "Ana")
    set_setting(db, "max_results", str(max_results).strip() or "5")
    set_setting(db, "mensagem_boas_vindas", mensagem_boas_vindas.strip())
    set_setting(db, "telegram_id_admin", telegram_id_admin.strip())
    set_setting(db, ONBOARD_DONE_KEY, "1")
    flash(request, "Onboarding concluído. Bem-vindo!", "success")
    auth.login(request)
    return RedirectResponse("/", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════════════════
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if auth.is_authed(request):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "flash": pop_flash(request)}
    )


@app.post("/login")
async def login_post(request: Request, password: str = Form(...)):
    if auth.check_password(password):
        auth.login(request)
        flash(request, "Bem-vindo de volta.", "success")
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Senha incorreta.", "flash": None},
        status_code=200,
    )


@app.get("/logout")
async def logout(request: Request):
    auth.logout(request)
    return RedirectResponse("/login", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  OVERVIEW
# ════════════════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    mem = None
    try:
        from dashboard.db import get_mem
        mem = get_mem()
    except Exception:
        pass
    total = db.execute("SELECT COUNT(*) FROM imoveis").fetchone()[0]
    disponiveis = db.execute(
        "SELECT COUNT(*) FROM imoveis WHERE situacao='Disponivel' OR situacao IS NULL"
    ).fetchone()[0]
    vendidos = db.execute(
        "SELECT COUNT(*) FROM imoveis WHERE situacao IN ('Vendido','Alugado','Vendido/Alugado')"
    ).fetchone()[0]
    n_clientes = db.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    visitas_hoje = db.execute(
        "SELECT COUNT(*) FROM visitas WHERE status='agendada' "
        "AND date(data_visita) = date('now','localtime')"
    ).fetchone()[0]
    visitas_proximas = db.execute(
        "SELECT COUNT(*) FROM visitas WHERE status='agendada' "
        "AND date(data_visita) BETWEEN date('now','localtime') "
        "AND date('now','localtime','+7 days')"
    ).fetchone()[0]
    followups_pendentes = db.execute(
        "SELECT COUNT(*) FROM followups WHERE status='pendente'"
    ).fetchone()[0]
    msgs_hoje = 0
    if mem is not None:
        try:
            msgs_hoje = mem.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE date(ts, 'unixepoch','localtime') = date('now','localtime')"
            ).fetchone()[0]
        except Exception:
            msgs_hoje = 0
    return templates.TemplateResponse(
        request, "index.html",
        {
            "flash": pop_flash(request),
            "stats": {
                "total": total, "disponiveis": disponiveis,
                "vendidos": vendidos, "clientes": n_clientes,
                "visitas_hoje": visitas_hoje,
                "visitas_proximas": visitas_proximas,
                "followups_pendentes": followups_pendentes,
                "msgs_hoje": msgs_hoje,
            },
            "agente_nome": get_setting(db, "agente_nome", "Ana"),
        }
    )


# ════════════════════════════════════════════════════════════════════════
#  IMÓVEIS — uploader e rotas estáticas ANTES de /imoveis/{id}
# ════════════════════════════════════════════════════════════════════════
@app.get("/imoveis/upload", response_class=HTMLResponse)
async def imovel_upload_get(request: Request):
    r = _g(request)
    if r:
        return r
    return templates.TemplateResponse(
        request, "imovel_upload.html",
        {"flash": pop_flash(request), "error": None, "preview": None}
    )


@app.post("/imoveis/upload")
async def imovel_upload_post(request: Request, file: UploadFile = File(...)):
    r = _g(request)
    if r:
        return r
    MAX = 50 * 1024 * 1024
    raw = await file.read()
    if len(raw) > MAX:
        return templates.TemplateResponse(
            request, "imovel_upload.html",
            {"flash": None, "error": f"Arquivo muito grande ({len(raw)//1024//1024} MB). Máx 50 MB.", "preview": None},
            status_code=400,
        )
    name = (file.filename or "").lower()
    try:
        if name.endswith(".jsonl") or name.endswith(".ndjson"):
            records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        elif name.endswith(".json"):
            records = json.loads(raw.decode("utf-8"))
            if isinstance(records, dict):
                records = [records]
        else:
            return templates.TemplateResponse(
                request, "imovel_upload.html",
                {"flash": None, "error": "Formato não suportado. Use .json ou .jsonl.", "preview": None},
                status_code=400,
            )
    except Exception as e:
        return templates.TemplateResponse(
            request, "imovel_upload.html",
            {"flash": None, "error": f"Erro parseando arquivo: {e}", "preview": None},
            status_code=400,
        )
    if not isinstance(records, list) or not records:
        return templates.TemplateResponse(
            request, "imovel_upload.html",
            {"flash": None, "error": "Arquivo vazio ou inválido.", "preview": None},
            status_code=400,
        )
    if request.query_params.get("confirm") != "1":
        return templates.TemplateResponse(
            request, "imovel_upload.html",
            {"flash": None, "error": None, "preview": {
                "filename": file.filename, "count": len(records),
                "first": records[0] if records else {},
            }}
        )
    db = get_db()
    n_ok, n_err = 0, 0
    for rec in records:
        try:
            db_imoveis.insert_imovel(db, rec)
            n_ok += 1
        except Exception as e:
            n_err += 1
            log.warning("Falha importando registro: %s", e)
    flash(request, f"Importação concluída: {n_ok} ok, {n_err} erros.", "success")
    return RedirectResponse("/imoveis", status_code=302)


@app.post("/imoveis/reindex")
async def imovel_reindex(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    n = 0
    try:
        db.execute("DELETE FROM imoveis_fts")
        for row in db.execute("SELECT id, full_text FROM imoveis").fetchall():
            ft = row[1] or ""
            db.execute(
                "INSERT OR REPLACE INTO imoveis_fts (id, content) VALUES (?, ?)",
                (row[0], ft),
            )
            n += 1
        db.commit()
    except Exception as e:
        flash(request, f"Erro reindexando FTS: {e}", "error")
        return RedirectResponse("/imoveis", status_code=302)
    flash(request, f"FTS reindexado: {n} imóveis.", "success")
    return RedirectResponse("/imoveis", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  IMÓVEIS — lista, criar, detalhe, editar, deletar
# ════════════════════════════════════════════════════════════════════════
@app.get("/imoveis", response_class=HTMLResponse)
async def imoveis_list(
    request: Request, q: str = "", page: int = 1, situacao: str = ""
):
    r = _g(request)
    if r:
        return r
    db = get_db()
    per_page = 20
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(description LIKE ? OR street LIKE ? OR district LIKE ? OR reference LIKE ?)")
        params.extend([like, like, like, like])
    if situacao:
        where.append("situacao = ?")
        params.append(situacao)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM imoveis {where_sql}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM imoveis {where_sql} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, max(0, (page - 1) * per_page)]
    ).fetchall()
    desc = db.execute("SELECT * FROM imoveis LIMIT 0").description
    cols = [d[0] for d in desc] if desc else []
    items = [dict(zip(cols, row)) for row in rows]
    pages = max(1, (total + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request, "imoveis.html",
        {"flash": pop_flash(request), "items": items, "q": q,
         "situacao": situacao, "page": page, "pages": pages, "total": total,
         "page_id": "imoveis"}
    )


@app.get("/imoveis/novo", response_class=HTMLResponse)
async def imovel_novo_get(request: Request):
    r = _g(request)
    if r:
        return r
    return templates.TemplateResponse(
        request, "imovel_form.html",
        {"flash": pop_flash(request), "item": {}, "is_new": True, "error": None}
    )


@app.post("/imoveis/novo")
async def imovel_novo_post(request: Request):
    r = _g(request)
    if r:
        return r
    form = await request.form()
    data = {k: v for k, v in form.items() if v not in ("", None)}
    db = get_db()
    try:
        new_id = db_imoveis.insert_imovel(db, data)
        flash(request, f"Imóvel COD {new_id} criado.", "success")
        return RedirectResponse(f"/imoveis/{new_id}", status_code=302)
    except Exception as e:
        log.exception("Erro criando imovel")
        return templates.TemplateResponse(
            request, "imovel_form.html",
            {"flash": None, "item": dict(form), "is_new": True, "error": str(e)},
            status_code=400,
        )


@app.get("/imoveis/{imovel_id}", response_class=HTMLResponse)
async def imovel_detail(request: Request, imovel_id: int):
    r = _g(request)
    if r:
        return r
    db = get_db()
    item = db_imoveis.get_imovel_by_id(db, imovel_id)
    if not item:
        raise HTTPException(status_code=404, detail="Imóvel não encontrado")
    fotos = db_imoveis.get_fotos(db, imovel_id)
    return templates.TemplateResponse(
        request, "imovel_detail.html",
        {"flash": pop_flash(request), "item": item, "fotos": fotos}
    )


@app.get("/imoveis/{imovel_id}/editar", response_class=HTMLResponse)
async def imovel_editar_get(request: Request, imovel_id: int):
    r = _g(request)
    if r:
        return r
    db = get_db()
    item = db_imoveis.get_imovel_by_id(db, imovel_id)
    if not item:
        raise HTTPException(status_code=404, detail="Imóvel não encontrado")
    return templates.TemplateResponse(
        request, "imovel_form.html",
        {"flash": pop_flash(request), "item": item, "is_new": False, "error": None}
    )


@app.post("/imoveis/{imovel_id}/editar")
async def imovel_editar_post(request: Request, imovel_id: int):
    r = _g(request)
    if r:
        return r
    form = await request.form()
    data = {k: v for k, v in form.items() if v not in ("", None)}
    db = get_db()
    try:
        db_imoveis.update_imovel(db, imovel_id, data)
        flash(request, f"Imóvel COD {imovel_id} atualizado.", "success")
        return RedirectResponse(f"/imoveis/{imovel_id}", status_code=302)
    except Exception as e:
        log.exception("Erro atualizando imovel")
        return templates.TemplateResponse(
            request, "imovel_form.html",
            {"flash": None, "item": dict(form), "is_new": False, "error": str(e)},
            status_code=400,
        )


@app.post("/imoveis/{imovel_id}/deletar")
async def imovel_deletar(request: Request, imovel_id: int):
    r = _g(request)
    if r:
        return r
    db = get_db()
    if db_imoveis.delete_imovel(db, imovel_id):
        flash(request, f"Imóvel COD {imovel_id} removido.", "success")
    else:
        flash(request, "Não foi possível remover.", "error")
    return RedirectResponse("/imoveis", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  CLIENTES
# ════════════════════════════════════════════════════════════════════════
@app.get("/clientes", response_class=HTMLResponse)
async def clientes_list(request: Request, q: str = ""):
    r = _g(request)
    if r:
        return r
    db = get_db()
    if q:
        rows = db_clientes.search_clientes(db, q)
    else:
        rows = db_clientes.list_clientes(db)
    return templates.TemplateResponse(
        request, "clientes.html",
        {"flash": pop_flash(request), "items": rows, "q": q, "page_id": "clientes"}
    )


@app.get("/clientes/{cliente_id}", response_class=HTMLResponse)
async def cliente_detail(request: Request, cliente_id: int):
    r = _g(request)
    if r:
        return r
    db = get_db()
    mem = None
    try:
        from dashboard.db import get_mem
        mem = get_mem()
    except Exception:
        pass
    cliente = db_clientes.get_cliente_by_id(db, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    user_id = None
    if cliente.get("whatsapp"):
        try:
            user_id = int(str(cliente["whatsapp"]).lstrip("+").split("@")[0])
        except Exception:
            user_id = None
    mensagens = []
    profile = None
    if mem and user_id:
        try:
            rows = mem.execute(
                "SELECT role, content, ts FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 50",
                (user_id,),
            ).fetchall()
            mensagens = [{"role": r[0], "content": r[1], "ts": r[2]} for r in rows]
            prow = mem.execute(
                "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            if prow:
                desc = mem.execute("SELECT * FROM user_profiles LIMIT 0").description
                cols = [d[0] for d in desc]
                profile = dict(zip(cols, prow))
        except Exception as e:
            log.warning("Erro ao carregar mensagens do cliente: %s", e)
    return templates.TemplateResponse(
        request, "cliente_detail.html",
        {"flash": pop_flash(request), "cliente": cliente,
         "mensagens": mensagens, "profile": profile}
    )


# ════════════════════════════════════════════════════════════════════════
#  VISITAS
# ════════════════════════════════════════════════════════════════════════
@app.get("/visitas", response_class=HTMLResponse)
async def visitas_list(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    rows = db.execute(
        "SELECT * FROM visitas ORDER BY data_visita DESC LIMIT 100"
    ).fetchall()
    desc = db.execute("SELECT * FROM visitas LIMIT 0").description
    cols = [d[0] for d in desc] if desc else []
    items = [dict(zip(cols, row)) for row in rows]
    return templates.TemplateResponse(
        request, "visitas.html",
        {"flash": pop_flash(request), "items": items, "page_id": "visitas"}
    )


@app.get("/visitas/nova", response_class=HTMLResponse)
async def visita_nova_get(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    imoveis = db.execute("SELECT id, street, district FROM imoveis ORDER BY id DESC LIMIT 50").fetchall()
    return templates.TemplateResponse(
        request, "visita_nova.html",
        {"flash": pop_flash(request), "imoveis": imoveis, "error": None}
    )


@app.post("/visitas/nova")
async def visita_nova_post(
    request: Request,
    imovel_id: int = Form(...),
    cliente_nome: str = Form(""),
    data_visita: str = Form(...),
    notas: str = Form(""),
):
    r = _g(request)
    if r:
        return r
    db = get_db()
    try:
        db_visitas.insert_visita(
            db, imovel_id=imovel_id, cliente_nome=cliente_nome,
            data_visita=data_visita, notas=notas,
        )
        flash(request, "Visita agendada.", "success")
    except Exception as e:
        flash(request, f"Erro: {e}", "error")
    return RedirectResponse("/visitas", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  FOLLOW-UPS
# ════════════════════════════════════════════════════════════════════════
@app.get("/followups", response_class=HTMLResponse)
async def followups_list(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    rows = db.execute(
        "SELECT * FROM followups ORDER BY data_prazo ASC LIMIT 200"
    ).fetchall()
    desc = db.execute("SELECT * FROM followups LIMIT 0").description
    cols = [d[0] for d in desc] if desc else []
    items = [dict(zip(cols, row)) for row in rows]
    return templates.TemplateResponse(
        request, "followups.html",
        {"flash": pop_flash(request), "items": items, "page_id": "followups"}
    )


@app.get("/followups/novo", response_class=HTMLResponse)
async def followup_novo_get(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    clientes = db.execute("SELECT id, nome FROM clientes ORDER BY nome").fetchall()
    imoveis = db.execute("SELECT id, street, district FROM imoveis ORDER BY id DESC LIMIT 50").fetchall()
    return templates.TemplateResponse(
        request, "followup_novo.html",
        {"flash": pop_flash(request), "clientes": clientes,
         "imoveis": imoveis, "error": None}
    )


@app.post("/followups/novo")
async def followup_novo_post(
    request: Request,
    tipo: str = Form(...),
    data_prazo: str = Form(...),
    descricao: str = Form(""),
    cliente_nome: str = Form(""),
    imovel_id: int = Form(0),
):
    r = _g(request)
    if r:
        return r
    db = get_db()
    try:
        db_followups.insert_followup(
            db, tipo=tipo, data_prazo=data_prazo, descricao=descricao,
            cliente_nome=cliente_nome or None,
            imovel_id=imovel_id or None,
        )
        flash(request, "Follow-up criado.", "success")
    except Exception as e:
        flash(request, f"Erro: {e}", "error")
    return RedirectResponse("/followups", status_code=302)


@app.post("/followups/{followup_id}/concluir")
async def followup_concluir(request: Request, followup_id: int):
    r = _g(request)
    if r:
        return r
    db = get_db()
    db.execute(
        "UPDATE followups SET status='concluido', concluido_em=datetime('now','localtime') WHERE id=?",
        (followup_id,),
    )
    db.commit()
    flash(request, "Follow-up concluído.", "success")
    return RedirectResponse("/followups", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ════════════════════════════════════════════════════════════════════════
@app.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "flash": pop_flash(request),
            "settings": {
                "agente_nome": get_setting(db, "agente_nome", "Ana"),
                "max_results": get_setting(db, "max_results", "5"),
                "mensagem_boas_vindas": get_setting(
                    db, "mensagem_boas_vindas",
                    "Olá! Sou a Ana, sua assistente imobiliária. Como posso ajudar?"
                ),
                "telegram_id_admin": get_setting(db, "telegram_id_admin", ""),
            },
            "env": {
                "bot_token_last4": (os.getenv("BOT_TOKEN", "") or "")[-4:],
                "nvidia_model": os.getenv("NVIDIA_MODEL", "qwen/qwen3.5-122b-a10b"),
                "qdrant_url": os.getenv("QDRANT_URL", "http://localhost:6333"),
            },
            "page_id": "settings",
        }
    )


@app.post("/settings")
async def settings_post(
    request: Request,
    agente_nome: str = Form(...),
    max_results: str = Form("5"),
    mensagem_boas_vindas: str = Form(""),
    telegram_id_admin: str = Form(""),
):
    r = _g(request)
    if r:
        return r
    db = get_db()
    set_setting(db, "agente_nome", agente_nome.strip() or "Ana")
    set_setting(db, "max_results", str(max_results).strip() or "5")
    set_setting(db, "mensagem_boas_vindas", mensagem_boas_vindas.strip())
    set_setting(db, "telegram_id_admin", telegram_id_admin.strip())
    flash(request, "Configurações salvas.", "success")
    return RedirectResponse("/settings", status_code=302)


# ════════════════════════════════════════════════════════════════════════
#  API JSON
# ════════════════════════════════════════════════════════════════════════
@app.get("/api/stats", response_class=JSONResponse)
async def api_stats(request: Request):
    r = _g(request)
    if r:
        return r
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM imoveis").fetchone()[0]
    disponiveis = db.execute(
        "SELECT COUNT(*) FROM imoveis WHERE situacao='Disponivel' OR situacao IS NULL"
    ).fetchone()[0]
    n_clientes = db.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]
    visitas_hoje = db.execute(
        "SELECT COUNT(*) FROM visitas WHERE status='agendada' AND date(data_visita) = date('now','localtime')"
    ).fetchone()[0]
    followups_pendentes = db.execute(
        "SELECT COUNT(*) FROM followups WHERE status='pendente'"
    ).fetchone()[0]
    return {
        "total_imoveis": total,
        "disponiveis": disponiveis,
        "clientes": n_clientes,
        "visitas_hoje": visitas_hoje,
        "followups_pendentes": followups_pendentes,
    }


@app.get("/api/imoveis/search", response_class=JSONResponse)
async def api_search(request: Request, q: str = ""):
    r = _g(request)
    if r:
        return r
    db = get_db()
    if not q:
        return {"results": []}
    like = f"%{q}%"
    rows = db.execute(
        "SELECT id, street, district, bedrooms, sale_price, rental_price "
        "FROM imoveis WHERE description LIKE ? OR street LIKE ? OR district LIKE ? "
        "LIMIT 20",
        (like, like, like),
    ).fetchall()
    desc = db.execute("SELECT id, street, district, bedrooms, sale_price, rental_price FROM imoveis LIMIT 0").description
    cols = [d[0] for d in desc] if desc else []
    return {"results": [dict(zip(cols, row)) for row in rows]}
