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
