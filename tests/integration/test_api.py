"""A API contra o MongoDB do builder: o TTL das sessões e a ficha pública de uma espécie real."""

import pytest
from pymongo.errors import PyMongoError

from avesrag_assistant import db as conexao
from avesrag_assistant.api import especies
from avesrag_assistant.api.guardrails import conferir_especie
from avesrag_assistant.api.sessoes import CONVERSATIONS, RepositorioMongo
from avesrag_assistant.conversa.estado import EstadoConversa, Mensagem

pytestmark = pytest.mark.integration

BANCO_DE_TESTE = "avesrag_teste_api"


@pytest.fixture(scope="module")
def banco():
    conexao.close()
    try:
        db = conexao.get_db()
        db.command("ping")
    except PyMongoError as e:
        pytest.skip(f"MongoDB do builder indisponível em MONGODB_URI: {e}")
    yield db
    conexao.close()


@pytest.fixture
def banco_de_teste(banco):
    teste = banco.client[BANCO_DE_TESTE]
    teste.drop_collection(CONVERSATIONS)
    yield teste
    banco.client.drop_database(BANCO_DE_TESTE)


def test_ttl_e_ida_e_volta_no_mongo(banco_de_teste):
    repo = RepositorioMongo(banco_de_teste, ttl_dias=14)
    repo.garantir_indices()
    repo.garantir_indices()  # idempotente
    indices = banco_de_teste[CONVERSATIONS].index_information()
    assert indices["ttl_expira_em"]["expireAfterSeconds"] == 0

    e = EstadoConversa(id="abc123", convite="ana", chamadas_llm=2)
    e.mensagens.append(Mensagem(papel="usuario", texto="oi"))
    repo.salvar(e)
    lido = repo.carregar("abc123")
    assert (lido.convite, lido.chamadas_llm, lido.mensagens[0].texto) == ("ana", 2, "oi")
    assert repo.ok()
    with pytest.raises(FileNotFoundError):
        repo.carregar("outra")


def test_ficha_publica_de_uma_especie_real(banco):
    ficha = especies.ficha_publica(banco, "sp_pitangus_sulphuratus")
    assert ficha is not None
    conferir_especie(ficha)
    assert ficha["nome"] == "bem-te-vi" and ficha["familia"] == "Tyrannidae"
    assert ficha["foto"]["url"].startswith("https://") and ficha["foto"]["credito"]
    assert ficha["ilustracao"]["rotulo"].startswith("Ilustração gerada por IA")
    assert ficha["url_wikiaves"]
    assert ficha["ficha"] and ficha["como_reconhecer"]
    assert "evidencia" not in str(ficha).lower()
    itens = {i["rotulo"]: i["valor"] for i in ficha["ficha"]}
    assert itens["Dieta"] == "Onívora" and itens["Peso"].endswith(" g")
    assert itens["Conservação"] == "Pouco preocupante" and itens["Ordem"] == "Passeriformes"
    assert ficha["nome_en"] == "Great Kiskadee" and 2 <= len(ficha["fotos"]) <= 4
    fotos = especies.fotos(banco, ["sp_pitangus_sulphuratus", "sp_nao_existe"])
    assert set(fotos) == {"sp_pitangus_sulphuratus"}
    assert especies.ficha_publica(banco, "sp_nao_existe") is None


def test_catalogo_real_tem_as_387_aptas(banco):
    from avesrag_assistant.api.config import carregar_familias
    from avesrag_assistant.settings import ROOT_DIR

    familias = carregar_familias(ROOT_DIR / "config" / "familias.yaml")
    itens = especies.catalogo(banco, familias)
    assert len(itens) == 387
    assert all(i["foto"] for i in itens) and all(i["familia"] for i in itens)
    sem_nome_popular = {i["familia"] for i in itens if i["familia_popular"] == i["familia"]}
    assert sem_nome_popular == set(), (
        f"famílias sem nome popular em familias.yaml: {sem_nome_popular}"
    )
    assert [i["nome"] for i in itens] == sorted((i["nome"] for i in itens), key=str.casefold)
