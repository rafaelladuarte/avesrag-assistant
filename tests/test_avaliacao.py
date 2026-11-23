"""Rodar a avaliação e o relatório: posições, métricas, cache e TSV."""

import csv

import pytest
from avesrag_builder.llm.base import Embeddings

from avesrag_assistant.avaliacao.casos import Caso
from avesrag_assistant.avaliacao.relatorio import gravar_tsv, inalcancaveis, metricas, tabela
from avesrag_assistant.avaliacao.rodar import MODOS_AVALIACAO, rodar
from avesrag_assistant.busca.cache import CacheEmbeddings
from avesrag_assistant.busca.modelos import contexto
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.settings import ROOT_DIR
from conftest import DOCS, FICHAS, BancoFalso


class Embedder:
    nome = "falso"

    def __init__(self):
        self.pedidos = []

    def embeddings(self, textos, modelo):
        self.pedidos.append(list(textos))
        return Embeddings(vetores=[[0.1] for _ in textos], tokens=3 * len(textos))


@pytest.fixture(scope="module")
def ranking():
    return carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")


@pytest.fixture
def casos(vocab_regras):
    def caso(id, esperada, uso, origem="memoria", **atributos):
        return Caso.model_validate(
            {
                "id": id,
                "descricao": f"descrição {id}",
                "esperada": esperada,
                "autoria": "teste",
                "origem": origem,
                "uso": uso,
                "atributos": atributos,
            },
            context=contexto(*vocab_regras),
        )

    return [
        caso("c001", "sp_amarela", "calibracao", cores=["amarelo"]),
        caso("c002", "sp_cinza", "teste", origem="llm"),
        caso("c003", "sp_incompleta", "teste"),
    ]


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------


def test_metricas():
    m = metricas([1, 2, 5, None])
    assert m.n == 4
    assert m.tops == {1: 0.25, 3: 0.5, 10: 0.75}
    assert m.mediana == 3.5


def test_metricas_mediana_fora_das_posicoes_medidas():
    assert metricas([1, None, None]).mediana is None


def test_metricas_sem_casos():
    m = metricas([])
    assert (m.n, m.mediana, m.tops[1]) == (0, None, 0.0)


# ---------------------------------------------------------------------------
# rodar
# ---------------------------------------------------------------------------


def test_rodar_posicoes_por_modo_e_uma_chamada_de_embedding(tmp_path, casos, ranking, vocab_regras):
    embedder = Embedder()
    cache = CacheEmbeddings(tmp_path / "c.json", embedder, "m")
    db = BancoFalso(DOCS, FICHAS)
    av = rodar(db, casos, cache, ranking, vocab_regras[1], inalcancaveis={"sp_incompleta"})

    assert av.modos == MODOS_AVALIACAO
    assert (av.chamadas, av.tokens) == (1, 9)
    assert embedder.pedidos == [["descrição c001", "descrição c002", "descrição c003"]]
    c001, c002, c003 = av.resultados
    assert c001.posicoes == {
        "vetor": 2,
        "filtros_duros": 2,
        "reordenado": 1,
        "hibrido": 1,
        "degradado": 2,
    }
    assert c001.primeiras["reordenado"][0] == "sp_amarela"
    assert c002.posicoes["vetor"] == 3
    assert not c003.alcancavel and c003.posicoes["vetor"] is None

    # A segunda avaliação sai toda do cache.
    av2 = rodar(db, casos, cache, ranking, vocab_regras[1])
    assert (av2.chamadas, av2.tokens) == (0, 0)
    assert len(embedder.pedidos) == 1


def test_rodar_so_degradado_dispensa_o_cache(casos, ranking, vocab_regras):
    av = rodar(BancoFalso(DOCS, FICHAS), casos, None, ranking, vocab_regras[1], ["degradado"])
    assert av.chamadas == 0
    assert av.resultados[0].posicoes == {"degradado": 2}


def test_rodar_modo_desconhecido(casos, ranking, vocab_regras):
    with pytest.raises(ValueError, match="modos desconhecidos"):
        rodar(BancoFalso(DOCS, FICHAS), casos, None, ranking, vocab_regras[1], ["outro"])


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------


@pytest.fixture
def avaliacao(tmp_path, casos, ranking, vocab_regras):
    cache = CacheEmbeddings(tmp_path / "c.json", Embedder(), "m")
    return rodar(
        BancoFalso(DOCS, FICHAS),
        casos,
        cache,
        ranking,
        vocab_regras[1],
        ["vetor", "reordenado"],
        inalcancaveis={"sp_incompleta"},
    )


def test_tabela(avaliacao):
    linhas = tabela(avaliacao)
    assert linhas[0].split() == ["modo", "grupo", "n", "top1", "top3", "top10", "mediana"]
    por_chave = {tuple(linha.split()[:2]): linha.split()[2:] for linha in linhas[1:]}
    assert len(por_chave) == 2 * 4
    assert por_chave[("reordenado", "calibracao")] == ["1", "100%", "100%", "100%", "1"]
    assert por_chave[("vetor", "alcancaveis")] == ["2", "0%", "100%", "100%", "2.5"]
    assert por_chave[("vetor", "sem_llm")] == ["1", "0%", "100%", "100%", "2"]
    assert por_chave[("vetor", "teste")][0] == "1"  # o inalcançável fica de fora


def test_inalcancaveis(avaliacao):
    assert inalcancaveis(avaliacao) == ["c003 sp_incompleta"]


def test_gravar_tsv(tmp_path, avaliacao):
    path = gravar_tsv(avaliacao, tmp_path / "sub" / "avaliacao.tsv")
    with path.open(encoding="utf-8") as fh:
        linhas = list(csv.reader(fh, delimiter="\t"))
    assert linhas[0] == [
        "caso",
        "esperada",
        "uso",
        "origem",
        "alcancavel",
        "pos_vetor",
        "pos_reordenado",
        "primeiras_vetor",
        "primeiras_reordenado",
    ]
    assert linhas[1][:7] == ["c001", "sp_amarela", "calibracao", "memoria", "sim", "2", "1"]
    assert linhas[3][4:7] == ["nao", ">40", ">40"]
    assert linhas[1][8].split()[0] == "sp_amarela"
