"""Escolha da pergunta, conversa.yaml e usuário simulado (sem Mongo nem rede)."""

import random

import pytest
import yaml
from avesrag_builder.core.models import Atributos, CorPorParte

from avesrag_assistant.avaliacao.simulacao import (
    Simulado,
    linhas_tsv,
    responder,
    resumo_perguntas,
    tabela,
)
from avesrag_assistant.conversa.config import ConversaInvalida, carregar_conversa
from avesrag_assistant.conversa.perguntas import (
    Pergunta,
    aplicar_resposta,
    escolher,
    ganho,
    opcoes,
    pesos,
    valores_da_ficha,
)
from avesrag_assistant.settings import ROOT_DIR

REAL = ROOT_DIR / "config" / "conversa.yaml"


@pytest.fixture(scope="module")
def conversa(vocab_regras):
    return carregar_conversa(REAL, vocab_regras[0])


@pytest.fixture
def perguntas(conversa):
    return conversa.perguntas


def _ficha(**dados):
    if "cores_por_parte" in dados:
        dados["cores_por_parte"] = [
            CorPorParte(parte=p, cor=c) for p, c in dados["cores_por_parte"]
        ]
    return Atributos(**dados)


# ---------------------------------------------------------------------------
# conversa.yaml
# ---------------------------------------------------------------------------


def test_conversa_real_carrega(conversa):
    assert conversa.busca.modo == "hibrido"
    assert conversa.perguntas.maximo == 3
    assert conversa.simulacao.vizinhas_de("laranja") == ["amarelo", "vermelho"]


def _gravar(tmp_path, **mudancas):
    dados = yaml.safe_load(REAL.read_text(encoding="utf-8"))
    for caminho, valor in mudancas.items():
        alvo = dados
        *pais, chave = caminho.split("__")
        for p in pais:
            alvo = alvo[p]
        alvo[chave] = valor
    path = tmp_path / "conversa.yaml"
    path.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mudancas", "erro"),
    [
        ({"perguntas__perguntaveis": ["cores"]}, "não viram pergunta"),
        ({"perguntas__perguntaveis": ["asa"]}, "desconhecidos"),
        ({"perguntas__partes": ["rabo"]}, "fora do vocabulário"),
        ({"perguntas__temperatura": 0}, "temperatura"),
        ({"busca__modo": "degradado"}, "modo"),
        ({"simulacao__vizinhas": [["vermelho", "carmim"]]}, "carmim"),
    ],
)
def test_conversa_invalida(tmp_path, vocab_regras, mudancas, erro):
    with pytest.raises(ConversaInvalida, match=erro):
        carregar_conversa(_gravar(tmp_path, **mudancas), vocab_regras[0])


# ---------------------------------------------------------------------------
# Opções, valores e ganho
# ---------------------------------------------------------------------------


def test_opcoes_tiram_o_que_foi_dito_e_o_que_foi_perguntado(obs, perguntas):
    a = obs(
        tamanho=["pequeno"],
        cores_por_parte=[{"parte": "barriga", "cor": "amarelo"}],
        bico={"comprimento": "curto"},
    )
    chaves = [p.chave for p in opcoes(perguntas, a, ["marcas", "cores_por_parte:peito"])]
    assert "tamanho" not in chaves and "marcas" not in chaves
    assert "cores_por_parte:barriga" not in chaves and "cores_por_parte:peito" not in chaves
    assert "cores_por_parte:cabeca" in chaves
    assert "bico:comprimento" not in chaves and "bico:espessura" in chaves


def test_valores_da_ficha(vocab_regras):
    regras = vocab_regras[1]
    ficha = _ficha(
        cores_por_parte=[("barriga", "amarelo"), ("barriga", "branco"), ("dorso", "marrom")],
        onde_estava=["chao"],
    )
    assert valores_da_ficha(Pergunta("cores_por_parte", "barriga"), ficha, regras) == [
        "amarelo",
        "branco",
    ]
    assert valores_da_ficha(Pergunta("cores_por_parte", "peito"), ficha, regras) == []
    assert valores_da_ficha(Pergunta("onde_estava"), ficha, regras) == ["chao"]
    assert valores_da_ficha(Pergunta("bico", "comprimento"), ficha, regras) == []  # sem bico_tipo


def test_pesos_softmax():
    assert pesos([1.0, 1.0], 1.0) == pytest.approx([0.5, 0.5])
    w = pesos([2.0, 0.0], 1.0)
    assert w[0] > w[1] and sum(w) == pytest.approx(1.0)
    assert pesos([], 1.0) == []


def test_ganho_separa_mais_quem_divide_ao_meio(vocab_regras):
    regras = vocab_regras[1]
    fichas = [
        _ficha(cores_por_parte=[("barriga", "amarelo")], tamanho=["pequeno"]),
        _ficha(cores_por_parte=[("barriga", "amarelo")], tamanho=["pequeno"]),
        _ficha(cores_por_parte=[("barriga", "branco")], tamanho=["pequeno"]),
        _ficha(cores_por_parte=[("barriga", "branco")], tamanho=["pequeno"]),
    ]
    w = [0.25] * 4
    barriga = ganho(Pergunta("cores_por_parte", "barriga"), fichas, w, regras)
    assert barriga == pytest.approx(0.5)  # qualquer resposta tira metade
    assert ganho(Pergunta("tamanho"), fichas, w, regras) == 0  # todas iguais
    assert ganho(Pergunta("marcas"), fichas, w, regras) == 0  # ninguém tem o dado


