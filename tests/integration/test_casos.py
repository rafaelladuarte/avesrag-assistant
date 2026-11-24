"""O conjunto de avaliação contra o registry e a base de busca do builder (só leitura)."""

import pytest
from avesrag_builder.db.collections import SPECIES_REGISTRY
from pymongo.errors import PyMongoError

from avesrag_assistant import db as conexao
from avesrag_assistant.avaliacao.casos import carregar_casos, conferir_no_banco
from avesrag_assistant.settings import ROOT_DIR

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def banco():
    conexao.close()
    try:
        db = conexao.get_db()
        db[SPECIES_REGISTRY].find_one({}, {"_id": 1})
    except PyMongoError as e:
        pytest.skip(f"MongoDB do builder indisponível em MONGODB_URI: {e}")
    yield db
    conexao.close()


def test_casos_reais_contra_o_banco(banco, vocab_regras):
    casos = carregar_casos(ROOT_DIR / "avaliacao" / "casos.yaml", *vocab_regras)
    conf = conferir_no_banco(banco, casos)
    assert conf.inexistentes == []
    assert conf.inalcancaveis == frozenset({"sp_crotophaga_ani", "sp_megarynchus_pitangua"})
