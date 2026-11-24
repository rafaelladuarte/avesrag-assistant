"""Retriever com um banco falso: pipeline, filtro duro, agregação, união e os modos."""

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS

from avesrag_assistant.busca.modelos import Consulta, contexto
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.busca.retriever import (
    Acerto,
    agregar,
    buscar,
    etapa_vetor,
    filtro_duro,
    unir,
)
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


def test_filtro_duro_so_com_os_campos_pedidos(obs):
    a = obs(
        cores_por_parte=[{"parte": "barriga", "cor": "amarelo"}],
        marcas=["sobrancelha"],
        ambiente=["mata"],
    )
    assert filtro_duro(a, ["cores", "cores_por_parte"]) == {"filtros.cores": "amarelo"}
    assert filtro_duro(a, ["cauda"]) is None


# ---------------------------------------------------------------------------
# agregar e unir
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


def test_agregar_marca_a_origem():
    docs = [{"species_id": "sp_a", "dominio": "aparencia", "nota": 0.7}]
    assert agregar(docs, 5, "cores") == [Acerto("sp_a", 0.7, "aparencia", "cores")]


def test_unir_acrescenta_as_novas_e_sobe_a_nota_da_repetida():
    base = [Acerto("sp_a", 0.8, "aparencia"), Acerto("sp_b", 0.7, "aparencia")]
    extras = [
        Acerto("sp_b", 0.75, "voz_habitos", "completo"),
        Acerto("sp_c", 0.6, "aparencia", "completo"),
        Acerto("sp_a", 0.5, "aparencia", "completo"),
    ]
    assert unir(base, extras) == [
        Acerto("sp_a", 0.8, "aparencia"),
        Acerto("sp_b", 0.75, "voz_habitos"),  # a nota sobe, a origem fica a do vetor
        Acerto("sp_c", 0.6, "aparencia", "completo"),
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


# ---------------------------------------------------------------------------
# modo hibrido
# ---------------------------------------------------------------------------

RARA = {
    "species_id": "sp_rara",
    "nome_cientifico": "Ave rara",
    "nomes": {"pt": ["ave-rara"]},
    "atributos": {"cores": ["amarelo"], "marcas": ["sobrancelha"], "ambiente": ["mata"]},
}


def _so_niveis(ranking):
    """O ranking sem a fonte por atributos: só os níveis do híbrido."""
    return ranking.model_copy(
        update={"hibrido": ranking.hibrido.model_copy(update={"atributos": 0})}
    )


def _filtro(pipeline):
    return pipeline[0]["$vectorSearch"].get("filter")


def _banco_hibrido():
    """O vetor sem filtro traz DOCS; qualquer busca filtrada traz só a sp_rara."""

    def responder(pipeline):
        if "filtros." in str(_filtro(pipeline)):
            return [{"species_id": "sp_rara", "dominio": "aparencia", "nota": 0.65}]
        return DOCS

    db = BancoFalso(DOCS, [*FICHAS, RARA])
    db[SPECIES_RETRIEVAL_DOCS].responder = responder
    return db


def test_hibrido_uma_busca_por_nivel_e_a_especie_so_do_filtro(consulta, ranking, vocab_regras):
    db = _banco_hibrido()
    c = consulta(cores=["amarelo"], marcas=["sobrancelha"], ambiente=["mata"])
    saida = buscar(db, c, [0.1], _so_niveis(ranking), vocab_regras[1], modo="hibrido")

    filtros = [_filtro(p) for p in db[SPECIES_RETRIEVAL_DOCS].pipelines]
    assert filtros == [
        None,
        {
            "$and": [
                {"filtros.cores": "amarelo"},
                {"filtros.marcas": "sobrancelha"},
                {"filtros.ambiente": "mata"},
            ]
        },
        {"$and": [{"filtros.cores": "amarelo"}, {"filtros.marcas": "sobrancelha"}]},
        {"filtros.cores": "amarelo"},
    ]
    rara = next(x for x in saida if x.species_id == "sp_rara")
    assert rara.origem == "completo"  # a primeira busca que a trouxe
    assert {x.origem for x in saida if x.species_id != "sp_rara"} == {"vetor"}
    # Com a menor nota do vetor, mas reordenada pelos atributos: passa a verde, que conflita.
    ordem = [x.species_id for x in saida]
    assert ordem.index("sp_rara") < ordem.index("sp_verde")


def test_hibrido_pula_nivel_vazio_ou_repetido_e_leva_as_recusadas(consulta, ranking, vocab_regras):
    db = _banco_hibrido()
    c = consulta(cores=["amarelo"])
    c = c.model_copy(update={"recusadas": ["sp_verde"]})
    buscar(db, c, [0.1], _so_niveis(ranking), vocab_regras[1], modo="hibrido")

    filtros = [_filtro(p) for p in db[SPECIES_RETRIEVAL_DOCS].pipelines]
    nin = {"species_id": {"$nin": ["sp_verde"]}}
    # completo, aparencia e cores dão o mesmo filtro: só uma busca extra.
    assert filtros == [nin, {"$and": [nin, {"filtros.cores": "amarelo"}]}]


def test_hibrido_sem_atributos_e_so_o_vetor(consulta, ranking, vocab_regras):
    db = _banco_hibrido()
    saida = buscar(db, consulta(), [0.1], ranking, vocab_regras[1], modo="hibrido")
    assert len(db[SPECIES_RETRIEVAL_DOCS].pipelines) == 1
    assert [x.species_id for x in saida] == ["sp_verde", "sp_amarela", "sp_cinza"]


def test_hibrido_sem_a_secao_no_ranking(consulta, ranking, vocab_regras):
    sem = ranking.model_copy(update={"hibrido": None})
    with pytest.raises(ValueError, match="seção `hibrido`"):
        buscar(
            _banco_hibrido(), consulta(cores=["amarelo"]), [0.1], sem, vocab_regras[1], "hibrido"
        )


def test_hibrido_traz_as_especies_pelos_atributos(consulta, ranking, vocab_regras):
    db = _banco_hibrido()
    pelos = ranking.model_copy(
        update={"hibrido": ranking.hibrido.model_copy(update={"atributos": 5})}
    )
    # O universo (distinct dos documentos) passa a ter a sp_rara.
    docs = db[SPECIES_RETRIEVAL_DOCS]
    docs.docs = [*docs.docs, {"species_id": "sp_rara", "dominio": "aparencia", "nota": 0.6}]
    c = consulta(marcas=["sobrancelha"])
    c = c.model_copy(update={"recusadas": ["sp_amarela"]})
    saida = buscar(db, c, [0.1], pelos, vocab_regras[1], modo="hibrido")

    filtros = [_filtro(p) for p in db[SPECIES_RETRIEVAL_DOCS].pipelines]
    # A última busca pede ao vetor só a nota das espécies que pontuam nos atributos,
    # sem as recusadas: sp_rara tem sobrancelha; sp_amarela também, mas foi recusada.
    assert filtros[-1] == {"species_id": {"$in": ["sp_rara"]}}
    rara = next(x for x in saida if x.species_id == "sp_rara")
    assert rara.origem in ("completo", "atributos")


def test_hibrido_sem_atributos_nao_carrega_o_universo(consulta, ranking, vocab_regras):
    db = _banco_hibrido()
    pelos = ranking.model_copy(
        update={"hibrido": ranking.hibrido.model_copy(update={"atributos": 5})}
    )
    buscar(db, consulta(), [0.1], pelos, vocab_regras[1], modo="hibrido")
    assert len(db[SPECIES_RETRIEVAL_DOCS].pipelines) == 1
