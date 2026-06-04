"""Dashboard auth: 1 senha via env var, sessao via SessionMiddleware."""
import os
from fastapi import Request
from fastapi.responses import RedirectResponse

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "overstreet2024")
SECRET_KEY = os.getenv("DASHBOARD_SECRET", "overstreet-dev-secret-CHANGE-ME")


def is_authed(request: Request) -> bool:
    return request.session.get("authed") is True


def require_auth(request: Request):
    """Dependencia: redireciona pra /login se nao autenticado. Retorna None se OK."""
    if not is_authed(request):
        return RedirectResponse("/login", status_code=302)
    return None


def check_password(password: str) -> bool:
    return password == DASHBOARD_PASSWORD


def login(request: Request):
    request.session["authed"] = True


def logout(request: Request):
    request.session.clear()
