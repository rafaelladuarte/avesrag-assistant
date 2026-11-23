"""Reordenação das candidatas pelos atributos observados (plano_assistant1.md §3.2).

Funções puras: nada de Mongo nem de rede. Cada atributo informado vira itens
comparados com a ficha da espécie (`Atributos` do builder):

- `bate` (valor 1, ou 0,5 para a cor vista em outra parte do corpo);
- `conflita`: a ficha tem o dado, mas não o valor informado;
- `sem_dado`: a ficha não tem o dado. É neutro, porque a fonte pode só não ter citado.

O atributo bate se algum item bateu, e ganha `peso × soma / itens com dado`.
Só conflita se nenhum item bateu, e perde `peso × penalidade_conflito`. Os
pesos e a penalidade vêm de `config/ranking.yaml`, validado ao carregar.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

from avesrag_builder.core.models import Atributos

# Privados no builder: reaproveitados para não copiar o leitor que recusa chave duplicada.
from avesrag_builder.core.vocabulario import (
    RegrasBico,
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
    field_validator,
    model_validator,
)

from avesrag_assistant.busca.modelos import (
    ATRIBUTOS_LISTA,
    DIMENSOES_BICO,
    AtributosObservados,
    Bico,
    CorPorParte,
    DetalheAtributo,
)

# Valor de um item: None = sem_dado; 0 = conflita; > 0 = bate.
CONFLITO = 0.0
MEIO = 0.5
Item = tuple[str, float | None]


# ---------------------------------------------------------------------------
# config/ranking.yaml
# ---------------------------------------------------------------------------


class RankingInvalido(ValueError):
    """O `ranking.yaml` não passou na validação."""

    def __init__(self, arquivo: Path | str, erros: list[str]):
        self.arquivo = str(arquivo)
        self.erros = erros
        lista = "\n".join(f"  - {e}" for e in erros)
        super().__init__(f"{self.arquivo}: {len(erros)} erro(s)\n{lista}")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigVetor(_Base):
    peso: PositiveFloat
    candidatos: PositiveInt  # numCandidates do $vectorSearch
    documentos: PositiveInt  # limit do $vectorSearch
    especies: PositiveInt  # espécies que seguem para a reordenação

    @model_validator(mode="after")
    def _ordem(self) -> Self:
        if not self.especies <= self.documentos <= self.candidatos:
            raise ValueError("esperado especies ≤ documentos ≤ candidatos")
        return self


# O índice de busca não filtra as dimensões do bico (só tem o `bico_tipo`).
FORA_DO_HIBRIDO = ("bico",)


class NivelHibrido(_Base):
    """Uma busca extra do modo `hibrido`: os atributos deste nível viram filtro."""

    nome: str = Field(min_length=1)
    atributos: Literal["todos"] | list[str] = Field(min_length=1)

    @field_validator("atributos")
    @classmethod
    def _atributos_conhecidos(cls, v: Literal["todos"] | list[str]) -> Literal["todos"] | list[str]:
        if v == "todos":
            return v
        conhecidos = set(AtributosObservados.model_fields)
        erros = []
        if desconhecidos := sorted(set(v) - conhecidos):
            erros.append(f"atributos desconhecidos: {desconhecidos}")
        if fora := sorted(set(v) & set(FORA_DO_HIBRIDO)):
            erros.append(f"o índice não filtra {fora}")
        if len(set(v)) != len(v):
            erros.append("atributo repetido")
        if erros:
            raise ValueError("; ".join(erros))
        return v

    def campos(self) -> tuple[str, ...]:
        """Os campos do `AtributosObservados` que este nível usa como filtro."""
        if self.atributos == "todos":
            return tuple(c for c in AtributosObservados.model_fields if c not in FORA_DO_HIBRIDO)
        return tuple(self.atributos)


class ConfigHibrido(_Base):
    especies: PositiveInt  # espécies que cada nível acrescenta, no máximo
    niveis: list[NivelHibrido] = Field(min_length=1)  # do mais estrito ao mais solto

    @field_validator("niveis")
    @classmethod
    def _nomes_unicos(cls, v: list[NivelHibrido]) -> list[NivelHibrido]:
        nomes = [n.nome for n in v]
        if repetidos := sorted({n for n in nomes if nomes.count(n) > 1}):
            raise ValueError(f"níveis com o mesmo nome: {repetidos}")
        return v


class Ranking(_Base):
    versao: str = Field(min_length=1)
    vetor: ConfigVetor
    penalidade_conflito: float = Field(ge=0)
    pesos: dict[str, PositiveFloat]
    hibrido: ConfigHibrido | None = None  # só o modo `hibrido` usa

    @field_validator("pesos")
    @classmethod
    def _um_peso_por_atributo(cls, v: dict[str, float]) -> dict[str, float]:
        esperados = set(AtributosObservados.model_fields)
        erros = []
        if faltando := sorted(esperados - set(v)):
            erros.append(f"faltam pesos para {faltando}")
        if sobrando := sorted(set(v) - esperados):
            erros.append(f"atributos desconhecidos: {sobrando}")
        if erros:
            raise ValueError("; ".join(erros))
        return v


def carregar_ranking(path: Path | str) -> Ranking:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise RankingInvalido(path, e.erros) from e
    try:
        return Ranking.model_validate(dados)
    except ValidationError as e:
        raise RankingInvalido(path, _erros_pydantic(e)) from e


# ---------------------------------------------------------------------------
# Itens por atributo
# ---------------------------------------------------------------------------


def _itens_lista(informados: list[str], ficha: list[str] | None) -> list[Item]:
    if not ficha:
        return [(v, None) for v in informados]
    return [(v, 1.0 if v in ficha else CONFLITO) for v in informados]


def _itens_tamanho(informados: list[str], ficha: list[str] | None) -> list[Item]:
    # A ficha traz duas faixas perto dos cortes: basta haver interseção.
    rotulo = "/".join(informados)
    if not ficha:
        return [(rotulo, None)]
    return [(rotulo, 1.0 if set(informados) & set(ficha) else CONFLITO)]


def _itens_cores_por_parte(informados: list[CorPorParte], ficha: Atributos) -> list[Item]:
    pares = {(p.parte, p.cor) for p in ficha.cores_por_parte or []}
    cores_da_parte: dict[str, set[str]] = {}
    for parte, cor in pares:
        cores_da_parte.setdefault(parte, set()).add(cor)
    cores_vistas = {cor for _, cor in pares} | set(ficha.cores or [])

    itens: list[Item] = []
    for item in informados:
        rotulo = f"{item.parte}:{item.cor}"
        if (item.parte, item.cor) in pares:
            valor = 1.0
        elif item.parte in cores_da_parte:
            valor = CONFLITO  # a ficha descreve a parte com outra cor
        elif item.cor in cores_vistas:
            valor = MEIO  # a cor existe na ave, mas a ficha não diz onde
        else:
            valor = None
        itens.append((rotulo, valor))
    return itens


def _itens_bico(informado: Bico, ficha: Atributos, regras_bico: RegrasBico) -> list[Item]:
    pedidos = [(d, getattr(informado, d)) for d in DIMENSOES_BICO if getattr(informado, d)]
    pedidos += [("extra", v) for v in informado.extra or []]
    if ficha.bico_tipo is None:
        return [(f"{d}:{v}", None) for d, v in pedidos]
    perfil = regras_bico.perfil(ficha.bico_tipo)
    itens: list[Item] = []
    for dim, valor in pedidos:
        aceitos = getattr(perfil, dim)
        if dim == "extra" and not aceitos:
            itens.append((f"{dim}:{valor}", None))  # o perfil não define extra: neutro
        else:
            itens.append((f"{dim}:{valor}", 1.0 if valor in aceitos else CONFLITO))
    return itens


def _itens(
    nome: str, atributos: AtributosObservados, ficha: Atributos, regras_bico: RegrasBico
) -> list[Item]:
    valor = getattr(atributos, nome)
    if nome == "tamanho":
        return _itens_tamanho(valor, ficha.tamanho)
    if nome in ATRIBUTOS_LISTA:
        return _itens_lista(valor, getattr(ficha, nome))
    if nome == "cores_por_parte":
        return _itens_cores_por_parte(valor, ficha)
    if nome == "bico":
        return _itens_bico(valor, ficha, regras_bico)
    raise KeyError(f"atributo sem regra de pontuação: {nome!r}")


def _agregar(nome: str, itens: list[Item], peso: float, penalidade: float) -> DetalheAtributo:
    com_dado = [(rotulo, v) for rotulo, v in itens if v is not None]
    bateram = [rotulo for rotulo, v in com_dado if v > 0]
    conflitaram = [rotulo for rotulo, v in com_dado if v == CONFLITO]
    if not com_dado:
        resultado, pontos = "sem_dado", 0.0
    elif bateram:
        resultado = "bate"
        pontos = peso * sum(v for _, v in com_dado) / len(com_dado)
    else:
        resultado, pontos = "conflita", -peso * penalidade
    return DetalheAtributo(
        atributo=nome,
        resultado=resultado,
        pontos=pontos,
        bateram=bateram,
        conflitaram=conflitaram,
    )


def pontuar(
    atributos: AtributosObservados,
    ficha: Atributos,
    ranking: Ranking,
    regras_bico: RegrasBico,
) -> list[DetalheAtributo]:
    """Um detalhe por atributo informado, na ordem dos campos."""
    return [
        _agregar(
            nome,
            _itens(nome, atributos, ficha, regras_bico),
            ranking.pesos[nome],
            ranking.penalidade_conflito,
        )
        for nome in atributos.informados()
    ]


# ---------------------------------------------------------------------------
# Nota final e reordenação
# ---------------------------------------------------------------------------


def normalizar(notas: dict[str, float]) -> dict[str, float]:
    """Min-max entre as candidatas; se todas forem iguais, todas valem 1."""
    if not notas:
        return {}
    menor, maior = min(notas.values()), max(notas.values())
    if maior == menor:
        return {chave: 1.0 for chave in notas}
    return {chave: (n - menor) / (maior - menor) for chave, n in notas.items()}


@dataclass(frozen=True)
class Entrada:
    species_id: str
    nota_vetor: float
    ficha: Atributos


@dataclass(frozen=True)
class Pontuada:
    species_id: str
    nota_vetor: float
    nota_vetor_normalizada: float
    pontos: float
    nota_final: float
    detalhes: list[DetalheAtributo] = field(default_factory=list)


def reordenar(
    entradas: Sequence[Entrada],
    atributos: AtributosObservados,
    ranking: Ranking,
    regras_bico: RegrasBico,
) -> list[Pontuada]:
    """`nota_final = peso_vetor × nota normalizada + Σ pontos`, da maior para a menor.

    O empate é desfeito pelo `species_id`, para o resultado ser estável.
    """
    normalizadas = normalizar({e.species_id: e.nota_vetor for e in entradas})
    pontuadas = []
    for e in entradas:
        detalhes = pontuar(atributos, e.ficha, ranking, regras_bico)
        pontos = sum(d.pontos for d in detalhes)
        norm = normalizadas[e.species_id]
        pontuadas.append(
            Pontuada(
                species_id=e.species_id,
                nota_vetor=e.nota_vetor,
                nota_vetor_normalizada=norm,
                pontos=pontos,
                nota_final=ranking.vetor.peso * norm + pontos,
                detalhes=detalhes,
            )
        )
    return sorted(pontuadas, key=lambda p: (-p.nota_final, p.species_id))
