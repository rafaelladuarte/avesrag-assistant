"""Contratos de saída dos prompts do chat, gerados do vocabulário e das regras de bico.

Os valores aceitos viram `Literal`, então o JSON Schema estrito enviado ao LLM
já os traz como `enum` e a validação Pydantic confere de novo (Modelo de Dados >
Contratos do chat). Depois da validação, os atributos passam ao
`AtributosObservados` do retriever (`validar_atributos`), o mesmo objeto do
gabarito do `casos.yaml`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from avesrag_builder.extract.contratos import schema_estrito
from pydantic import BaseModel, ConfigDict, Field, create_model

from avesrag_assistant.busca.modelos import ATRIBUTOS_LISTA, DIMENSOES_BICO

# Tamanho máximo da descrição reescrita (o prompt pede bem menos).
LIMITE_DESCRICAO = 800


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Local(_Base):
    texto: str = Field(min_length=1)


def _literal(valores: list[str]) -> Any:
    return Literal[tuple(valores)]  # type: ignore[valid-type]


def _modelo_atributos(voc: Vocabulario, regras: RegrasBico) -> type[BaseModel]:
    dimensoes = regras.arquivo.dimensoes
    bico = create_model(
        "BicoC01",
        __base__=_Base,
        **{d: (_literal(dimensoes[d]) | None, ...) for d in DIMENSOES_BICO},
        extra=(list[_literal(dimensoes["extra"])] | None, ...),
    )
    cor_por_parte = create_model(
        "CorPorParteC01",
        __base__=_Base,
        parte=(_literal(voc.partes()), ...),
        cor=(_literal(voc.valores(voc.arquivo.cores_por_parte.cor)), ...),
    )
    campos: dict[str, Any] = {
        nome: (list[_literal(voc.valores(nome))] | None, ...) for nome in ATRIBUTOS_LISTA
    }
    campos["cores_por_parte"] = (list[cor_por_parte] | None, ...)
    campos["bico"] = (bico | None, ...)
    return create_model("AtributosC01", __base__=_Base, **campos)


def contrato_c01(voc: Vocabulario, regras: RegrasBico) -> type[BaseModel]:
    """O modelo da saída do `c01_extrator` para este vocabulário."""
    return create_model(
        "C01Extrator",
        __base__=_Base,
        fora_de_escopo=(bool, ...),
        local=(Local | None, ...),
        data_observacao=(str | None, ...),  # AAAA-MM-DD, quando a pessoa disse
        atributos=(_modelo_atributos(voc, regras), ...),
        descricao_para_busca=(str, Field(min_length=1, max_length=LIMITE_DESCRICAO)),
    )


class C02Pergunta(_Base):
    pergunta: str = Field(min_length=1, max_length=300)


class EspecieSugerida(_Base):
    nome_popular: str = Field(min_length=1)
    nome_cientifico: str = Field(min_length=1)


class C04Candidatas(_Base):
    """Espécies que o LLM sugere para a descrição, da mais provável para a menos."""

    especies: list[EspecieSugerida] = Field(max_length=10)


@lru_cache
def _schemas(voc: Vocabulario, regras: RegrasBico) -> dict[str, type[BaseModel]]:
    return {
        "C01Extrator": contrato_c01(voc, regras),
        "C02Pergunta": C02Pergunta,
        "C04Candidatas": C04Candidatas,
    }


def contrato(nome: str, voc: Vocabulario, regras: RegrasBico) -> type[BaseModel]:
    return _schemas(voc, regras)[nome]


def schema_do_contrato(nome: str, voc: Vocabulario, regras: RegrasBico) -> dict[str, Any]:
    """`{"name", "strict", "schema"}` no formato `json_schema` da OpenAI."""
    return schema_estrito(contrato(nome, voc, regras))


# Nome do schema → placeholders que a `entrada` do prompt tem de ter.
ENTRADAS = {
    "C01Extrator": {"estado", "mensagem"},
    "C02Pergunta": {"pedido", "estado"},
    "C04Candidatas": {"mensagem"},
}
