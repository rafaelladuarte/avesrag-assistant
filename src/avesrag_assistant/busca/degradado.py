"""Modo degradado: `$search` por texto, sem embedding nem atributos.

A Arquitetura usa este modo quando a conversa não junta o mínimo de atributos
depois de 3 perguntas. A busca vai no índice `texto_especies` do builder
(`texto` e `nomes`, analisador `lucene.portuguese`), e cada espécie vale pela
maior nota entre os seus documentos, como no modo vetor. Na `Candidata`, a
`nota_vetor` traz a nota do `$search`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from avesrag_builder.core.vocabulario import RegrasBico
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS
from avesrag_builder.docs.indices import INDICE_TEXTO
from pymongo.database import Database

from avesrag_assistant.busca.modelos import AtributosObservados, Candidata, Consulta
from avesrag_assistant.busca.pontuacao import Ranking
from avesrag_assistant.busca.retriever import TOP, agregar, montar_candidatas

CAMPOS_TEXTO = ("texto", "nomes")


def etapa_texto(
    descricao: str, ranking: Ranking, recusadas: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Pipeline do `$search`. As recusadas saem num `$match`, porque o índice de
    texto só mapeia `texto` e `nomes`."""
    pipeline: list[dict[str, Any]] = [
        {
            "$search": {
                "index": INDICE_TEXTO,
                "text": {"query": descricao, "path": list(CAMPOS_TEXTO)},
            }
        },
    ]
    if recusadas:
        pipeline.append({"$match": {"species_id": {"$nin": list(recusadas)}}})
    pipeline += [
        {"$limit": ranking.vetor.documentos},
        {
            "$project": {
                "_id": 0,
                "species_id": 1,
                "dominio": 1,
                "nota": {"$meta": "searchScore"},
            }
        },
    ]
    return pipeline


def buscar_degradado(
    db: Database,
    consulta: Consulta,
    ranking: Ranking,
    regras_bico: RegrasBico,
    k: int = TOP,
) -> list[Candidata]:
    """As `k` espécies pela busca textual da `descricao`; os atributos não entram."""
    docs = db[SPECIES_RETRIEVAL_DOCS].aggregate(
        etapa_texto(consulta.descricao, ranking, consulta.recusadas)
    )
    acertos = agregar(docs, ranking.vetor.especies)
    vazios = AtributosObservados.model_construct()
    return montar_candidatas(db, acertos, vazios, ranking, regras_bico, k)
