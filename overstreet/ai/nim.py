"""NVIDIA NIM: Ana — assistente inteligente com memória, aprendizado e tool-use."""
import json
import re
import asyncio
import logging
from openai import OpenAI
from overstreet.config import NVIDIA_API_KEY, NVIDIA_BASE_URL, NVIDIA_MODEL, NVIDIA_FAST_MODEL
from overstreet.db.imoveis import _query_dict, _query_dicts

log = logging.getLogger("overstreet.ai.nim")

_nvidia: OpenAI | None = None

# ── Parte estática do prompt ───────────────────────────────────────────────

_KNOWLEDGE = """
TIPOS DE IMÓVEIS (use estes grupos na busca):
casa=tipos 68,74,85 · sobrado=47,78,73,86 · apartamento=72 · cobertura=83,82
terreno=66,79,67,165,163 · flat=69 · kitnet/studio=84,48,49
ponto comercial/loja=64,65,76,80 · sala comercial=36,35,37,38
galpão=57 · chácara=70 · fazenda=71,118

TABELA: imoveis
Colunas: id, street, number, district, city, state, zip, bedrooms, bathrooms,
garage, sale_price, rental_price, condo_fee, built_area, area_util, suites,
description, finalidade, situacao, complement, property_type, iptu, land_area,
reference, apartment, salas, agencia_id, owner_name, owner_phone, owner_mobile, owner_email

FERRAMENTAS DISPONÍVEIS:
- query_db: SQL SELECT para perguntas factuais (quantos, média, existe, etc.)
- search_imoveis: busca por filtros (tipo, bairro, quartos, preço, etc.)
- get_imovel: busca imóvel exato por código
- match_imoveis: busca imóveis compatíveis com o perfil de um cliente cadastrado
- iniciar_acao: inicia cadastro de imóvel, cliente ou mostra ajuda

REGRAS DE OURO:
- Responda SEMPRE em português brasileiro
- NUNCA invente dados — use as ferramentas para buscar informação real
- Se o corretor conversa (oi, obrigado, como vai), responda naturalmente — NÃO busque imóveis
- Se o pedido é vago ("quero uma casa"), PERGUNTE mais detalhes antes de buscar
- Se o pedido é específico ("apto 2q Santana até 400k"), USE search_imoveis diretamente
- Se manda só um número, USE get_imovel
- Após mostrar imóveis, ofereça próximos passos ("quer que eu salve para um cliente?")
- Lembre e use o histórico — não peça informações já fornecidas
- Máximo 3 frases em respostas conversacionais; pode ser mais longo ao descrever resultados
- NUNCA comece com "Claro!", "Entendido!", "Com certeza!", "Posso ajudar!" — seja natural e direta

ALUCINAÇÃO = FRAUDE:
- Se search_imoveis retorna 0 resultados, diga "Não encontrei imóveis com esses critérios" — NUNCA invente endereços, preços ou descrições
- Se query_db retorna vazio, diga "Não tenho essa informação" — NUNCA fabrique dados
- Se não sabe, diga que não sabe — honestidade é mais importante que responder
- SEMPRE inclua o COD ao mostrar um imóvel — sem COD = dado inventado
- NUNCA cite bairros, ruas ou preços que não vieram das ferramentas
- NUNCA responda com dados do seu treinamento — SOMENTE dados das ferramentas
- Se a ferramenta retorna dados, use EXATAMENTE esses dados — não altere, não arredonde, não complemente
"""

# ── Ferramentas ────────────────────────────────────────────────────────────