def test_ganho_nao_elimina_quem_nao_tem_o_dado_nem_quem_tem_os_dois(vocab_regras):
    regras = vocab_regras[1]
    fichas = [
        _ficha(cores_por_parte=[("peito", "amarelo")]),
        _ficha(cores_por_parte=[("peito", "amarelo"), ("peito", "branco")]),
        _ficha(),
    ]
    # Ninguém é eliminado: a 2ª tem as duas cores, a 3ª não tem o dado.
    assert ganho(Pergunta("cores_por_parte", "peito"), fichas, [1 / 3] * 3, regras) == 0


def test_escolher_a_de_maior_ganho_ou_nenhuma(obs, perguntas, vocab_regras):
    regras = vocab_regras[1]
    candidatas = [
        (1.0, _ficha(cores_por_parte=[("barriga", "amarelo")], onde_estava=["chao"])),
        (1.0, _ficha(cores_por_parte=[("barriga", "branco")], onde_estava=["chao"])),
    ]
    escolha = escolher(candidatas, obs(), perguntas, regras)
    assert escolha.pergunta == Pergunta("cores_por_parte", "barriga")
    assert escolha.ganho == pytest.approx(0.5)
    # Já perguntada: sobra só o que não separa.
    assert escolher(candidatas, obs(), perguntas, regras, ["cores_por_parte:barriga"]) is None
    assert escolher(candidatas[:1], obs(), perguntas, regras) is None  # uma candidata só


def test_aplicar_resposta(obs, vocab_regras):
    a = obs(cores_por_parte=[{"parte": "peito", "cor": "amarelo"}], tamanho=["pequeno"])
    b = aplicar_resposta(a, Pergunta("cores_por_parte", "barriga"), ["branco"], *vocab_regras)
    assert [(p.parte, p.cor) for p in b.cores_por_parte] == [
        ("peito", "amarelo"),
        ("barriga", "branco"),
    ]
    c = aplicar_resposta(b, Pergunta("bico", "comprimento"), ["curto", "medio"], *vocab_regras)
    assert c.bico.comprimento == "curto"
    d = aplicar_resposta(c, Pergunta("tamanho"), ["medio"], *vocab_regras)
    assert d.tamanho == ["pequeno", "medio"]
    assert aplicar_resposta(d, Pergunta("marcas"), [], *vocab_regras) is d  # "não sei"


# ---------------------------------------------------------------------------
# Usuário simulado
# ---------------------------------------------------------------------------


def test_responder_pela_ficha(conversa, vocab_regras):
    regras = vocab_regras[1]
    ficha = _ficha(
        cores_por_parte=[("barriga", "amarelo"), ("barriga", "branco")],
        onde_estava=["chao", "copa"],
    )
    rng = random.Random(0)
    sim = conversa.simulacao
    barriga = Pergunta("cores_por_parte", "barriga")
    assert responder(barriga, ficha, regras, sim, rng) == (["amarelo", "branco"], False)
    resposta, ruido = responder(Pergunta("onde_estava"), ficha, regras, sim, rng)
    assert len(resposta) == 1 and resposta[0] in ("chao", "copa") and not ruido  # um valor só
    assert responder(Pergunta("marcas"), ficha, regras, sim, rng) == ([], False)  # não sei


def test_responder_com_ruido(conversa, vocab_regras):
    regras = vocab_regras[1]
    ficha = _ficha(cores_por_parte=[("barriga", "laranja")], cauda=["longa"])
    sim = conversa.simulacao
    rng = random.Random(0)
    cor, ruido = responder(Pergunta("cores_por_parte", "barriga"), ficha, regras, sim, rng, 1.0)
    assert ruido and cor[0] in ("amarelo", "vermelho")
    assert responder(Pergunta("cauda"), ficha, regras, sim, rng, 1.0) == ([], True)
    # Mesma semente, mesma resposta.
    a = responder(Pergunta("cores_por_parte", "barriga"), ficha, regras, sim, random.Random(7), 1.0)
    b = responder(Pergunta("cores_por_parte", "barriga"), ficha, regras, sim, random.Random(7), 1.0)
    assert a == b


def test_relatorio_da_simulacao():
    from avesrag_assistant.avaliacao.simulacao import Passo

    class C:
        def __init__(self, id_, origem):
            self.id, self.esperada, self.origem = id_, "sp_x", origem

    sims = [
        Simulado(
            C("p1", "foto"), [5, 2, 1, 1], [Passo("cores_por_parte:barriga", 0.4, ["amarelo"])]
        ),
        Simulado(C("g1", "llm"), [None, None, 12, 3], [Passo("marcas", 0.2, [])]),
    ]
    linhas = tabela(sims)
    assert linhas[1].split() == ["pessoas", "1", "0", "0%", "0%", "100%"]
    assert linhas[3].split() == ["pessoas", "1", "2", "100%", "100%", "100%"]
    assert resumo_perguntas(sims) == [
        "cores_por_parte: 1 pergunta(s), 0 não sei",
        "marcas: 1 pergunta(s), 1 não sei",
    ]
    tsv = linhas_tsv(sims)
    assert tsv[0] == ["caso", "esperada", "origem", "pos_0", "pos_1", "pos_2", "pos_3", "perguntas"]
    assert tsv[2][3:] == ["", "", "12", "3", "marcas=não sei"]
