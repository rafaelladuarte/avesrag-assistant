"""Retriever: vetor da descrição + atributos observados → espécies ranqueadas.

Etapas (plano_assistant1.md §3.2):

1. `$vectorSearch` nos 3 domínios; cada espécie vale pela maior nota entre os
   seus documentos, e ficam as `ranking.vetor.especies` melhores;
2. as fichas dessas espécies na `species`;
3. a reordenação pelos atributos (`pontuacao.reordenar`).

`buscar` recebe o vetor pronto: quem gera o embedding é o chamador, pelo cache.
Os três modos da avaliação passam pelo mesmo caminho: `vetor` e `filtros_duros`
reordenam sem atributos (a ordem fica a do vetor), e `filtros_duros` ainda põe
os atributos como `$and` no `filter`, como a fumaça do builder.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from avesrag_builder.core.models import Atributos
from avesrag_builder.core.vocabulario import RegrasBico
from avesrag_builder.db.collections import SPECIES, SPECIES_RETRIEVAL_DOCS
from avesrag_builder.docs.indices import INDICE_VETOR
from avesrag_builder.docs.montar import CHAVES_FILTRO
from pymongo.database import Database

from avesrag_assistant.busca.modelos import (
    ATRIBUTOS_LISTA,
    AtributosObservados,
    Candidata,
    Consulta,
)
from avesrag_assistant.busca.pontuacao import Entrada, Ranking, reordenar

log = logging.getLogger(__name__)

Modo = Literal["vetor", "filtros_duros", "reordenado"]
MODOS: tuple[Modo, ...] = ("vetor", "filtros_duros", "reordenado")
TOP = 10


@dataclass(frozen=True)
class Acerto:
    """Uma espécie pela maior nota entre os seus documentos, e o domínio dessa nota."""

    species_id: str
    nota: float
    dominio: str


@dataclass(frozen=True)
class Ficha:
    species_id: str
    nome_cientifico: str
    nome: str
    atributos: Atributos


# ---------------------------------------------------------------------------
# Funções puras
# ---------------------------------------------------------------------------


def etapa_vetor(
    vetor: list[float],
    ranking: Ranking,
    recusadas: Sequence[str] = (),
    filtro: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pipeline do `$vectorSearch` em species_retrieval_docs."""
    etapa: dict[str, Any] = {
        "index": INDICE_VETOR,
        "path": "embedding",
        "queryVector": vetor,
        "numCandidates": ranking.vetor.candidatos,
        "limit": ranking.vetor.documentos,
    }
    condicoes = []
    if recusadas:
        condicoes.append({"species_id": {"$nin": list(recusadas)}})
    if filtro:
        condicoes.append(filtro)
    if len(condicoes) == 1:
        etapa["filter"] = condicoes[0]
    elif condicoes:
        etapa["filter"] = {"$and": condicoes}
    return [
        {"$vectorSearch": etapa},
        {
            "$project": {
                "_id": 0,
                "species_id": 1,
                "dominio": 1,
                "nota": {"$meta": "vectorSearchScore"},
            }
        },
    ]


def filtro_duro(atributos: AtributosObservados) -> dict[str, Any] | None:
    """Os atributos como filtro eliminatório do índice (modo `filtros_duros`).

    Cada valor de lista vira uma igualdade; a cor de `cores_por_parte` vira
    `filtros.cores` (o índice não tem os pares); o `tamanho` vira `$in`, porque
    basta uma das faixas. O bico fica de fora: o índice só tem o `bico_tipo`.
    """
    condicoes: list[dict[str, Any]] = []
    for nome in ATRIBUTOS_LISTA:
        valores = getattr(atributos, nome)
        if not valores or nome not in CHAVES_FILTRO:
            continue
        if nome == "tamanho":
            condicoes.append({"filtros.tamanho": {"$in": list(valores)}})
        else:
            condicoes += [{f"filtros.{nome}": v} for v in valores]
    cores = list(atributos.cores or [])
    for par in atributos.cores_por_parte or []:
        if par.cor not in cores:
            cores.append(par.cor)
            condicoes.append({"filtros.cores": par.cor})
    if not condicoes:
        return None
    return condicoes[0] if len(condicoes) == 1 else {"$and": condicoes}


