"""Roda os casos nos modos de busca e mede a posição da espécie esperada.

A posição é medida até `ranking.vetor.especies` (as 40 que vão para a
reordenação), para a mediana ter sentido; fora disso, `None` (">40"). Os
embeddings de todas as descrições saem numa chamada só, pelo cache: a segunda
avaliação não chama a API. Com `sugeridas` (caso → species_id sugeridos pelo
LLM), os modos que reordenam recebem as sugestões, como na conversa.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from avesrag_builder.core.vocabulario import RegrasBico
from pymongo.database import Database

from avesrag_assistant.avaliacao.casos import Caso
from avesrag_assistant.busca.cache import CacheEmbeddings
from avesrag_assistant.busca.degradado import buscar_degradado
from avesrag_assistant.busca.modelos import Candidata, Consulta
from avesrag_assistant.busca.pontuacao import Ranking
from avesrag_assistant.busca.retriever import MODOS, buscar

DEGRADADO = "degradado"
MODOS_AVALIACAO: tuple[str, ...] = (*MODOS, DEGRADADO)
PRIMEIRAS = 3


@dataclass
class ResultadoCaso:
    caso: Caso
    alcancavel: bool
    posicoes: dict[str, int | None] = field(default_factory=dict)
    primeiras: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Avaliacao:
    modos: tuple[str, ...]
    ate: int  # posições medidas até aqui
    resultados: list[ResultadoCaso] = field(default_factory=list)
    chamadas: int = 0  # chamadas de embedding feitas nesta avaliação
    tokens: int = 0


def posicao(candidatas: Sequence[Candidata], species_id: str) -> int | None:
    for i, c in enumerate(candidatas, start=1):
        if c.species_id == species_id:
            return i
    return None


def rodar(
    db: Database,
    casos: Sequence[Caso],
    cache: CacheEmbeddings | None,
    ranking: Ranking,
    regras_bico: RegrasBico,
    modos: Sequence[str] = MODOS_AVALIACAO,
    inalcancaveis: frozenset[str] = frozenset(),
    sugeridas: Mapping[str, Sequence[str]] | None = None,
) -> Avaliacao:
    desconhecidos = [m for m in modos if m not in MODOS_AVALIACAO]
    if desconhecidos:
        raise ValueError(f"modos desconhecidos: {desconhecidos} (use {MODOS_AVALIACAO})")
    ate = ranking.vetor.especies
    av = Avaliacao(modos=tuple(modos), ate=ate)

    vetores: list[list[float] | None] = [None] * len(casos)
    if any(m != DEGRADADO for m in modos):
        if cache is None:
            raise ValueError("os modos com vetor precisam do cache de embeddings")
        chamadas, tokens = cache.chamadas, cache.tokens
        vetores = list(cache.vetores([c.descricao for c in casos]))
        av.chamadas, av.tokens = cache.chamadas - chamadas, cache.tokens - tokens

    for caso, vetor in zip(casos, vetores, strict=True):
        # Os atributos do caso já foram validados contra o vocabulário ao carregar.
        consulta = Consulta.model_construct(
            descricao=caso.descricao, atributos=caso.atributos, recusadas=[]
        )
        res = ResultadoCaso(caso=caso, alcancavel=caso.esperada not in inalcancaveis)
        for modo in modos:
            if modo == DEGRADADO:
                candidatas = buscar_degradado(db, consulta, ranking, regras_bico, k=ate)
            else:
                candidatas = buscar(
                    db,
                    consulta,
                    vetor,
                    ranking,
                    regras_bico,
                    modo,
                    k=ate,
                    sugeridas=(sugeridas or {}).get(caso.id, ()),
                )
            res.posicoes[modo] = posicao(candidatas, caso.esperada)
            res.primeiras[modo] = [c.species_id for c in candidatas[:PRIMEIRAS]]
        av.resultados.append(res)
    return av
