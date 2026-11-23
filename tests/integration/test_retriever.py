"""Retriever contra o MongoDB do builder (só leitura).

Usa o embedding de um documento real como consulta: nenhuma chamada paga.
"""

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS
from pymongo.errors import PyMongoError

from avesrag_assistant import db as conexao
from avesrag_assistant.busca.modelos import validar_consulta
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.busca.retriever import MODOS, buscar
from avesrag_assistant.settings import ROOT_DIR

pytestmark = pytest.mark.integration

BEM_TE_VI = "sp_pitangus_sulphuratus"


@pytest.fixture(scope="module")
def banco():
    conexao.close()
    try:
        db = conexao.get_db()
        # Uma leitura, e não só o ping: o ping passa até num Mongo que exige autenticação.
        db[SPECIES_RETRIEVAL_DOCS].find_one({}, {"_id": 1})
    except PyMongoError as e:
        pytest.skip(f"MongoDB do builder indisponível em MONGODB_URI: {e}")
    yield db
    conexao.close()


@pytest.fixture(scope="module")
def vetor_bem_te_vi(banco):
    doc = banco[SPECIES_RETRIEVAL_DOCS].find_one({"_id": f"{BEM_TE_VI}:aparencia"})
    if doc is None:
        pytest.skip("a base de busca não tem o documento de aparência do bem-te-vi")
    return doc["embedding"]


@pytest.fixture(scope="module")
def ranking():
    return carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")


@pytest.mark.parametrize("modo", MODOS)
def test_o_proprio_documento_traz_a_especie_em_primeiro(
    banco, vetor_bem_te_vi, ranking, vocab_regras, modo
):
    consulta = validar_consulta({"descricao": "bem-te-vi"}, *vocab_regras)
    saida = buscar(banco, consulta, vetor_bem_te_vi, ranking, vocab_regras[1], modo=modo)
    assert len(saida) == 10
    assert saida[0].species_id == BEM_TE_VI
    assert saida[0].dominio == "aparencia"
    assert all(c.nome and c.nome_cientifico for c in saida)


def test_recusada_some_do_resultado(banco, vetor_bem_te_vi, ranking, vocab_regras):
    consulta = validar_consulta({"descricao": "bem-te-vi", "recusadas": [BEM_TE_VI]}, *vocab_regras)
    saida = buscar(banco, consulta, vetor_bem_te_vi, ranking, vocab_regras[1])
    assert BEM_TE_VI not in [c.species_id for c in saida]
    assert len(saida) == 10
