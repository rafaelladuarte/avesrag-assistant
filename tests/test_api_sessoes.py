"""O repositório de sessões nas duas implementações (plano_assistant4.md §6)."""

from datetime import timedelta

import pytest

from avesrag_assistant.api.sessoes import RepositorioArquivo, RepositorioMongo
from avesrag_assistant.conversa.estado import EstadoConversa, Mensagem


class ColecaoMongoFalsa:
    def __init__(self):
        self.docs = {}
        self.indices = []
        self.database = self

    def create_index(self, campo, **kwargs):
        self.indices.append((campo, kwargs))

    def find_one(self, filtro):
        d = self.docs.get(filtro["_id"])
        return dict(d) if d else None

    def replace_one(self, filtro, doc, upsert=False):
        self.docs[filtro["_id"]] = dict(doc)

    def find(self, filtro):
        return [dict(d) for d in self.docs.values()]

    @property
    def client(self):
        return self

    @property
    def admin(self):
        return self

    def command(self, nome):
        return {"ok": 1}


class BancoMongoFalso:
    def __init__(self):
        self.colecao = ColecaoMongoFalsa()

    def __getitem__(self, nome):
        assert nome == "conversations"
        return self.colecao


def _estado():
    e = EstadoConversa(id="abc123", convite="ana", chamadas_llm=3)
    e.mensagens.append(Mensagem(papel="usuario", texto="oi"))
    return e


@pytest.fixture(params=["arquivo", "mongo"])
def repo(request, tmp_path):
    if request.param == "arquivo":
        return RepositorioArquivo(tmp_path)
    return RepositorioMongo(BancoMongoFalso(), ttl_dias=14)


def test_salva_e_relê(repo):
    repo.salvar(_estado())
    e = repo.carregar("abc123")
    assert (e.id, e.convite, e.chamadas_llm) == ("abc123", "ana", 3)
    assert e.mensagens[0].texto == "oi"
    assert repo.ok()


def test_sessao_que_nao_existe(repo):
    with pytest.raises(FileNotFoundError):
        repo.carregar("naoexiste")


def test_mongo_grava_expira_em_e_cria_o_ttl():
    db = BancoMongoFalso()
    repo = RepositorioMongo(db, ttl_dias=14)
    repo.garantir_indices()
    assert db.colecao.indices == [("expira_em", {"expireAfterSeconds": 0, "name": "ttl_expira_em"})]
    e = _estado()
    repo.salvar(e)
    doc = db.colecao.docs["abc123"]
    assert doc["expira_em"] - e.atualizada_em == timedelta(days=14)
    assert doc["_id"] == "abc123"


def test_todas_lista_as_sessoes_e_pula_as_ilegiveis(repo, tmp_path):
    repo.salvar(EstadoConversa(id="um111", convite="ana"))
    repo.salvar(EstadoConversa(id="dois2", convite="bia"))
    if isinstance(repo, RepositorioArquivo):
        (tmp_path / "estragada.json").write_text('{"id": "x", "campo_novo": 1}', encoding="utf-8")
    else:
        repo.colecao.docs["estragada"] = {"_id": "estragada", "id": "x", "campo_novo": 1}
    assert sorted(e.id for e in repo.todas()) == ["dois2", "um111"]


def test_todas_sem_pasta(tmp_path):
    assert RepositorioArquivo(tmp_path / "nao_existe").todas() == []
