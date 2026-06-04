"""Handler de controle de chaves de imóveis."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

STATUS_EMOJIS = {
    "imobiliaria": "🏢",
    "com_corretor": "👤",
    "com_proprietario": "🏠",
    "perdida": "❌",
}

STATUS_LABELS = {
    "imobiliaria": "Na Imobiliária",
    "com_corretor": "Com Corretor",
    "com_proprietario": "Com Proprietário",
    "perdida": "Perdida",
}


def _format_chave_lista(chaves: list[dict]) -> str:
    """Formata lista de chaves."""
    if not chaves:
        return "Nenhuma chave encontrada."

    lines = ["🔑 <b>Controle de Chaves</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
    for c in chaves:
        status = c.get("status", "imobiliaria")
        emoji = STATUS_EMOJIS.get(status, "❓")
        label = STATUS_LABELS.get(status, status)
        imovel_id = c.get("imovel_id", "?")
        local = c.get("local") or "—"
        retirada_por = c.get("retirada_por") or ""
        devolvida_em = c.get("devolvida_em") or ""

        lines.append(f"{emoji} <b>Chave #{c['id']}</b> — Imóvel COD {imovel_id}")
        lines.append(f"  Status: {label}")

        if status == "com_corretor" and retirada_por:
            lines.append(f"  Retirada por: {retirada_por}")
        if devolvida_em:
            lines.append(f"  Devolvida em: {devolvida_em}")
        lines.append(f"  Local: {local}")
        lines.append("")

    lines.append(f"<i>Total: {len(chaves)} chave(s)</i>")
    return "\n".join(lines)


@router.message(Command("chaves"))
async def cmd_chaves(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Lista chaves. Uso: /chaves [status]"""
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    from overstreet.db.chaves import list_chaves

    args = message.text.removeprefix("/chaves").strip().lower()
    status = None
    status_label = ""

    # Verificar se passou um status
    valid_names = {
        "imobiliaria": "imobiliaria",
        "imobiliária": "imobiliaria",
        "corretor": "com_corretor",
        "com_corretor": "com_corretor",
        "proprietario": "com_proprietario",
        "proprietário": "com_proprietario",
        "com_proprietario": "com_proprietario",
        "perdida": "perdida",
        "perdida": "perdida",
    }
    if args:
        status = valid_names.get(args)
        if status:
            status_label = f" ({STATUS_LABELS.get(status, args)})"

    chaves = list_chaves(tenant_conn, status)
    texto = _format_chave_lista(chaves)
    if status_label:
        texto = texto.replace("Controle de Chaves",
                             f"Controle de Chaves{status_label}")
    await message.answer(texto, parse_mode="HTML")


