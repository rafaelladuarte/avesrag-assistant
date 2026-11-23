"""Contratos do retriever: a entrada (`Consulta`, `AtributosObservados`) e a saída.

`AtributosObservados` é o mesmo objeto que o `c01_extrator` devolve (Assistant 2)
e que o `avaliacao/casos.yaml` usa como gabarito. Os valores são conferidos
contra o `vocabulario.yaml` e o `regras_bico.yaml` do builder, que entram pelo
`context` do Pydantic: use `validar_atributos()` ou `validar_consulta()`. Sem o
contexto a validação falha, para nenhum caminho aceitar valores sem conferir.

Só valores canônicos passam: um sinônimo ("amarela") é recusado, porque quem
normaliza é o extrator ou quem anota o caso. Lista vazia vira `None` (o LLM
manda `[]`), e valores repetidos são removidos.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from avesrag_builder.core.models import Dominio, SpeciesId
from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

CHAVE_VOCABULARIO = "vocabulario"
CHAVE_REGRAS_BICO = "regras_bico"

# Atributos de lista: o nome é o mesmo do vocabulário.
ATRIBUTOS_LISTA = (
    "tamanho",
    "cores",
    "marcas",
    "cauda",
    "ambiente",
    "onde_estava",
    "o_que_fazia",
    "grupo",
    "atividade",
)
# Atributos do vocabulário que a pessoa não informa: o bico vem em dimensões
# visuais, e a IUCN não é observável.
ATRIBUTOS_NAO_OBSERVADOS = ("bico_tipo", "iucn")
DIMENSOES_BICO = ("comprimento", "espessura", "curvatura")

Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Resultado = Literal["bate", "conflita", "sem_dado"]


def contexto(vocabulario: Vocabulario, regras_bico: RegrasBico) -> dict[str, Any]:
    return {CHAVE_VOCABULARIO: vocabulario, CHAVE_REGRAS_BICO: regras_bico}


def _sem_repetidos(valores: list[Any] | None) -> list[Any] | None:
    if not valores:
        return None
    return list(dict.fromkeys(valores))


class Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


class CorPorParte(Modelo):
    model_config = ConfigDict(extra="forbid", frozen=True)  # hashable: dá para tirar repetidos

    parte: Texto
    cor: Texto


class Bico(Modelo):
    """Dimensões visuais do bico (`dimensoes` do `regras_bico.yaml`)."""

    comprimento: Texto | None = None
    espessura: Texto | None = None
    curvatura: Texto | None = None
    extra: list[Texto] | None = None

    @field_validator("extra")
    @classmethod
    def _extra(cls, v: list[str] | None) -> list[str] | None:
        return _sem_repetidos(v)

    def vazio(self) -> bool:
        return all(getattr(self, d) is None for d in (*DIMENSOES_BICO, "extra"))


class AtributosObservados(Modelo):
    """O que a pessoa viu, no vocabulário da canônica."""

    tamanho: list[Texto] | None = None
    cores: list[Texto] | None = None
    cores_por_parte: list[CorPorParte] | None = None
    marcas: list[Texto] | None = None
    bico: Bico | None = None
    cauda: list[Texto] | None = None
    ambiente: list[Texto] | None = None
    onde_estava: list[Texto] | None = None
    o_que_fazia: list[Texto] | None = None
    grupo: list[Texto] | None = None
    atividade: list[Texto] | None = None

    @field_validator(*ATRIBUTOS_LISTA, "cores_por_parte")
    @classmethod
    def _listas(cls, v: list[Any] | None) -> list[Any] | None:
        return _sem_repetidos(v)

    @field_validator("bico")
    @classmethod
    def _bico_vazio(cls, v: Bico | None) -> Bico | None:
        return None if v is None or v.vazio() else v

    @model_validator(mode="after")
    def _vocabulario(self, info: ValidationInfo) -> Self:
        ctx = info.context or {}
        vocab: Vocabulario | None = ctx.get(CHAVE_VOCABULARIO)
        regras: RegrasBico | None = ctx.get(CHAVE_REGRAS_BICO)
        if vocab is None or regras is None:
            raise ValueError(
                "AtributosObservados precisa do vocabulário e das regras de bico: "
                "use validar_atributos() ou model_validate(..., context=contexto(...))"
            )
        erros = []
        for nome in ATRIBUTOS_LISTA:
            for i, valor in enumerate(getattr(self, nome) or []):
                if not vocab.contem(nome, valor):
                    erros.append(f"{nome}[{i}]: {valor!r} não está no vocabulário")
        partes = set(vocab.partes())
        atributo_cor = vocab.arquivo.cores_por_parte.cor
        for i, item in enumerate(self.cores_por_parte or []):
            if item.parte not in partes:
                erros.append(f"cores_por_parte[{i}].parte: {item.parte!r} não está no vocabulário")
            if not vocab.contem(atributo_cor, item.cor):
                erros.append(f"cores_por_parte[{i}].cor: {item.cor!r} não está no vocabulário")
        if self.bico is not None:
            dimensoes = regras.arquivo.dimensoes
            for dim in DIMENSOES_BICO:
                valor = getattr(self.bico, dim)
                if valor is not None and valor not in dimensoes[dim]:
                    erros.append(f"bico.{dim}: {valor!r} não está em {dimensoes[dim]}")
            for i, valor in enumerate(self.bico.extra or []):
                if valor not in dimensoes["extra"]:
                    erros.append(f"bico.extra[{i}]: {valor!r} não está em {dimensoes['extra']}")
        if erros:
            raise ValueError("; ".join(erros))
        return self

    def informados(self) -> list[str]:
        """Nomes dos atributos preenchidos, na ordem dos campos."""
        return [nome for nome in type(self).model_fields if getattr(self, nome) is not None]


def _atributos_vazios() -> AtributosObservados:
    return AtributosObservados.model_construct()


class Consulta(Modelo):
    descricao: Texto  # vira o embedding (no Assistant 2, a `descricao_para_busca` do c01)
    atributos: AtributosObservados = Field(default_factory=_atributos_vazios)
    recusadas: list[SpeciesId] = Field(default_factory=list)  # já recusados na conversa


def validar_atributos(
    dados: dict[str, Any], vocabulario: Vocabulario, regras_bico: RegrasBico
) -> AtributosObservados:
    return AtributosObservados.model_validate(dados, context=contexto(vocabulario, regras_bico))


def validar_consulta(
    dados: dict[str, Any], vocabulario: Vocabulario, regras_bico: RegrasBico
) -> Consulta:
    return Consulta.model_validate(dados, context=contexto(vocabulario, regras_bico))


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------


class DetalheAtributo(Modelo):
    """Como um atributo informado se compara com a ficha de uma espécie."""

    atributo: str
    resultado: Resultado
    pontos: float
    bateram: list[str] = Field(
        default_factory=list
    )  # rótulos legíveis: "amarelo", "barriga:amarelo"
    conflitaram: list[str] = Field(default_factory=list)


class Candidata(Modelo):
    species_id: SpeciesId
    nome_cientifico: Texto
    nome: Texto
    nota_vetor: float
    nota_vetor_normalizada: float
    dominio: Dominio  # domínio do documento com a melhor nota
    origem: str = "vetor"  # a busca que trouxe a espécie: vetor, um nível do híbrido ou texto
    pontos: float
    nota_final: float
    detalhes: list[DetalheAtributo] = Field(default_factory=list)
