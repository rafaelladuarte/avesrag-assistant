"""Retriever com um banco falso: pipeline, filtro duro, agregação e os três modos."""

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS

from avesrag_assistant.busca.modelos import Consulta, contexto
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.busca.retriever import Acerto, agregar, buscar, etapa_vetor, filtro_duro
from avesrag_assistant.settings import ROOT_DIR
from conftest import DOCS, FICHAS, BancoFalso


@pytest.fixture(scope="module")
def ranking():
    return carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")


# ---------------------------------------------------------------------------
# etapa_vetor
# ---------------------------------------------------------------------------


def test_etapa_vetor_usa_o_ranking_e_sem_filtro(ranking):
    pipeline = etapa_vetor([0.1], ranking)
    etapa = pipeline[0]["$vectorSearch"]
    assert etapa["numCandidates"] == ranking.vetor.candidatos
    assert etapa["limit"] == ranking.vetor.documentos
    assert "filter" not in etapa
    assert pipeline[1]["$project"]["nota"] == {"$meta": "vectorSearchScore"}


def test_etapa_vetor_com_recusadas(ranking):
    etapa = etapa_vetor([0.1], ranking, ["sp_a"])[0]["$vectorSearch"]
    assert etapa["filter"] == {"species_id": {"$nin": ["sp_a"]}}


def test_etapa_vetor_com_recusadas_e_filtro(ranking):
    etapa = etapa_vetor([0.1], ranking, ["sp_a"], {"filtros.cores": "amarelo"})[0]["$vectorSearch"]
    assert etapa["filter"] == {
        "$and": [{"species_id": {"$nin": ["sp_a"]}}, {"filtros.cores": "amarelo"}]
    }


# ---------------------------------------------------------------------------
# filtro_duro
# ---------------------------------------------------------------------------


def test_filtro_duro_sem_atributos(obs):
    assert filtro_duro(obs()) is None


def test_filtro_duro_um_valor(obs):
    assert filtro_duro(obs(marcas=["sobrancelha"])) == {"filtros.marcas": "sobrancelha"}


def test_filtro_duro_listas_tamanho_cor_por_parte_e_bico(obs):
    a = obs(
        tamanho=["pequeno", "medio"],
        cores=["amarelo"],
        cores_por_parte=[
            {"parte": "barriga", "cor": "amarelo"},
            {"parte": "cabeca", "cor": "branco"},
        ],
        marcas=["sobrancelha"],
        bico={"espessura": "grosso"},
    )
    assert filtro_duro(a) == {
        "$and": [
            {"filtros.tamanho": {"$in": ["pequeno", "medio"]}},
            {"filtros.cores": "amarelo"},
            {"filtros.marcas": "sobrancelha"},
            {"filtros.cores": "branco"},
        ]
    }


# ---------------------------------------------------------------------------
# agregar
# ---------------------------------------------------------------------------


def test_agregar_maior_nota_dominio_corte_e_desempate():
    docs = [
        {"species_id": "sp_b", "dominio": "aparencia", "nota": 0.70},
        {"species_id": "sp_b", "dominio": "voz_habitos", "nota": 0.75},
        {"species_id": "sp_a", "dominio": "aparencia", "nota": 0.75},
        {"species_id": "sp_c", "dominio": "aparencia", "nota": 0.60},
    ]
    assert agregar(docs, 2) == [
        Acerto("sp_a", 0.75, "aparencia"),
        Acerto("sp_b", 0.75, "voz_habitos"),
    ]


# ---------------------------------------------------------------------------
# buscar com banco falso
# ---------------------------------------------------------------------------


@pytest.fixture
def consulta(vocab_regras):
    def _consulta(**atributos):
        return Consulta.model_validate(
            {"descricao": "uma ave", "atributos": atributos}, context=contexto(*vocab_regras)
        )

    return _consulta


def test_buscar_reordenado_muda_a_ordem(consulta, ranking, vocab_regras):
    db = BancoFalso(DOCS, FICHAS)
    c = consulta(cores=["amarelo"], marcas=["sobrancelha"])
    saida = buscar(db, c, [0.1], ranking, vocab_regras[1])
    assert [x.species_id for x in saida] == ["sp_amarela", "sp_verde", "sp_cinza"]
    primeira = saida[0]
    assert (primeira.nome, primeira.dominio, primeira.nota_vetor) == (
        "ave-amarela",
        "voz_habitos",
        0.78,
    )
    assert [d.resultado for d in primeira.detalhes] == ["bate", "bate"]
    assert saida[2].nome == "Ave cinza"  # sem nomes.pt, o científico


def test_buscar_modo_vetor_ignora_os_atributos(consulta, ranking, vocab_regras):
    db = BancoFalso(DOCS, FICHAS)
    c = consulta(cores=["amarelo"])
    saida = buscar(db, c, [0.1], ranking, vocab_regras[1], modo="vetor")
    assert [x.species_id for x in saida] == ["sp_verde", "sp_amarela", "sp_cinza"]
    assert all(x.detalhes == [] and x.pontos == 0 for x in saida)
    assert "filter" not in db[SPECIES_RETRIEVAL_DOCS].pipelines[0][0]["$vectorSearch"]


def test_buscar_filtros_duros_manda_o_filtro(consulta, ranking, vocab_regras):
    db = BancoFalso(DOCS, FICHAS)
    buscar(db, consulta(cores=["amarelo"]), [0.1], ranking, vocab_regras[1], modo="filtros_duros")
    etapa = db[SPECIES_RETRIEVAL_DOCS].pipelines[0][0]["$vectorSearch"]
    assert etapa["filter"] == {"filtros.cores": "amarelo"}


def test_buscar_corta_em_k_e_tira_sem_ficha(consulta, ranking, vocab_regras):
    saida = buscar(BancoFalso(DOCS, FICHAS), consulta(), [0.1], ranking, vocab_regras[1], k=2)
    assert [x.species_id for x in saida] == ["sp_verde", "sp_amarela"]


def test_buscar_modo_desconhecido(consulta, ranking, vocab_regras):
    with pytest.raises(ValueError, match="modo desconhecido"):
        buscar(BancoFalso(DOCS, FICHAS), consulta(), [0.1], ranking, vocab_regras[1], modo="x")
