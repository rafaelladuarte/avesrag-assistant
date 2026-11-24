"""Extrator de atributos (`c01_extrator`, plano_assistant2.md §3).

Recebe o estado atual (os atributos já extraídos e a descrição de até ali) e a
mensagem nova, e devolve o estado inteiro atualizado: atributos no vocabulário
(`AtributosObservados`), local, data e a `descricao_para_busca`, que é o que a
pessoa disse reescrito no estilo do texto de aparência das fichas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from avesrag_builder.llm.base import Pedido

from avesrag_assistant.busca.modelos import AtributosObservados, validar_atributos
from avesrag_assistant.llm.cache import ARQUIVO_PADRAO, CacheLLM
from avesrag_assistant.llm.contratos import contrato, schema_do_contrato
from avesrag_assistant.llm.prompts import PromptChat, carregar_prompt
from avesrag_assistant.settings import Settings


@dataclass(frozen=True)
class Extracao:
    fora_de_escopo: bool
    local: str | None
    data_observacao: str | None
    atributos: AtributosObservados
    descricao_para_busca: str


def estado_para_prompt(atributos: AtributosObservados | None, descricao: str | None) -> str:
    """O estado que o extrator recebe: só o que já foi extraído, em JSON."""
    dados = atributos.model_dump(exclude_none=True) if atributos is not None else {}
    return json.dumps(
        {"atributos": dados, "descricao_para_busca": descricao},
        ensure_ascii=False,
    )


class Extrator:
    def __init__(
        self,
        prompt: PromptChat,
        cache: CacheLLM,
        vocabulario: Vocabulario,
        regras_bico: RegrasBico,
        modelo_padrao: str,
    ):
        if prompt.schema_nome != "C01Extrator":
            raise ValueError(f"o extrator usa o schema C01Extrator, não {prompt.schema_nome}")
        self.prompt = prompt
        self.cache = cache
        self.vocabulario = vocabulario
        self.regras_bico = regras_bico
        self.modelo = prompt.modelo or modelo_padrao
        self._system = prompt.render_system(vocabulario, regras_bico)
        self._schema = schema_do_contrato("C01Extrator", vocabulario, regras_bico)
        self._contrato = contrato("C01Extrator", vocabulario, regras_bico)

    def _validar(self, conteudo: str) -> Extracao:
        saida = self._contrato.model_validate_json(conteudo)
        atributos = validar_atributos(
            saida.atributos.model_dump(), self.vocabulario, self.regras_bico
        )
        return Extracao(
            fora_de_escopo=saida.fora_de_escopo,
            local=saida.local.texto if saida.local else None,
            data_observacao=saida.data_observacao,
            atributos=atributos,
            descricao_para_busca=saida.descricao_para_busca.strip(),
        )

    def extrair(
        self,
        mensagem: str,
        atributos: AtributosObservados | None = None,
        descricao: str | None = None,
        pergunta: str | None = None,
    ) -> Extracao:
        """`pergunta`: o texto da última pergunta feita à pessoa, se a mensagem responde a ela."""
        entrada = self.prompt.render_entrada(
            estado=estado_para_prompt(atributos, descricao),
            pergunta=(pergunta or "nenhuma").strip(),
            mensagem=mensagem.strip(),
        )
        pedido = Pedido(
            id=f"chat:{self.prompt.id}",
            modelo=self.modelo,
            system=self._system,
            entrada=entrada,
            schema=self._schema,
            temperatura=self.prompt.temperatura,
        )
        return self.cache.gerar(self.prompt.id, self.prompt.versao, pedido, self._validar)


def criar_extrator(
    settings: Settings, vocabulario: Vocabulario, regras_bico: RegrasBico
) -> Extrator:
    """Extrator com o prompt do `config/prompts/`, o fornecedor da OpenAI e o cache em `reports/`."""
    from avesrag_builder.llm.openai import OpenAIFornecedor

    prompt = carregar_prompt(settings.CONFIG_DIR / "prompts" / "c01_extrator.yaml", vocabulario)
    settings.require("OPENAI_API_KEY")
    fornecedor = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    cache = CacheLLM(settings.REPORTS_DIR / ARQUIVO_PADRAO, fornecedor)
    return Extrator(prompt, cache, vocabulario, regras_bico, settings.LLM_MODEL)
