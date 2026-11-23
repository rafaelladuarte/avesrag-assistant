"""Leitura e validação do conjunto de avaliação."""

import pytest
import yaml

from avesrag_assistant.avaliacao.casos import CasosInvalidos, carregar_casos, conferir_no_banco
from avesrag_assistant.settings import ROOT_DIR
from conftest import DOCS, FICHAS, BancoFalso

CASO = {
    "id": "c001",
    "descricao": "barriga amarela",
    "esperada": "sp_amarela",
    "autoria": "teste",
    "origem": "memoria",
    "uso": "calibracao",
    "atributos": {"cores": ["amarelo"]},
}


def _arquivo(tmp_path, casos, texto=None):
    path = tmp_path / "casos.yaml"
    path.write_text(
        texto if texto is not None else yaml.safe_dump({"versao": "1.0", "casos": casos}),
        encoding="utf-8",
    )
    return path


def test_casos_reais_carregam(vocab_regras):
    casos = carregar_casos(ROOT_DIR / "avaliacao" / "casos.yaml", *vocab_regras)
    assert len(casos) >= 30
    assert {c.uso for c in casos} == {"calibracao", "teste"}


def test_caso_sem_atributos(tmp_path, vocab_regras):
    sem = {k: v for k, v in CASO.items() if k != "atributos"}
    (caso,) = carregar_casos(_arquivo(tmp_path, [sem]), *vocab_regras)
    assert caso.atributos.informados() == []


@pytest.mark.parametrize(
    ("casos", "erro"),
    [
        ([CASO, CASO], "'c001' repetido"),
        ([{**CASO, "atributos": {"cores": ["amarela"]}}], "casos.0.atributos"),
        ([{**CASO, "origem": "sonho"}], "casos.0.origem"),
        ([{**CASO, "uso": "treino"}], "casos.0.uso"),
        ([{**CASO, "id": "caso1"}], "casos.0.id"),
        ([{**CASO, "esperada": "Pitangus"}], "casos.0.esperada"),
        ([{**CASO, "extra": 1}], "casos.0.extra"),
        ([CASO, {**CASO, "id": "c002", "uso": "teste"}], "sp_amarela: a mesma espécie"),
        ([], "casos"),
    ],
)
def test_casos_invalidos_sao_recusados_com_o_caminho(tmp_path, vocab_regras, casos, erro):
    with pytest.raises(CasosInvalidos, match=erro):
        carregar_casos(_arquivo(tmp_path, casos), *vocab_regras)


def test_chave_duplicada_e_recusada(tmp_path, vocab_regras):
    texto = yaml.safe_dump({"versao": "1.0", "casos": [CASO]}) + "versao: '2.0'\n"
    with pytest.raises(CasosInvalidos, match="chave duplicada"):
        carregar_casos(_arquivo(tmp_path, None, texto), *vocab_regras)


def test_conferir_no_banco(tmp_path, vocab_regras):
    casos = carregar_casos(
        _arquivo(
            tmp_path,
            [
                CASO,
                {**CASO, "id": "c002", "esperada": "sp_incompleta"},
                {**CASO, "id": "c003", "esperada": "sp_inexistente"},
            ],
        ),
        *vocab_regras,
    )
    db = BancoFalso(DOCS, FICHAS, registry={"sp_amarela", "sp_incompleta"})
    conf = conferir_no_banco(db, casos)
    assert conf.inexistentes == ["sp_inexistente"]
    assert conf.inalcancaveis == frozenset({"sp_incompleta"})
