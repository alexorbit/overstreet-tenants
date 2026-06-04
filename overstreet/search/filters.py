"""Extração de filtros de busca a partir de linguagem natural."""
import re


def extract_filters(text: str) -> dict:
    """Extrai filtros estruturados de uma query em português."""
    filters = {}
    text_lower = text.lower()

    # Tipo de imóvel (verificar multi-palavra primeiro)
    tipos = [
        "ponto comercial", "sala comercial", "casa condominio",
        "sobrado condominio", "salao comercial", "salão comercial",
        "apartamento", "apto", "flat", "casa", "sobrado", "cobertura",
        "terreno", "loja", "galpao", "galpão", "kitnet", "studio",
        "kitchenette", "salao", "salão", "chacara", "chácara", "fazenda",
    ]
    for t in tipos:
        if t in text_lower:
            filters["tipo"] = t
            break

    # Térrea → casa
    if re.search(r'terr[êe]a', text_lower) and "tipo" not in filters:
        filters["tipo"] = "casa"

    # Quartos/dormitórios
    m = re.search(r'(\d+)\s*(?:dorm|quarto|suite|suíte|suites)', text_lower)
    if m:
        filters["quartos_min"] = int(m.group(1))

    # Suítes
    m = re.search(r'(\d+)\s*suite', text_lower)
    if m:
        filters["suites_min"] = int(m.group(1))

    # Vagas
    m = re.search(r'(\d+)\s*vaga', text_lower)
    if m:
        filters["vagas_min"] = int(m.group(1))

    # Bairro via regex
    m = re.search(
        r'(?:bairro\s+|em\s+|no\s+|na\s+|regiao\s+|região\s+)([\w\s]+?)(?:\s+com|\s+de|\s+para|$)',
        text_lower
    )
    if m:
        bairro = m.group(1).strip()
        for stop in ["um", "uma", "o", "a", "com", "de", "para", "no", "na"]:
            bairro = bairro.replace(f" {stop} ", " ").strip()
        if len(bairro) > 2:
            filters["bairro"] = bairro

    # Bairros conhecidos (fallback)
    if "bairro" not in filters:
        bairros_conhecidos = [
            "santana", "tucuruvi", "casa verde", "limao", "limão",
            "mandaqui", "parada inglesa", "vila guilherme", "imirim",
            "agua fria", "tremembe", "lauzane", "freguesia",
            "jardim sao paulo", "serra da cantareira", "horto florestal",
            "mooca", "pinheiros", "vila mariana", "consolacao",
            "liberdade", "se", "centro", "butanta", "lapa",
            "santa terezinha", "jabaquara", "santo amaro",
        ]
        for b in bairros_conhecidos:
            if b in text_lower:
                filters["bairro"] = b
                break

    # Finalidade
    if "aluguel" in text_lower or "alugar" in text_lower:
        filters["finalidade"] = "aluguel"
    elif "venda" in text_lower or "comprar" in text_lower:
        filters["finalidade"] = "venda"

    return filters
