"""Uma conversa inteira contra o MongoDB do builder, com o LLM falso (só leitura).

O embedding é o do documento de aparência do bem-te-vi (nenhuma chamada paga), e
o "usuário" responde cada pergunta pela ficha do bem-te-vi, como na simulação.
"""

import random

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS
from pymongo.errors import PyMongoError

from avesrag_assistant import db as conexao
from avesrag_assistant.avaliacao.simulacao import responder
from avesrag_assistant.busca.modelos import validar_atributos
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.busca.retriever import carregar_fichas
from avesrag_assistant.conversa.config import carregar_conversa
from avesrag_assistant.conversa.estado import EstadoConversa
from avesrag_assistant.conversa.orquestrador import Orquestrador, Servicos
from avesrag_assistant.conversa.perguntas import Pergunta, aplicar_resposta
from avesrag_assistant.llm.extrator import Extracao
from avesrag_assistant.settings import ROOT_DIR

pytestmark = pytest.mark.integration

BEM_TE_VI = "sp_pitangus_sulphuratus"


@pytest.fixture(scope="module")
def banco():
    conexao.close()
    try:
        db = conexao.get_db()
        db[SPECIES_RETRIEVAL_DOCS].find_one({}, {"_id": 1})
    except PyMongoError as e:
        pytest.skip(f"MongoDB do builder indisponível em MONGODB_URI: {e}")
    yield db
    conexao.close()


class UsuarioFalso:
    """Extrator falso: a descrição inicial e, depois, as respostas pela ficha."""

    def __init__(self, ficha, vocab_regras, conversa):
        self.ficha = ficha
        self.vocab_regras = vocab_regras
        self.conversa = conversa
        self.pendente: str | None = None  # a chave da pergunta, posta pelo teste
        self.rng = random.Random(0)

    def extrair(self, mensagem, atributos=None, descricao=None, pergunta=None):
        if self.pendente is None:
            atr = validar_atributos(
                {"cores": ["amarelo"], "marcas": ["sobrancelha"]}, *self.vocab_regras
            )
        else:
            atributo, _, parte = self.pendente.partition(":")
            p = Pergunta(atributo, parte or None)
            valores, _ = responder(
                p, self.ficha, self.vocab_regras[1], self.conversa.simulacao, self.rng
            )
            atr = aplicar_resposta(atributos, p, valores, *self.vocab_regras)
        return Extracao(False, None, None, atr, mensagem)


class RedatorFalso:
    def redigir(self, pedido, atributos):
        return f"Você lembra {pedido}?"


class SugestorFalso:
    def especies(self, mensagem, indice):
        return [BEM_TE_VI]


def test_conversa_ate_confirmar_o_bem_te_vi(banco, vocab_regras):
    doc = banco[SPECIES_RETRIEVAL_DOCS].find_one({"_id": f"{BEM_TE_VI}:aparencia"})
    if doc is None:
        pytest.skip("a base de busca não tem o documento de aparência do bem-te-vi")
    conversa = carregar_conversa(ROOT_DIR / "config" / "conversa.yaml", vocab_regras[0])
    ficha = carregar_fichas(banco, [BEM_TE_VI])[BEM_TE_VI].atributos
    usuario = UsuarioFalso(ficha, vocab_regras, conversa)
    servicos = Servicos(
        db=banco,
        ranking=carregar_ranking(ROOT_DIR / "config" / "ranking.yaml"),
        conversa=conversa,
        vocabulario=vocab_regras[0],
        regras_bico=vocab_regras[1],
        extrator=usuario,
        redator=RedatorFalso(),
        vetor=lambda texto: doc["embedding"],
        sugestor=SugestorFalso(),
        indice={},
    )
    o = Orquestrador(servicos, EstadoConversa(id="integracao"))

    o.processar("passarinho amarelo com uma listra branca na cabeça")
    for _ in range(conversa.perguntas.maximo):
        if o.estado.etapa != "coleta":
            break
        usuario.pendente = o.estado.pergunta
        o.processar("resposta")
    assert o.estado.etapa == "confirmacao"
    ids = [c.species_id for c in o.estado.candidatas]
    assert BEM_TE_VI in ids
    o.processar(str(ids.index(BEM_TE_VI) + 1))
    assert (o.estado.etapa, o.estado.confirmada) == ("fim", BEM_TE_VI)
