"""`config/conversa.yaml`: limites e listas da conversa, validados contra o vocabulário."""

from __future__ import annotations

from pathlib import Path
from typing import Self

# Privados no builder: reaproveitados para não copiar o leitor que recusa chave duplicada.
from avesrag_builder.core.vocabulario import (
    Vocabulario,
    VocabularioInvalido,
    _erros_pydantic,
    _ler_yaml,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from avesrag_assistant.busca.modelos import AtributosObservados
from avesrag_assistant.busca.retriever import MODOS, Modo

# Atributos que não viram pergunta: `cores` sozinha não diz onde (a pergunta é por
# parte), e o grupo, a atividade e o que fazia raramente separam as candidatas.
NAO_PERGUNTAVEIS = ("cores", "grupo", "atividade", "o_que_fazia")
CHAVE_VOCABULARIO = "vocabulario"


class ConversaInvalida(ValueError):
    """O `conversa.yaml` não passou na validação."""

    def __init__(self, arquivo: Path | str, erros: list[str]):
        self.arquivo = str(arquivo)
        self.erros = erros
        lista = "\n".join(f"  - {e}" for e in erros)
        super().__init__(f"{self.arquivo}: {len(erros)} erro(s)\n{lista}")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _repetidos(valores: list[str]) -> list[str]:
    return sorted({v for v in valores if valores.count(v) > 1})


class ConfigBusca(_Base):
    modo: Modo

    @field_validator("modo")
    @classmethod
    def _modo(cls, v: str) -> str:
        if v not in MODOS:
            raise ValueError(f"modo desconhecido: {v!r} (use {', '.join(MODOS)})")
        return v


class ConfigPerguntas(_Base):
    maximo: int = Field(ge=0)
    candidatas: PositiveInt
    temperatura: PositiveFloat
    ganho_minimo: float = Field(ge=0, le=1)
    perguntaveis: list[str] = Field(min_length=1)
    partes: list[str] = Field(min_length=1)
    parar_com_peso: float | None = Field(default=None, gt=0, le=1)  # 1ª com mais peso: não pergunta
    pedidos: dict[str, str]  # chave da pergunta → o que perguntar, para o c02
    nomes_partes: dict[str, str]  # parte → "da barriga", para o pedido de cor

    @model_validator(mode="after")
    def _pedidos_cobrem_as_perguntas(self) -> Self:
        esperadas = set()
        for nome in self.perguntaveis:
            if nome == "bico":
                esperadas |= {f"bico:{d}" for d in ("comprimento", "espessura", "curvatura")}
            else:
                esperadas.add(nome)
        erros = []
        if faltando := sorted(esperadas - set(self.pedidos)):
            erros.append(f"pedidos: faltam {faltando}")
        if "{parte}" not in self.pedidos.get("cores_por_parte", "{parte}"):
            erros.append("pedidos.cores_por_parte: falta {parte}")
        if faltando := sorted(set(self.partes) - set(self.nomes_partes)):
            erros.append(f"nomes_partes: faltam {faltando}")
        if erros:
            raise ValueError("; ".join(erros))
        return self

    def pedido(self, chave: str) -> str:
        """O que perguntar para a chave `atributo[:parte]`."""
        atributo, _, parte = chave.partition(":")
        if atributo == "cores_por_parte":
            return self.pedidos[atributo].format(parte=self.nomes_partes[parte])
        return self.pedidos[chave]

    @field_validator("perguntaveis")
    @classmethod
    def _perguntaveis(cls, v: list[str]) -> list[str]:
        erros = []
        if desconhecidos := sorted(set(v) - set(AtributosObservados.model_fields)):
            erros.append(f"atributos desconhecidos: {desconhecidos}")
        if fora := sorted(set(v) & set(NAO_PERGUNTAVEIS)):
            erros.append(f"atributos que não viram pergunta: {fora}")
        if repetidos := _repetidos(v):
            erros.append(f"repetidos: {repetidos}")
        if erros:
            raise ValueError("; ".join(erros))
        return v

    @field_validator("partes")
    @classmethod
    def _partes(cls, v: list[str], info: ValidationInfo) -> list[str]:
        vocab: Vocabulario | None = (info.context or {}).get(CHAVE_VOCABULARIO)
        if vocab is not None and (fora := sorted(set(v) - set(vocab.partes()))):
            raise ValueError(f"partes fora do vocabulário: {fora}")
        if repetidos := _repetidos(v):
            raise ValueError(f"repetidas: {repetidos}")
        return v


class ConfigApresentacao(_Base):
    minimo_visuais: int = Field(ge=0)
    visuais: list[str] = Field(min_length=1)

    @field_validator("visuais")
    @classmethod
    def _visuais(cls, v: list[str]) -> list[str]:
        if desconhecidos := sorted(set(v) - set(AtributosObservados.model_fields)):
            raise ValueError(f"atributos desconhecidos: {desconhecidos}")
        return v


class ConfigConfirmacao(_Base):
    rodadas: PositiveInt


class ConfigSimulacao(_Base):
    um_valor: list[str] = Field(default_factory=list)  # a resposta é um valor sorteado
    vizinhas: list[tuple[str, str]] = Field(default_factory=list)
    conflito: dict[str, float] = Field(default_factory=lambda: {"padrao": 0.0})
    nao_sei: float = Field(default=0.0, ge=0, le=1)

    @field_validator("conflito")
    @classmethod
    def _conflito(cls, v: dict[str, float]) -> dict[str, float]:
        erros = []
        if "padrao" not in v:
            erros.append("falta a taxa `padrao`")
        if desconhecidos := sorted(set(v) - set(AtributosObservados.model_fields) - {"padrao"}):
            erros.append(f"atributos desconhecidos: {desconhecidos}")
        if fora := sorted(k for k, x in v.items() if not 0 <= x <= 1):
            erros.append(f"taxas fora de 0 a 1: {fora}")
        if erros:
            raise ValueError("; ".join(erros))
        return v

    def taxa_conflito(self, atributo: str) -> float:
        return self.conflito.get(atributo, self.conflito["padrao"])

    @field_validator("um_valor")
    @classmethod
    def _um_valor(cls, v: list[str]) -> list[str]:
        if desconhecidos := sorted(set(v) - set(AtributosObservados.model_fields)):
            raise ValueError(f"atributos desconhecidos: {desconhecidos}")
        return v

    @field_validator("vizinhas")
    @classmethod
    def _cores(cls, v: list[tuple[str, str]], info: ValidationInfo) -> list[tuple[str, str]]:
        vocab: Vocabulario | None = (info.context or {}).get(CHAVE_VOCABULARIO)
        if vocab is None:
            return v
        cores = {c for par in v for c in par}
        if fora := sorted(c for c in cores if not vocab.contem("cores", c)):
            raise ValueError(f"cores fora do vocabulário: {fora}")
        return v

    def vizinhas_de(self, cor: str) -> list[str]:
        return sorted(
            {b for a, b in self.vizinhas if a == cor} | {a for a, b in self.vizinhas if b == cor}
        )


class ConfigConversa(_Base):
    versao: str = Field(min_length=1)
    busca: ConfigBusca
    perguntas: ConfigPerguntas
    apresentacao: ConfigApresentacao
    confirmacao: ConfigConfirmacao
    simulacao: ConfigSimulacao = Field(default_factory=ConfigSimulacao)

    @model_validator(mode="after")
    def _vocabulario_presente(self, info: ValidationInfo) -> Self:
        if (info.context or {}).get(CHAVE_VOCABULARIO) is None:
            raise ValueError("conversa.yaml precisa do vocabulário: use carregar_conversa()")
        return self


def carregar_conversa(path: Path | str, vocabulario: Vocabulario) -> ConfigConversa:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise ConversaInvalida(path, e.erros) from e
    try:
        return ConfigConversa.model_validate(dados, context={CHAVE_VOCABULARIO: vocabulario})
    except ValidationError as e:
        raise ConversaInvalida(path, _erros_pydantic(e)) from e
