"""Retriever: vetor da descrição + atributos observados → espécies ranqueadas.

Etapas (plano_assistant1.md §3.2):

1. `$vectorSearch` nos 3 domínios; cada espécie vale pela maior nota entre os
   seus documentos, e ficam as `ranking.vetor.especies` melhores;
2. as fichas dessas espécies na `species`;
3. a reordenação pelos atributos (`pontuacao.reordenar`).

`buscar` recebe o vetor pronto: quem gera o embedding é o chamador, pelo cache.
Os modos passam pelo mesmo caminho: `vetor` e `filtros_duros` reordenam sem
atributos (a ordem fica a do vetor), e `filtros_duros` ainda põe os atributos
como `$and` no `filter`, como a fumaça do builder. O `hibrido` junta às espécies
do vetor as de buscas extras com os atributos como filtro, do nível mais estrito
ao mais solto (`ranking.hibrido`), e reordena tudo como o `reordenado`. Com
`ranking.hibrido.atributos`, entram ainda as espécies com mais pontos nos
atributos entre todas as fichas da busca, sem depender do vetor: quando a
descrição não se parece com o texto da ficha, é por aí que a espécie chega.
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
from avesrag_assistant.busca.pontuacao import (
    Entrada,
    Ranking,
    melhores_por_atributos,
    reordenar,
)

log = logging.getLogger(__name__)

Modo = Literal["vetor", "filtros_duros", "reordenado", "hibrido"]
MODOS: tuple[Modo, ...] = ("vetor", "filtros_duros", "reordenado", "hibrido")
VETOR = "vetor"  # origem das espécies da busca vetorial sem filtro
ATRIBUTOS = "atributos"  # origem das espécies que entram pelos pontos dos atributos
TOP = 10


@dataclass(frozen=True)
class Acerto:
    """Uma espécie pela maior nota entre os seus documentos, e o domínio dessa nota."""

    species_id: str
    nota: float
    dominio: str
    origem: str = VETOR  # a busca que trouxe a espécie


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


def filtro_duro(
    atributos: AtributosObservados, campos: Sequence[str] | None = None
) -> dict[str, Any] | None:
    """Os atributos como filtro eliminatório do índice (`filtros_duros` e `hibrido`).

    Cada valor de lista vira uma igualdade; a cor de `cores_por_parte` vira
    `filtros.cores` (o índice não tem os pares); o `tamanho` vira `$in`, porque
    basta uma das faixas. O bico fica de fora: o índice só tem o `bico_tipo`.
    Com `campos`, só esses atributos entram no filtro.
    """
    if campos is not None:
        atributos = AtributosObservados.model_construct(
            **{c: getattr(atributos, c) for c in campos}
        )
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


def agregar(docs: Iterable[dict[str, Any]], n: int, origem: str = VETOR) -> list[Acerto]:
    """Documentos → as `n` melhores espécies pela maior nota; desempate pelo `species_id`."""
    melhores: dict[str, Acerto] = {}
    for d in docs:
        sid, nota = d["species_id"], float(d["nota"])
        atual = melhores.get(sid)
        if atual is None or nota > atual.nota:
            melhores[sid] = Acerto(sid, nota, d["dominio"], origem)
    return sorted(melhores.values(), key=lambda a: (-a.nota, a.species_id))[:n]


def unir(base: Sequence[Acerto], extras: Iterable[Acerto]) -> list[Acerto]:
    """Acrescenta à base as espécies novas, na ordem em que chegam.

    Uma espécie repetida fica com a maior nota e o domínio dela, mas mantém a
    origem da primeira busca que a trouxe.
    """
    unidos = {a.species_id: a for a in base}
    for a in extras:
        atual = unidos.get(a.species_id)
        if atual is None:
            unidos[a.species_id] = a
        elif a.nota > atual.nota:
            unidos[a.species_id] = Acerto(a.species_id, a.nota, a.dominio, atual.origem)
    return list(unidos.values())


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------


def buscar_vetor(
    db: Database,
    vetor: list[float],
    ranking: Ranking,
    recusadas: Sequence[str] = (),
    filtro: dict[str, Any] | None = None,
    n: int | None = None,
    origem: str = VETOR,
) -> list[Acerto]:
    """As `n` melhores espécies (padrão: `ranking.vetor.especies`)."""
    docs = db[SPECIES_RETRIEVAL_DOCS].aggregate(etapa_vetor(vetor, ranking, recusadas, filtro))
    return agregar(docs, n or ranking.vetor.especies, origem)


def buscar_hibrido(
    db: Database,
    consulta: Consulta,
    vetor: list[float],
    ranking: Ranking,
    regras_bico: RegrasBico | None = None,
    universo: dict[str, Ficha] | None = None,
) -> list[Acerto]:
    """As espécies do vetor mais as de cada nível de `ranking.hibrido`.

    Um nível com o filtro vazio, ou igual ao do nível anterior, é pulado. Com
    `ranking.hibrido.atributos` e o `universo` (todas as fichas da busca), entram
    as espécies com mais pontos nos atributos, com a nota do vetor de cada uma.
    """
    if ranking.hibrido is None:
        raise ValueError("o modo hibrido precisa da seção `hibrido` no ranking.yaml")
    acertos = buscar_vetor(db, vetor, ranking, consulta.recusadas)
    anterior = None
    for nivel in ranking.hibrido.niveis:
        filtro = filtro_duro(consulta.atributos, nivel.campos())
        if filtro is None or filtro == anterior:
            continue
        anterior = filtro
        extras = buscar_vetor(
            db, vetor, ranking, consulta.recusadas, filtro, ranking.hibrido.especies, nivel.nome
        )
        acertos = unir(acertos, extras)

    n = ranking.hibrido.atributos
    if n and universo and regras_bico is not None:
        recusadas = set(consulta.recusadas)
        fichas = {s: f.atributos for s, f in universo.items() if s not in recusadas}
        ids = melhores_por_atributos(fichas, consulta.atributos, ranking, regras_bico, n)
        if ids:
            filtro = {"species_id": {"$in": ids}}
            extras = buscar_vetor(db, vetor, ranking, (), filtro, len(ids), ATRIBUTOS)
            acertos = unir(acertos, extras)
    return acertos


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


def carregar_universo(db: Database) -> dict[str, Ficha]:
    """As fichas de todas as espécies com documento de busca."""
    return carregar_fichas(db, db[SPECIES_RETRIEVAL_DOCS].distinct("species_id", {}))


def montar_candidatas(
    db: Database,
    acertos: Sequence[Acerto],
    atributos: AtributosObservados,
    ranking: Ranking,
    regras_bico: RegrasBico,
    k: int = TOP,
) -> list[Candidata]:
    """Acertos da busca → fichas → reordenação → as `k` primeiras como `Candidata`.

    Sem atributos, a ordem é a da nota da busca, com o mesmo desempate.
    """
    fichas = carregar_fichas(db, [a.species_id for a in acertos])
    sem_ficha = [a.species_id for a in acertos if a.species_id not in fichas]
    if sem_ficha:
        log.warning("espécies com documento de busca e sem ficha: %s", ", ".join(sem_ficha))
    acertos = [a for a in acertos if a.species_id in fichas]

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
                origem=por_id[p.species_id].origem,
                pontos=p.pontos,
                nota_final=p.nota_final,
                detalhes=p.detalhes,
            )
        )
    return candidatas


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
    reordena = modo in ("reordenado", "hibrido")
    atributos = consulta.atributos if reordena else AtributosObservados.model_construct()

    pelos_atributos = (
        modo == "hibrido" and ranking.hibrido is not None and ranking.hibrido.atributos
    )
    universo = carregar_universo(db) if pelos_atributos and atributos.informados() else {}
    if modo == "hibrido":
        acertos = buscar_hibrido(db, consulta, vetor, ranking, regras_bico, universo)
    else:
        filtro = filtro_duro(consulta.atributos) if modo == "filtros_duros" else None
        acertos = buscar_vetor(db, vetor, ranking, consulta.recusadas, filtro)
    return montar_candidatas(db, acertos, atributos, ranking, regras_bico, k)