def agregar(docs: Iterable[dict[str, Any]], n: int) -> list[Acerto]:
    """Documentos → as `n` melhores espécies pela maior nota; desempate pelo `species_id`."""
    melhores: dict[str, Acerto] = {}
    for d in docs:
        sid, nota = d["species_id"], float(d["nota"])
        atual = melhores.get(sid)
        if atual is None or nota > atual.nota:
            melhores[sid] = Acerto(sid, nota, d["dominio"])
    return sorted(melhores.values(), key=lambda a: (-a.nota, a.species_id))[:n]


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------


def buscar_vetor(
    db: Database,
    vetor: list[float],
    ranking: Ranking,
    recusadas: Sequence[str] = (),
    filtro: dict[str, Any] | None = None,
) -> list[Acerto]:
    docs = db[SPECIES_RETRIEVAL_DOCS].aggregate(etapa_vetor(vetor, ranking, recusadas, filtro))
    return agregar(docs, ranking.vetor.especies)


def carregar_fichas(db: Database, species_ids: Sequence[str]) -> dict[str, Ficha]:
    cursor = db[SPECIES].find(
        {"species_id": {"$in": list(species_ids)}},
        {"_id": 0, "species_id": 1, "nome_cientifico": 1, "nomes.pt": 1, "atributos": 1},
    )
    fichas = {}
    for doc in cursor:
        nomes = (doc.get("nomes") or {}).get("pt") or [doc["nome_cientifico"]]
        fichas[doc["species_id"]] = Ficha(
            species_id=doc["species_id"],
            nome_cientifico=doc["nome_cientifico"],
            nome=nomes[0],
            atributos=Atributos.model_validate(doc.get("atributos") or {}),
        )
    return fichas


def buscar(
    db: Database,
    consulta: Consulta,
    vetor: list[float],
    ranking: Ranking,
    regras_bico: RegrasBico,
    modo: Modo = "reordenado",
    k: int = TOP,
) -> list[Candidata]:
    """As `k` espécies candidatas para a consulta, no modo pedido."""
    if modo not in MODOS:
        raise ValueError(f"modo desconhecido: {modo!r} (use {', '.join(MODOS)})")
    filtro = filtro_duro(consulta.atributos) if modo == "filtros_duros" else None
    acertos = buscar_vetor(db, vetor, ranking, consulta.recusadas, filtro)
    fichas = carregar_fichas(db, [a.species_id for a in acertos])
    sem_ficha = [a.species_id for a in acertos if a.species_id not in fichas]
    if sem_ficha:
        log.warning("espécies com documento de busca e sem ficha: %s", ", ".join(sem_ficha))
    acertos = [a for a in acertos if a.species_id in fichas]

    atributos = (
        consulta.atributos if modo == "reordenado" else AtributosObservados.model_construct()
    )
    entradas = [Entrada(a.species_id, a.nota, fichas[a.species_id].atributos) for a in acertos]
    por_id = {a.species_id: a for a in acertos}
    candidatas = []
    for p in reordenar(entradas, atributos, ranking, regras_bico)[:k]:
        ficha = fichas[p.species_id]
        candidatas.append(
            Candidata(
                species_id=p.species_id,
                nome_cientifico=ficha.nome_cientifico,
                nome=ficha.nome,
                nota_vetor=p.nota_vetor,
                nota_vetor_normalizada=p.nota_vetor_normalizada,
                dominio=por_id[p.species_id].dominio,
                pontos=p.pontos,
                nota_final=p.nota_final,
                detalhes=p.detalhes,
            )
        )
    return candidatas
