"""A dependência do builder por caminho: os YAML dele carregam pelo `BUILDER_DIR`."""

from avesrag_assistant import vocabulario
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.settings import ROOT_DIR, get_settings


def test_carrega_vocabulario_e_regras_bico_do_builder():
    voc, regras = vocabulario.carregar()
    assert voc is not None
    assert regras is not None


def test_usa_o_builder_dir_do_assistant(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILDER_DIR", str(tmp_path))
    assert get_settings().builder_config_dir == tmp_path / "config"


def test_ranking_do_assistant_e_valido():
    # O casos.yaml real é conferido em tests/test_casos.py.
    ranking = carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")
    assert ranking.versao == "1.1"
    assert [n.nome for n in ranking.hibrido.niveis] == ["completo", "aparencia", "cores"]
