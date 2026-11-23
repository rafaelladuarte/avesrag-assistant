"""Cache dos embeddings das consultas: a segunda vez não chama o fornecedor."""

import pytest
from avesrag_builder.llm.base import Embeddings

from avesrag_assistant.busca.cache import CacheEmbeddings, criar_cache
from avesrag_assistant.settings import MissingSettingError, get_settings


class Falso:
    nome = "falso"

    def __init__(self):
        self.pedidos: list[list[str]] = []

    def embeddings(self, textos, modelo):
        self.pedidos.append(list(textos))
        return Embeddings(
            vetores=[[float(len(t)), float(len(modelo))] for t in textos], tokens=len(textos)
        )


@pytest.fixture
def falso():
    return Falso()


def test_segundo_pedido_nao_chama_o_fornecedor(tmp_path, falso):
    cache = CacheEmbeddings(tmp_path / "c.json", falso, "m")
    assert cache.vetor("bico enorme") == [11.0, 1.0]
    assert cache.vetor("  bico enorme ") == [11.0, 1.0]
    assert falso.pedidos == [["bico enorme"]]
    assert (cache.chamadas, cache.tokens) == (1, 1)


def test_lote_misto_so_manda_os_ausentes(tmp_path, falso):
    cache = CacheEmbeddings(tmp_path / "c.json", falso, "m")
    cache.vetor("a")
    vetores = cache.vetores(["a", "bb", "ccc", "bb"])
    assert [v[0] for v in vetores] == [1.0, 2.0, 3.0, 2.0]
    assert falso.pedidos == [["a"], ["bb", "ccc"]]


def test_arquivo_e_lido_por_outra_instancia(tmp_path, falso):
    CacheEmbeddings(tmp_path / "c.json", falso, "m").vetor("a")
    outro = Falso()
    assert CacheEmbeddings(tmp_path / "c.json", outro, "m").vetor("a") == [1.0, 1.0]
    assert outro.pedidos == []
    assert not (tmp_path / "c.json.tmp").exists()


def test_modelos_diferentes_nao_se_misturam(tmp_path, falso):
    CacheEmbeddings(tmp_path / "c.json", falso, "m").vetor("a")
    assert CacheEmbeddings(tmp_path / "c.json", falso, "modelo2").vetor("a") == [1.0, 7.0]
    assert len(falso.pedidos) == 2


def test_criar_cache_exige_a_chave(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(MissingSettingError, match="OPENAI_API_KEY"):
        criar_cache(get_settings())


def test_criar_cache_usa_reports_e_o_modelo(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    cache = criar_cache(get_settings())
    assert cache.path == tmp_path / "cache_embeddings.json"
    assert cache.modelo == get_settings().EMBEDDING_MODEL
