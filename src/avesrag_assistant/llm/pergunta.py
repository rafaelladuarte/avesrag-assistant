"""Redator da pergunta (`c02_pergunta`, plano_assistant2.md §4.2).

O código escolhe o que perguntar (`conversa.perguntas`), e o LLM só redige. A
entrada são o pedido (do `conversa.yaml`) e os atributos já validados: o texto
da pessoa não entra. Uma pergunta sem "?" é recusada e pedida de novo; se o LLM
falhar, quem chama usa a pergunta de reserva (`reserva`).
"""

from __future__ import annotations

import json

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from avesrag_builder.llm.base import Pedido

from avesrag_assistant.busca.modelos import AtributosObservados
from avesrag_assistant.llm.cache import ARQUIVO_PADRAO, CacheLLM
from avesrag_assistant.llm.contratos import C02Pergunta, schema_do_contrato
from avesrag_assistant.llm.prompts import PromptChat, carregar_prompt
from avesrag_assistant.settings import Settings


def reserva(pedido: str) -> str:
    """A pergunta fixa, para quando o LLM falhar."""
    return f"Você lembra {pedido}?"


def _validar(conteudo: str) -> str:
    pergunta = C02Pergunta.model_validate_json(conteudo).pergunta.strip()
    if not pergunta.endswith("?"):
        raise ValueError(f"a pergunta não termina com '?': {pergunta!r}")
    return pergunta


class RedatorPergunta:
    def __init__(
        self,
        prompt: PromptChat,
        cache: CacheLLM,
        vocabulario: Vocabulario,
        regras_bico: RegrasBico,
        modelo_padrao: str,
    ):
        if prompt.schema_nome != "C02Pergunta":
            raise ValueError(f"o redator usa o schema C02Pergunta, não {prompt.schema_nome}")
        self.prompt = prompt
        self.cache = cache
        self.modelo = prompt.modelo or modelo_padrao
        self._system = prompt.render_system(vocabulario, regras_bico)
        self._schema = schema_do_contrato("C02Pergunta", vocabulario, regras_bico)

    def redigir(self, pedido: str, atributos: AtributosObservados) -> str:
        estado = json.dumps(atributos.model_dump(exclude_none=True), ensure_ascii=False)
        pedido_llm = Pedido(
            id=f"chat:{self.prompt.id}",
            modelo=self.modelo,
            system=self._system,
            entrada=self.prompt.render_entrada(pedido=pedido, estado=estado),
            schema=self._schema,
            temperatura=self.prompt.temperatura,
        )
        return self.cache.gerar(self.prompt.id, self.prompt.versao, pedido_llm, _validar)


def criar_redator(
    settings: Settings, vocabulario: Vocabulario, regras_bico: RegrasBico
) -> RedatorPergunta:
    """Redator com o prompt do `config/prompts/`, o fornecedor da OpenAI e o cache em `reports/`."""
    from avesrag_builder.llm.openai import OpenAIFornecedor

    prompt = carregar_prompt(settings.CONFIG_DIR / "prompts" / "c02_pergunta.yaml", vocabulario)
    settings.require("OPENAI_API_KEY")
    fornecedor = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    cache = CacheLLM(settings.REPORTS_DIR / ARQUIVO_PADRAO, fornecedor)
    return RedatorPergunta(prompt, cache, vocabulario, regras_bico, settings.LLM_MODEL)