@router.message(Command("chave"))
async def cmd_chave(message: Message, tenant_conn=None, tenant=None, **kwargs):
    """Gerencia chaves. Uso:
    /chave retirar COD
    /chave devolver COD
    /chave local COD escritório principal
    """
    if not tenant_conn:
        await message.answer("❌ Não consegui acessar o banco de dados.")
        return

    from overstreet.db.chaves import (
        get_chave_by_imovel, retirar_chave, devolver_chave,
        set_chave_local, registrar_chave
    )
    from overstreet.db.imoveis import _query_dict

    args = message.text.removeprefix("/chave").strip()
    parts = args.split(maxsplit=1)

    if not parts or not parts[0]:
        await message.answer(
            "🔑 Use:\n"
            "  /chave retirar <b>COD</b> — registrar retirada\n"
            "  /chave devolver <b>COD</b> — registrar devolução\n"
            "  /chave local <b>COD local</b> — registrar localização\n"
            "  /chave registrar <b>COD</b> — cadastrar chave para o imóvel",
            parse_mode="HTML"
        )
        return

    acao = parts[0].lower()

    if acao == "retirar":
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("❌ Use: /chave retirar <b>COD</b>", parse_mode="HTML")
            return
        try:
            cod = int(parts[1].strip().split()[0])
        except ValueError:
            await message.answer("❌ Código inválido.")
            return

        # Verificar se imóvel existe
        imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (cod,))
        if not imovel:
            await message.answer(f"❌ Imóvel COD {cod} não encontrado.")
            return

        # Buscar chave
        chave = get_chave_by_imovel(tenant_conn, cod)
        if not chave:
            await message.answer(
                f"⚠️ Nenhuma chave registrada para COD {cod}.\n"
                f"Use /chave registrar {cod} para cadastrar."
            )
            return

        nome_user = message.from_user.full_name or message.from_user.username or "Desconhecido"
        ok = retirar_chave(tenant_conn, chave["id"], nome_user)
        if ok:
            await message.answer(
                f"✅ Chave do imóvel <b>COD {cod}</b> retirada com sucesso!\n"
                f"👤 Retirada por: {nome_user}",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Erro ao registrar retirada.")

    elif acao == "devolver":
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("❌ Use: /chave devolver <b>COD</b>", parse_mode="HTML")
            return
        try:
            cod = int(parts[1].strip().split()[0])
        except ValueError:
            await message.answer("❌ Código inválido.")
            return

        imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (cod,))
        if not imovel:
            await message.answer(f"❌ Imóvel COD {cod} não encontrado.")
            return

        chave = get_chave_by_imovel(tenant_conn, cod)
        if not chave:
            await message.answer(f"⚠️ Nenhuma chave registrada para COD {cod}.")
            return

        ok = devolver_chave(tenant_conn, chave["id"])
        if ok:
            await message.answer(
                f"✅ Chave do imóvel <b>COD {cod}</b> devolvida!\n"
                f"🏢 Status: Na Imobiliária",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Erro ao registrar devolução.")

    elif acao == "local":
        if len(parts) < 2:
            await message.answer("❌ Use: /chave local <b>COD localização</b>", parse_mode="HTML")
            return
        sub_parts = parts[1].strip().split(maxsplit=1)
        if len(sub_parts) < 2:
            await message.answer("❌ Use: /chave local <b>COD localização</b>", parse_mode="HTML")
            return
        try:
            cod = int(sub_parts[0])
        except ValueError:
            await message.answer("❌ Código inválido.")
            return
        local = sub_parts[1].strip()

        chave = get_chave_by_imovel(tenant_conn, cod)
        if not chave:
            await message.answer(f"⚠️ Nenhuma chave registrada para COD {cod}.")
            return

        ok = set_chave_local(tenant_conn, chave["id"], local)
        if ok:
            await message.answer(
                f"📍 Local da chave <b>COD {cod}</b> atualizado: <b>{local}</b>",
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Erro ao atualizar local.")

    elif acao == "registrar":
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("❌ Use: /chave registrar <b>COD</b>", parse_mode="HTML")
            return
        try:
            cod = int(parts[1].strip().split()[0])
        except ValueError:
            await message.answer("❌ Código inválido.")
            return

        imovel = _query_dict(tenant_conn, "SELECT * FROM imoveis WHERE id = ?", (cod,))
        if not imovel:
            await message.answer(f"❌ Imóvel COD {cod} não encontrado.")
            return

        # Verificar se já existe chave
        existente = get_chave_by_imovel(tenant_conn, cod)
        if existente:
            await message.answer(
                f"⚠️ Já existe chave registrada para COD {cod} (#{existente['id']}).\n"
                f"Use /chave retirar {cod} ou /chave local {cod} ..."
            )
            return

        chave_id = registrar_chave(tenant_conn, cod)
        await message.answer(
            f"✅ Chave <b>#{chave_id}</b> registrada para imóvel <b>COD {cod}</b>!\n"
            f"🏢 Status: Na Imobiliária",
            parse_mode="HTML"
        )

    else:
        await message.answer(
            "❓ Ação desconhecida. Use:\n"
            "  /chave retirar <b>COD</b>\n"
            "  /chave devolver <b>COD</b>\n"
            "  /chave local <b>COD localização</b>\n"
            "  /chave registrar <b>COD</b>",
            parse_mode="HTML"
        )
