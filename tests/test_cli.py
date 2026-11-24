import json

import pytest
from avesrag_builder.db.collections import SPECIES_REGISTRY, SPECIES_RETRIEVAL_DOCS
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


# ---------------------------------------------------------------------------
# avaliar
# ---------------------------------------------------------------------------

CASOS_YAML = """
versao: "1.0"
casos:
  - { id: c001, descricao: "ave amarela", esperada: sp_amarela, autoria: t, origem: memoria,
      uso: calibracao, atributos: { cores: [amarelo] } }
  - { id: c002, descricao: "ave cinza", esperada: sp_cinza, autoria: t, origem: llm, uso: teste }
  - { id: c003, descricao: "ave rara", esperada: sp_incompleta, autoria: t, origem: memoria,
      uso: teste }
"""


@pytest.fixture
def avaliacao_falsa(ambiente, monkeypatch, tmp_path):
    banco, embedder = ambiente
    (tmp_path / "casos.yaml").write_text(CASOS_YAML, encoding="utf-8")
    monkeypatch.setenv("AVALIACAO_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    banco.colecoes[SPECIES_REGISTRY].docs.append({"species_id": "sp_incompleta"})
    return tmp_path, embedder


def test_avaliar(avaliacao_falsa):
    tmp_path, embedder = avaliacao_falsa
    resultado = runner.invoke(app, ["avaliar"])
    assert resultado.exit_code == 0, resultado.output
    linhas = resultado.output.splitlines()
    assert linhas[0].startswith(
        "3 casos · ranking 1.4 · sugestões não · posições até 40 · embedding: 1 chamada(s)"
    )
    assert linhas[1].split()[:3] == ["modo", "grupo", "n"]
    assert len(linhas) == 2 + 5 * 4 + 2  # 5 modos × 4 grupos
    assert "inalcançáveis (sem documento de busca): c003 sp_incompleta" in resultado.output
    assert (tmp_path / "reports" / "avaliacao.tsv").exists()
    assert len(embedder.textos) == 3

    de_novo = runner.invoke(app, ["avaliar", "--uso", "teste", "--modo", "vetor"])
    assert de_novo.exit_code == 0, de_novo.output
    assert de_novo.output.startswith("2 casos")
    assert "embedding: 0 chamada(s), 0 tokens" in de_novo.output
    assert len(embedder.textos) == 3


def test_avaliar_so_degradado_nao_pede_a_chave(avaliacao_falsa, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    resultado = runner.invoke(app, ["avaliar", "--modo", "degradado"])
    assert resultado.exit_code == 0, resultado.output


@pytest.mark.parametrize(
    ("args", "mensagem"),
    [
        (["--modo", "outro"], "modos desconhecidos"),
        (["--uso", "treino"], "uso desconhecido"),
    ],
)
def test_avaliar_opcao_invalida(avaliacao_falsa, args, mensagem):
    resultado = runner.invoke(app, ["avaliar", *args])
    assert resultado.exit_code == 2
    assert mensagem in resultado.output


def test_avaliar_esperada_fora_do_registry(avaliacao_falsa):
    tmp_path, _ = avaliacao_falsa
    texto = CASOS_YAML.replace("sp_incompleta", "sp_inexistente")
    (tmp_path / "casos.yaml").write_text(texto, encoding="utf-8")
    resultado = runner.invoke(app, ["avaliar"])
    assert resultado.exit_code == 2
    assert "fora do registry: sp_inexistente" in resultado.output


def test_avaliar_casos_invalidos(avaliacao_falsa):
    tmp_path, _ = avaliacao_falsa
    (tmp_path / "casos.yaml").write_text(CASOS_YAML.replace("memoria", "sonho", 1), "utf-8")
    resultado = runner.invoke(app, ["avaliar"])
    assert resultado.exit_code == 2
    assert "casos.0.origem" in resultado.output


class SugestorFalso:
    """Sugere sempre a sp_cinza, a última no vetor."""

    def __init__(self):
        self.mensagens = []
        self.cache = type("C", (), {"chamadas": 1, "tokens_entrada": 10, "tokens_saida": 5})()

    def especies(self, mensagem, indice):
        self.mensagens.append(mensagem)
        return ["sp_cinza"]


@pytest.fixture
def sugestor(monkeypatch):
    falso = SugestorFalso()
    monkeypatch.setattr(cli, "criar_sugestor", lambda settings, voc, regras: falso)
    return falso


def test_buscar_com_sugestoes(ambiente, sugestor):
    resultado = runner.invoke(app, ["buscar", "ave cinza", "--sugestoes"])
    assert resultado.exit_code == 0, resultado.output
    linhas = resultado.output.splitlines()
    assert "sugeridas pelo LLM: sp_cinza" in linhas
    assert "Ave cinza" in linhas[2] and "+ sugerida +2.00" in linhas[2]  # sobe para o 1º
    assert sugestor.mensagens == ["ave cinza"]


def test_avaliar_com_sugestoes(avaliacao_falsa, sugestor):
    resultado = runner.invoke(app, ["avaliar", "--sugestoes", "--modo", "hibrido"])
    assert resultado.exit_code == 0, resultado.output
    assert "sugestões: 1 chamada(s) ao LLM, 10 + 5 tokens" in resultado.output
    assert "sugestões sim" in resultado.output
    assert len(sugestor.mensagens) == 3