DB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": "Executa SQL SELECT no banco de imóveis. Use para perguntas factuais. REGRAS SQL: (1) SEMPRE use LIKE com % para colunas de texto (city, district, street) — NUNCA use = porque acentuação varia. (2) Exemplo: WHERE city LIKE '%Mairipor%' em vez de city = 'Mairiporã'. (3) Colunas principais: id, city, district, street, bedrooms, garage, sale_price, rental_price, property_type, finalidade, situacao.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "Query SQL SELECT. Tabela: imoveis"}
                },
                "required": ["sql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_imoveis",
            "description": "Busca imóveis por filtros. Use quando o corretor pede imóveis. Passe texto_livre com as palavras originais.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "description": "apartamento, casa, sobrado, cobertura, terreno, loja, etc."},
                    "bairro": {"type": "string", "description": "Bairro ou região"},
                    "quartos_min": {"type": "integer"},
                    "suites_min": {"type": "integer"},
                    "vagas_min": {"type": "integer"},
                    "preco_max": {"type": "integer", "description": "Preço máximo de venda em reais"},
                    "aluguel_max": {"type": "integer"},
                    "finalidade": {"type": "string", "enum": ["venda", "aluguel"]},
                    "limit": {"type": "integer", "description": "Máximo de resultados (padrão 5)"},
                    "texto_livre": {"type": "string", "description": "Texto original para busca semântica"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_imovel",
            "description": "Busca imóvel exato por código/ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "codigo": {"type": "integer", "description": "Código do imóvel"}
                },
                "required": ["codigo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "iniciar_acao",
            "description": (
                "Inicia um fluxo de cadastro ou ação especial. Use quando o corretor "
                "quer cadastrar imóvel, cadastrar cliente, ou pede ajuda detalhada."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "acao": {
                        "type": "string",
                        "enum": ["cadastrar_imovel", "cadastrar_cliente", "mostrar_ajuda"],
                        "description": "Ação a iniciar"
                    },
                    "mensagem": {
                        "type": "string",
                        "description": "Mensagem de confirmação para o corretor"
                    }
                },
                "required": ["acao", "mensagem"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "match_imoveis",
            "description": (
                "Busca imóveis compatíveis com o perfil de um cliente cadastrado. "
                "Use quando o corretor pergunta se tem algo para um cliente específico, "
                "ou quer sugestões de imóveis para um cliente. O sistema analisa o perfil "
                "do cliente e retorna imóveis com score de relevância."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nome_cliente": {
                        "type": "string",
                        "description": "Nome ou apelido do cliente"
                    }
                },
                "required": ["nome_cliente"]
            }
        }
    },
]


# ── Prompt builder ─────────────────────────────────────────────────────────

def build_system_prompt(
    tenant: dict | None = None,
    extra: str = "",
    profile: dict | None = None,
    training_examples: list[dict] | None = None,
) -> str:
    from overstreet.db.memory import format_preferences, get_shared_insights_for_prompt, get_shared_knowledge_for_prompt

    nome_imob = tenant["nome"] if tenant else "OverStreet"
    agente = tenant.get("agente_nome", "Ana") if tenant else "Ana"

    # ── Tier 1: Shared memory (global self-improvement) ──
    shared_insights = get_shared_insights_for_prompt(limit=5)
    shared_knowledge = get_shared_knowledge_for_prompt(limit=5)
    shared_section = ""
    if shared_insights or shared_knowledge:
        shared_section = "\n\n" + shared_insights
        if shared_knowledge:
            shared_section += "\n\n" + shared_knowledge

    # ── Tier 2: Private memory (per-tenant broker) ──
    broker_lines: list[str] = []
    if profile:
        nome_corretor = profile.get("nome", "")
        if nome_corretor:
            broker_lines.append(f"Nome do corretor: {nome_corretor}")
        pref_summary = format_preferences(profile)
        if pref_summary:
            broker_lines.append(f"Padrões de busca aprendidos: {pref_summary}")
        ctx = (profile.get("contexto_resumo") or "").strip()
        if ctx:
            broker_lines.append(f"Contexto recente: {ctx}")
        total = profile.get("total_mensagens", 0)
        if total == 0:
            broker_lines.append("(primeira conversa com este corretor)")

    broker_section = ""
    if broker_lines:
        broker_section = "\n\nCORRETOR ATUAL:\n" + "\n".join(broker_lines)

    # Training examples section (tenant-specific)
    examples_section = ""
    if training_examples:
        examples_section = "\n\nEXEMPLOS DE COMO RESPONDER (aprendidos com este tenant):\n"
        for ex in training_examples[:6]:
            examples_section += (
                f"P: {ex['pergunta']}\nR: {ex['resposta']}\n\n"
            )

    # Extra instructions
    extra_section = ""
    if extra and extra.strip():
        extra_section = f"\n\nINSTRUÇÕES ESPECÍFICAS:\n{extra.strip()}"

    return (
        f"Você é {agente}, assistente pessoal de corretores de imóveis da {nome_imob}.\n\n"
        f"{shared_section}"
        "QUEM VOCÊ É:\n"
        "Você é gentil, prestativa e amiga — como uma boa colega de trabalho que sempre quer ajudar. "
        "Você fala com carinho e atenção, mas sem ser fofoquinha ou artificial. "
        "Quando o corretor manda áudio, você responde como se tivesse ouvido ao vivo — "
        "nunca mencione que \"transcreveu\" ou \"ouvi\", apenas responda naturalmente. "
        "Você não é um chatbot genérico. Você é uma colega de trabalho inteligente e "
        "conversacional. Você aprende com cada interação, lembra do histórico, e "
        "personaliza cada resposta para o corretor. Você usa o nome do corretor "
        "naturalmente quando sabe. Você faz perguntas quando precisa de contexto. "
        "Você é proativa: sugere próximos passos, lembra de clientes mencionados, "
        "compara com buscas anteriores."
        f"{broker_section}"
        f"{examples_section}"
        f"\n\n{_KNOWLEDGE}"
        f"{extra_section}"
    )


