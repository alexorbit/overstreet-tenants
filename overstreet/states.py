"""FSM States do OverStreet-Corretor-Agent."""
from aiogram.fsm.state import State, StatesGroup


class CadastroImovelStates(StatesGroup):
    aguardando_descricao = State()
    revisando_preview = State()
    corrigindo = State()
    aguardando_codigo_foto = State()
    recebendo_fotos = State()


class CadastroClienteStates(StatesGroup):
    aguardando_descricao = State()
    revisando_preview = State()
    adicionando_alias = State()


class AgendamentoVisitaStates(StatesGroup):
    aguardando_imovel = State()
    aguardando_data = State()
    aguardando_cliente = State()
    confirmar = State()


class CadastroTenantStates(StatesGroup):
    aguardando_nome = State()
    aguardando_agente_nome = State()
    aguardando_agente_tel = State()
    aguardando_ids = State()
    confirmando = State()


class FollowupStates(StatesGroup):
    aguardando_descricao = State()
