import json

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS
from avesrag_builder.llm.base import Embeddings
from typer.testing import CliRunner

from avesrag_assistant import __version__, cli
from avesrag_assistant.busca.cache import CacheEmbeddings
from avesrag_assistant.cli import app
from conftest import DOCS, FICHAS, BancoFalso

runner = CliRunner()


def test_help_lista_os_comandos():
    resultado = runner.invoke(app, ["--help"])
    assert resultado.exit_code == 0
    assert "buscar" in resultado.output
    assert "avaliar" in resultado.output


def test_version():
    resultado = runner.invoke(app, ["--version"])
    assert resultado.exit_code == 0
    assert __version__ in resultado.output


def test_avaliar_ainda_nao_implementado():
    resultado = runner.invoke(app, ["avaliar"])
    assert resultado.exit_code == 2
    assert "ainda não foi implementado" in resultado.output


# ---------------------------------------------------------------------------
# buscar
# ---------------------------------------------------------------------------


class Embedder:
    nome = "falso"

    def __init__(self):
        self.textos = []

    def embeddings(self, textos, modelo):
        self.textos += textos
        return Embeddings(vetores=[[0.1] for _ in textos], tokens=7)


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    """CLI com banco falso e embedder falso; devolve o banco e o embedder."""
    banco = BancoFalso(DOCS, FICHAS)
    embedder = Embedder()
    monkeypatch.setattr(cli, "get_db", lambda: banco)
    monkeypatch.setattr(
        cli,
        "criar_cache",
        lambda settings: CacheEmbeddings(tmp_path / "cache.json", embedder, "m"),
    )
    return banco, embedder


def test_buscar_reordenado_mostra_o_que_bateu(ambiente):
    _, embedder = ambiente
    atributos = json.dumps({"cores": ["amarelo"], "marcas": ["sobrancelha"]})
    resultado = runner.invoke(app, ["buscar", "barriga amarela", "--atributos", atributos])
    assert resultado.exit_code == 0, resultado.output
    linhas = resultado.output.splitlines()
    assert linhas[0] == "modo reordenado · embedding novo (7 tokens) · atributos: cores, marcas"
    assert linhas[1].startswith(" 1. ave-amarela (Ave amarela)")
    assert "bate: cores amarelo; marcas sobrancelha" in resultado.output
    assert "conflita: cores amarelo" in resultado.output  # a ave verde
    assert embedder.textos == ["barriga amarela"]


def test_buscar_segunda_vez_usa_o_cache(ambiente):
    _, embedder = ambiente
    runner.invoke(app, ["buscar", "barriga amarela"])
    resultado = runner.invoke(app, ["buscar", "barriga amarela", "--modo", "vetor", "--k", "1"])
    assert resultado.exit_code == 0, resultado.output
    assert "embedding do cache" in resultado.output
    assert len(embedder.textos) == 1
    assert " 2." not in resultado.output


def test_buscar_degradado_nao_gera_embedding(ambiente):
    banco, embedder = ambiente
    resultado = runner.invoke(app, ["buscar", "bem-te-vi", "--modo", "degradado"])
    assert resultado.exit_code == 0, resultado.output
    assert "sem embedding" in resultado.output
    assert embedder.textos == []
    assert "$search" in banco[SPECIES_RETRIEVAL_DOCS].pipelines[0][0]


def test_buscar_recusar(ambiente):
    banco, _ = ambiente
    runner.invoke(app, ["buscar", "ave", "--recusar", "sp_verde", "--recusar", "sp_cinza"])
    etapa = banco[SPECIES_RETRIEVAL_DOCS].pipelines[0][0]["$vectorSearch"]
    assert etapa["filter"] == {"species_id": {"$nin": ["sp_verde", "sp_cinza"]}}


@pytest.mark.parametrize(
    ("args", "mensagem"),
    [
        (["--atributos", "{nao json"], "não é um JSON válido"),
        (["--atributos", '{"cores": ["amarela"]}'], "atributos: Value error, cores[0]"),
        (["--modo", "outro"], "modo desconhecido"),
        (["--recusar", "Pitangus"], "recusadas.0"),
    ],
)
def test_buscar_entrada_invalida(ambiente, args, mensagem):
    resultado = runner.invoke(app, ["buscar", "ave", *args])
    assert resultado.exit_code == 2
    assert mensagem in resultado.output


def test_buscar_sem_chave_de_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(cli, "get_db", lambda: BancoFalso(DOCS, FICHAS))
    resultado = runner.invoke(app, ["buscar", "ave"])
    assert resultado.exit_code == 2
    assert "OPENAI_API_KEY" in resultado.output
