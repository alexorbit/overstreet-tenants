"""Envio de email SMTP (stdlib — sem dependências externas)."""
import smtplib
import asyncio
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from overstreet.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_ENABLED

log = logging.getLogger("overstreet.ai.email")


def _send_sync(to: str, subject: str, html_body: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(SMTP_FROM, to, msg.as_string())
        return True
    except Exception as e:
        log.error(f"SMTP erro para {to}: {e}")
        return False


async def send_email(to: str, subject: str, html_body: str) -> bool:
    """Envia email via SMTP de forma assíncrona."""
    if not SMTP_ENABLED:
        log.warning("SMTP não configurado — email não enviado")
        return False
    return await asyncio.to_thread(_send_sync, to, subject, html_body)


def build_imovel_email_html(imovel: dict, agente_nome: str,
                             agente_telefone: str = "") -> str:
    """Monta HTML do email de imóvel para o cliente."""
    from overstreet.formatters.card import format_card_plain
    card_text = format_card_plain(imovel)
    wa_link = ""
    if agente_telefone:
        import re
        digits = re.sub(r'\D', '', agente_telefone)
        if not digits.startswith("55"):
            digits = "55" + digits
        wa_link = f'<p><a href="https://wa.me/{digits}" style="background:#25D366;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;">💬 Falar com {agente_nome}</a></p>'

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;">
  <h2 style="color:#2C3E50;">🏡 {agente_nome} separou este imóvel para você!</h2>
  <pre style="background:#f8f9fa;padding:15px;border-radius:8px;white-space:pre-wrap;font-size:13px;">{card_text}</pre>
  {wa_link}
  <hr>
  <p style="color:#888;font-size:12px;">OverStreet-Corretor-Agent · Imóveis com inteligência</p>
</body>
</html>"""
