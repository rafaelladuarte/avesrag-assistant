"""Configuração do armazenamento e dos convites vindos do ambiente."""

import json

import pytest

from avesrag_assistant.api.convites import ConviteInvalido, Convites
from avesrag_assistant.api.fabrica import SemConvites, convites_do_ambiente
from avesrag_assistant.armazem import ArmazemArquivo
from avesrag_assistant.settings import Settings


def _settings(monkeypatch, **env):
    for k in ("VERCEL", "ARMAZENAMENTO", "CURADORIA_HABILITADA", "CONVITES_JSON"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_armazenamento_padrao_local_e_vercel(monkeypatch):
    s = _settings(monkeypatch)
    assert (s.armazenamento, s.curadoria_habilitada) == ("arquivo", True)
    s = _settings(monkeypatch, VERCEL="1")
    assert (s.armazenamento, s.curadoria_habilitada) == ("mongo", False)
    s = _settings(monkeypatch, VERCEL="1", ARMAZENAMENTO="arquivo", CURADORIA_HABILITADA="true")
    assert (s.armazenamento, s.curadoria_habilitada) == ("arquivo", True)


def test_convites_do_ambiente_so_leitura(monkeypatch, tmp_path):
    dados = {
        "ana": {"hash": "h", "criado_em": "2026-11-27", "papel": "curadoria", "revogado": False}
    }
    s = _settings(
        monkeypatch, CONVITES_JSON=json.dumps(dados), CONVITES_PATH=str(tmp_path / "x.json")
    )
    convites = convites_do_ambiente(s)
    assert convites.papel_de("ana") == "curadoria"
    with pytest.raises(ConviteInvalido, match="só leitura"):
        convites.criar("beto")
    assert not (tmp_path / "x.json").exists()


def test_sem_convites_recusa_subir(monkeypatch, tmp_path):
    s = _settings(monkeypatch, CONVITES_PATH=str(tmp_path / "nao_existe.json"))
    with pytest.raises(SemConvites):
        convites_do_ambiente(s)


def test_convites_de_arquivo_continuam(tmp_path):
    c = Convites(tmp_path / "c.json")
    token = c.criar("rafa")
    assert c.validar(token) == "rafa"


def test_armazem_arquivo_guarda_e_relê(tmp_path):
    a = ArmazemArquivo(tmp_path / "c.json")
    a.gravar({"a": "1"})
    assert ArmazemArquivo(tmp_path / "c.json").obter(["a", "b"]) == {"a": "1"}
