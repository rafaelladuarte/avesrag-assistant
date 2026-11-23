"""Modo degradado: `$search` por texto, sem embedding nem atributos."""

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS

from avesrag_assistant.busca.degradado import buscar_degradado, etapa_texto
from avesrag_assistant.busca.modelos import validar_consulta
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.settings import ROOT_DIR
from conftest import DOCS, FICHAS, BancoFalso


@pytest.fixture(scope="module")
def ranking():
    return carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")


def test_etapa_texto_sem_recusadas(ranking):
    pipeline = etapa_texto("bem-te-vi", ranking)
    busca = pipeline[0]["$search"]
    assert busca["index"] == "texto_especies"
    assert busca["text"] == {"query": "bem-te-vi", "path": ["texto", "nomes"]}
    assert [list(etapa)[0] for etapa in pipeline] == ["$search", "$limit", "$project"]
    assert pipeline[1]["$limit"] == ranking.vetor.documentos
    assert pipeline[2]["$project"]["nota"] == {"$meta": "searchScore"}


def test_etapa_texto_tira_as_recusadas_antes_do_limite(ranking):
    pipeline = etapa_texto("bem-te-vi", ranking, ["sp_a"])
    assert pipeline[1] == {"$match": {"species_id": {"$nin": ["sp_a"]}}}
    assert "$limit" in pipeline[2]


def test_buscar_degradado_ignora_os_atributos(ranking, vocab_regras):
    db = BancoFalso(DOCS, FICHAS)
    consulta = validar_consulta(
        {"descricao": "ave amarela", "atributos": {"cores": ["amarelo"]}}, *vocab_regras
    )
    saida = buscar_degradado(db, consulta, ranking, vocab_regras[1], k=2)
    assert [c.species_id for c in saida] == ["sp_verde", "sp_amarela"]
    assert all(c.detalhes == [] and c.pontos == 0 for c in saida)
    assert "$search" in db[SPECIES_RETRIEVAL_DOCS].pipelines[0][0]