# ── NIM client ─────────────────────────────────────────────────────────────

def get_nvidia() -> OpenAI:
    global _nvidia
    if _nvidia is None:
        _nvidia = OpenAI(base_url=NVIDIA_BASE_URL, api_key=NVIDIA_API_KEY)
    return _nvidia


# ── ask_ana ────────────────────────────────────────────────────────────────

async def ask_ana(
    conn,
    qdrant,
    embedder,
    text: str,
    user_id: int = 0,
    tenant: dict | None = None,
    user_name: str = "",
    tenant_conn=None,
) -> dict:
    """Ana com tool-use, memória e aprendizado. Retorna {text, imoveis, codigo_exact, action}."""
    from overstreet.db.memory import (
        load_history, save_message,
        get_or_create_profile, merge_search_preferences,
    )
    from overstreet.ai.tools import exec_query, exec_search_imoveis, exec_get_imovel

    # Single-tenant: sem tenant_id
    # Load or create user profile
    profile = None
    if user_id:
        profile = get_or_create_profile(user_id, nome=user_name)

    # Training examples removidas (single-tenant, não tem tenant)
    training_examples: list[dict] = []

    # Sem bot_config em single-tenant. Defaults fixos.
    max_results = 5
    extra_prompt = ""

    # History (last 40 messages)
    history = load_history(user_id, limit=40) if user_id else []
    if user_id:
        save_message(user_id, "user", text)

    system_prompt = build_system_prompt(
        tenant=tenant,
        extra=extra_prompt,
        profile=profile,
        training_examples=training_examples or None,
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-40:])
    messages.append({"role": "user", "content": text})

    result: dict = {"text": "", "imoveis": [], "codigo_exact": None, "action": None}
    nvidia = get_nvidia()

    # Two-model strategy:
    # - Phase 1 (tool calls): fast model (mistral-small) — decides which tools to call
    # - Phase 2 (final response): powerful model (qwen3.5) — generates rich answer
    from overstreet.config import NVIDIA_FAST_MODEL
    tool_model = NVIDIA_FAST_MODEL
    response_model = NVIDIA_MODEL

    try:
            for _ in range(3):
                resp = await asyncio.to_thread(
                    nvidia.chat.completions.create,
                    model=tool_model,
                    messages=messages,
                    tools=DB_TOOLS,
                    tool_choice="auto",
            temperature=0,
            max_tokens=400,
            timeout=15,
                )
                choice = resp.choices[0]

                if not choice.message.tool_calls:
                    raw_text = (choice.message.content or "").strip()
                    if result["imoveis"] and raw_text and len(raw_text) < 50 and tool_model != response_model:
                        try:
                            enhanced_resp = await asyncio.to_thread(
                                nvidia.chat.completions.create,
                    model=response_model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=800,
                    timeout=20,
                            )
                            raw_text = (enhanced_resp.choices[0].message.content or "").strip()
                        except Exception as e2:
                            log.warning("Enhanced response failed, using fast model text: %s", e2)
                    result["text"] = raw_text
                    break

                messages.append(choice.message)
                for tool_call in choice.message.tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except Exception:
                        args = {}

                    if fn_name == "query_db":
                        tool_result = await asyncio.to_thread(
                            exec_query, conn, args.get("sql", ""), tenant_conn
                        )

                    if fn_name == "search_imoveis":
                        log.info("TOOL search_imoveis args: %s", args)
                        if user_id:
                            merge_search_preferences(user_id, args)

                        limit = min(int(args.get("limit", max_results)), max_results)
                        # Single-tenant: collection_name fixo
                        collection_name = "imoveis"
                        tool_result = await asyncio.to_thread(
                            exec_search_imoveis, conn, qdrant, embedder,
                            args.get("tipo"), args.get("bairro"),
                            args.get("quartos_min"), args.get("suites_min"),
                            args.get("vagas_min"), args.get("preco_max"),
                            args.get("aluguel_max"), args.get("finalidade"),
                            limit, args.get("texto_livre"), tenant_conn,
                            collection_name,
                        )
                        db = tenant_conn or conn
                        codigos = re.findall(r'COD (\d+)', tool_result)
                        log.info("TOOL search_imoveis result (%d chars, %d CODs): %s", len(tool_result), len(codigos), tool_result[:200])
                        for cod in codigos[:max_results]:
                            row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (int(cod),))
                            if row:
                                result["imoveis"].append(row)

                    elif fn_name == "get_imovel":
                        codigo = args.get("codigo", 0)
                        tool_result = await asyncio.to_thread(
                            exec_get_imovel, conn, codigo, tenant_conn
                        )
                        if "não encontrado" not in tool_result.lower():
                            db = tenant_conn or conn
                            row = _query_dict(db, "SELECT * FROM imoveis WHERE id=?", (codigo,))
                            if row:
                                result["imoveis"].append(row)
                            result["codigo_exact"] = codigo

                    elif fn_name == "iniciar_acao":
                        acao = args.get("acao", "")
                        mensagem = args.get("mensagem", "")
                        result["action"] = acao
                        result["text"] = mensagem
                        if user_id and mensagem:
                            save_message(user_id, "assistant", mensagem)
                        return result

                    elif fn_name == "match_imoveis":
                        from overstreet.ai.tools import exec_match_imoveis
                        nome_cliente = args.get("nome_cliente", "")
                        tool_result = await asyncio.to_thread(
                            exec_match_imoveis, conn, nome_cliente, tenant_conn
                        )
                        db_tool = tenant_conn or conn
                        codigos = re.findall(r'COD (\d+)', tool_result)
                        for cod in codigos[:max_results]:
                            row = _query_dict(db_tool, "SELECT * FROM imoveis WHERE id=?", (int(cod),))
                            if row:
                                result["imoveis"].append(row)

                    else:
                        tool_result = f"Ferramenta {fn_name} desconhecida"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    })

    except Exception as e:
            log.error("Erro NIM: %s", e)
            result["text"] = "Desculpe, ocorreu um erro. Tente novamente."

    if user_id and result["text"]:
        save_message(user_id, "assistant", result["text"])

    # Periodic context summarization: every 30 messages, update context summary
    if user_id and profile and profile.get("total_mensagens", 0) % 30 == 0:
        asyncio.create_task(_summarize_context(user_id, history[-20:]))

    return result


