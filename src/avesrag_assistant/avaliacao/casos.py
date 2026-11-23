"""Leitura e validação do `avaliacao/casos.yaml` (plano_assistant1.md §5).

A validação tem duas camadas. O modelo confere o formato e os atributos contra o
vocabulário, sem banco. `conferir_no_banco` confere as espécies esperadas: uma
espécie fora do registry é erro; uma espécie sem documento de busca (incompleta
ou fora do piloto) deixa o caso **inalcançável**, e o relatório o separa.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from avesrag_builder.core.models import SpeciesId
from avesrag_builder.core.vocabulario import (
    RegrasBico,
    Vocabulario,
    VocabularioInvalido,
    _erros_pydantic,
    _ler_yaml,
)
from avesrag_builder.db.collections import SPECIES_REGISTRY, SPECIES_RETRIEVAL_DOCS
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from pymongo.database import Database

from avesrag_assistant.busca.modelos import AtributosObservados, Texto, contexto

Origem = Literal["avistamento", "memoria", "foto", "llm"]
Uso = Literal["calibracao", "teste"]
IdCaso = Annotated[str, StringConstraints(pattern=r"^[a-z]\d{3}$")]


class CasosInvalidos(ValueError):
    """O arquivo de casos não passou na validação."""

    def __init__(self, arquivo: Path | str, erros: list[str]):
        self.arquivo = str(arquivo)
        self.erros = erros
        lista = "\n".join(f"  - {e}" for e in erros)
        super().__init__(f"{self.arquivo}: {len(erros)} erro(s)\n{lista}")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Caso(_Base):
    id: IdCaso
    descricao: Texto
    esperada: SpeciesId
    autoria: Texto  # quem escreveu: uma pessoa, `llm`, `claude`, `fumaca`
    origem: Origem
    uso: Uso
    atributos: AtributosObservados = Field(default_factory=AtributosObservados.model_construct)


class CasosArquivo(_Base):
    versao: Texto
    casos: list[Caso] = Field(min_length=1)


def _erros_cruzados(casos: Sequence[Caso]) -> list[str]:
    erros = []
    vistos: dict[str, int] = {}
    for i, caso in enumerate(casos):
        if caso.id in vistos:
            erros.append(f"casos.{i}.id: {caso.id!r} repetido (já em casos.{vistos[caso.id]})")
        vistos.setdefault(caso.id, i)
    usos: dict[str, set[str]] = defaultdict(set)
    for caso in casos:
        usos[caso.esperada].add(caso.uso)
    for especie, conjunto in sorted(usos.items()):
        if len(conjunto) > 1:
            erros.append(f"{especie}: a mesma espécie em calibracao e teste (mantenha num uso só)")
    return erros


def carregar_casos(
    path: Path | str, vocabulario: Vocabulario, regras_bico: RegrasBico
) -> list[Caso]:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise CasosInvalidos(path, e.erros) from e
    try:
        arquivo = CasosArquivo.model_validate(dados, context=contexto(vocabulario, regras_bico))
    except ValidationError as e:
        raise CasosInvalidos(path, _erros_pydantic(e)) from e
    if erros := _erros_cruzados(arquivo.casos):
        raise CasosInvalidos(path, erros)
    return arquivo.casos


@dataclass(frozen=True)
class Conferencia:
    inexistentes: list[str] = field(default_factory=list)  # fora do registry: erro
    inalcancaveis: frozenset[str] = frozenset()  # sem documento de busca


def conferir_no_banco(db: Database, casos: Sequence[Caso]) -> Conferencia:
    esperadas = sorted({c.esperada for c in casos})
    no_registry = set(
        db[SPECIES_REGISTRY].distinct("species_id", {"species_id": {"$in": esperadas}})
    )
    com_docs = set(
        db[SPECIES_RETRIEVAL_DOCS].distinct("species_id", {"species_id": {"$in": esperadas}})
    )
    return Conferencia(
        inexistentes=[s for s in esperadas if s not in no_registry],
        inalcancaveis=frozenset(s for s in esperadas if s in no_registry and s not in com_docs),
    )
