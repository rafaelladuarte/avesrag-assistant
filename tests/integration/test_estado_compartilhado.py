"""Caches, limites e uso diário no MongoDB (para várias instâncias, como na Vercel)."""

import pytest
from pymongo.errors import PyMongoError

from avesrag_assistant import db as conexao
from avesrag_assistant.api.limites_mongo import LimitadorMongo, UsoDiarioMongo
from avesrag_assistant.armazem import ArmazemMongo

pytestmark = pytest.mark.integration

BANCO_DE_TESTE = "avesrag_teste_estado"


@pytest.fixture
def banco():
    conexao.close()
    try:
        cliente = conexao.get_db().client
        cliente.admin.command("ping")
    except PyMongoError as e:
        pytest.skip(f"MongoDB do builder indisponível em MONGODB_URI: {e}")
    cliente.drop_database(BANCO_DE_TESTE)
    yield cliente[BANCO_DE_TESTE]
    cliente.drop_database(BANCO_DE_TESTE)
    conexao.close()


def test_armazem_mongo_guarda_e_troca(banco):
    a = ArmazemMongo(banco, "cache")
    assert a.obter(["x"]) == {}
    a.gravar({"x": "1", "y": [0.5, 1.5]})
    a.gravar({"x": "2"})
    assert a.obter(["x", "y", "z"]) == {"x": "2", "y": [0.5, 1.5]}
    assert ArmazemMongo(banco, "cache").obter(["x"]) == {"x": "2"}  # outra instância vê


def test_limitador_por_ip_vale_para_todas_as_instancias(banco):
    t = [1000.0]
    a = LimitadorMongo(banco, 2, relogio=lambda: t[0])
    b = LimitadorMongo(banco, 2, relogio=lambda: t[0])
    assert a.permitir_ip("1.1.1.1") and b.permitir_ip("1.1.1.1")
    assert not a.permitir_ip("1.1.1.1")  # a terceira, em qualquer instância
    assert a.permitir_ip("2.2.2.2")
    t[0] += 60  # outro minuto
    assert b.permitir_ip("1.1.1.1")


def test_limite_do_dia_atomico_e_sem_contar_o_excedente(banco):
    a = LimitadorMongo(banco, 100, dia=lambda: "2026-11-27")
    b = LimitadorMongo(banco, 100, dia=lambda: "2026-11-27")
    assert [a.consumir_dia("llm", 3), b.consumir_dia("llm", 3), a.consumir_dia("llm", 3)] == [
        True,
        True,
        True,
    ]
    assert b.consumir_dia("llm", 3) is False
    assert a.usado_hoje("llm") == 3
    assert a.consumir_dia("outro", 0) is False
    amanha = LimitadorMongo(banco, 100, dia=lambda: "2026-11-28")
    assert amanha.consumir_dia("llm", 3) is True


def test_uso_diario_soma_entre_instancias(banco):
    a = UsoDiarioMongo(banco, dia=lambda: "2026-11-27")
    b = UsoDiarioMongo(banco, dia=lambda: "2026-11-27")
    assert a.hoje() == {"sessoes": 0, "mensagens": 0, "chamadas_llm": 0, "chamadas_embedding": 0}
    a.registrar(sessoes=1, mensagens=2)
    b.registrar(mensagens=1, chamadas_llm=3)
    assert a.hoje() == {"sessoes": 1, "mensagens": 3, "chamadas_llm": 3, "chamadas_embedding": 0}