async def _summarize_context(user_id: int, recent_history: list[dict]):
    """Async background task: summarize recent conversation into user profile."""
    if not recent_history:
        return
    try:
        nvidia = get_nvidia()
        convo = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in recent_history[-20:]
        )
        prompt = (
            "Resuma em 2-3 frases o que este corretor está buscando, seus clientes "
            "e contexto relevante. Seja conciso. Conversa:\n\n" + convo
        )
        resp = await asyncio.to_thread(
            nvidia.chat.completions.create,
            model=NVIDIA_FAST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        summary = (resp.choices[0].message.content or "").strip()
        if summary:
            from overstreet.db.memory import update_profile
            update_profile(user_id, contexto_resumo=summary)
            log.info("Contexto atualizado para user_id=%d", user_id)
    except Exception as e:
        log.warning("Erro ao resumir contexto: %s", e)


async def nim_extract_json(prompt: str) -> dict:
    """Chama NIM para extrair JSON estruturado. Retorna dict ou {} em caso de falha."""
    from overstreet.config import NVIDIA_MODEL
    nvidia = get_nvidia()

    for model in [NVIDIA_FAST_MODEL, NVIDIA_MODEL]:
        try:
            resp = await asyncio.to_thread(
                nvidia.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
            )
            text = resp.choices[0].message.content.strip()
            log.debug("nim_extract_json [%s]: %s", model, text[:200])
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                data = json.loads(m.group())
                # Skip empty dicts — model couldn't extract anything
                if any(v for k, v in data.items() if k not in ("state", "suites", "bathrooms", "garage") and v):
                    return data
            else:
                log.warning("nim_extract_json [%s]: no JSON in response", model)
        except Exception as e:
            log.error("nim_extract_json erro [%s]: %s", model, e)
    log.warning("nim_extract_json: all models failed for prompt: %s", prompt[:100])
    return {}
