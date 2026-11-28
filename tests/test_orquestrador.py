"""Orquestrador com LLM e banco falsos: cada transição, o estado em JSON e a confirmação."""

import pytest
from avesrag_builder.db.collections import SPECIES_RETRIEVAL_DOCS

from avesrag_assistant.busca.modelos import validar_atributos
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.conversa import estado as sessoes
from avesrag_assistant.conversa.config import carregar_conversa
from avesrag_assistant.conversa.orquestrador import (
    ENCERRADA,
    FORA_DE_ESCOPO,
    FORA_SOBRE_AVE,
    NADA_DESCRITO,
    NAO_ENTENDI,
    PEDIR_ESCOLHA,
    SEM_MAIS_RODADAS,
    Orquestrador,
    Servicos,
    ler_confirmacao,
)
from avesrag_assistant.llm.cache import ErroLLM
from avesrag_assistant.llm.extrator import Extracao
from avesrag_assistant.settings import ROOT_DIR
from conftest import BancoFalso


def _ficha(sid, barriga, nota):
    doc = {"species_id": sid, "dominio": "aparencia", "nota": nota}
    ficha = {
        "species_id": sid,
        "nome_cientifico": f"Ave {sid}",
        "nomes": {"pt": [f"ave-{sid}"]},
        "atributos": {
            "tamanho": ["pequeno"],
            "cores": [barriga],
            "cores_por_parte": [{"parte": "barriga", "cor": barriga}],
        },
    }
    return doc, ficha


ESPECIES = [("sp_a", "amarelo", 0.8), ("sp_b", "branco", 0.7), ("sp_c", "vermelho", 0.6)]
EXTRA = ("sp_d", "preto", 0.5)


def _fora(filtro):
    """As species_id de um `$nin` em qualquer ponto do filtro (as recusadas)."""
    if isinstance(filtro, dict):
        ids = set(filtro.get("species_id", {}).get("$nin", []))
        for v in filtro.values():
            if isinstance(v, list):
                for item in v:
                    ids |= _fora(item)
        return ids
    return set()


def _banco(especies=ESPECIES):
    pares = [_ficha(*e) for e in especies]
    docs = [d for d, _ in pares]
    banco = BancoFalso(docs, [f for _, f in pares])

    def responder(pipeline):
        etapa = pipeline[0].get("$vectorSearch") or {}
        fora = _fora(etapa.get("filter"))
        return [d for d in docs if d["species_id"] not in fora]

    banco[SPECIES_RETRIEVAL_DOCS].responder = responder
    return banco


# Mensagem → atributos que ela acrescenta.
ROTEIRO = {
    "uma ave pequena": {"tamanho": ["pequeno"], "cores": ["amarelo"]},
    "amarela": {"cores_por_parte": [{"parte": "barriga", "cor": "amarelo"}]},
    "só cor": {"cores": ["amarelo"]},
}


class ExtratorFalso:
    def __init__(self, vocab_regras, falhar=False, fora=False):
        self.vocab_regras = vocab_regras
        self.chamadas = []
        self.falhar = falhar
        self.fora = fora

    def extrair(self, mensagem, atributos=None, descricao=None, pergunta=None):
        self.chamadas.append((mensagem, pergunta))
        if self.falhar:
            raise ErroLLM("falhou")
        dados = atributos.model_dump(exclude_none=True) if atributos else {}
        for nome, valores in ROTEIRO.get(mensagem, {}).items():
            dados[nome] = [*dados.get(nome, []), *valores]
        return Extracao(
            fora_de_escopo=self.fora,
            local=None,
            data_observacao=None,
            atributos=validar_atributos(dados, *self.vocab_regras),
            descricao_para_busca=mensagem,
        )


class RedatorFalso:
    def __init__(self, falhar=False):
        self.falhar = falhar

    def redigir(self, pedido, atributos):
        if self.falhar:
            raise ErroLLM("falhou")
        return f"Pergunta sobre {pedido}?"


class SugestorFalso:
    def __init__(self, sugeridas=()):
        self.sugeridas = list(sugeridas)
        self.mensagens = []

    def especies(self, mensagem, indice):
        self.mensagens.append(mensagem)
        return self.sugeridas


@pytest.fixture(scope="module")
def config(vocab_regras):
    return (
        carregar_ranking(ROOT_DIR / "config" / "ranking.yaml"),
        carregar_conversa(ROOT_DIR / "config" / "conversa.yaml", vocab_regras[0]),
    )


@pytest.fixture
def montar(vocab_regras, config):
    ranking, conversa = config

    def _montar(banco=None, extrator=None, redator=None, sugestor=None, conversa_=None):
        servicos = Servicos(
            db=banco or _banco(),
            ranking=ranking,
            conversa=conversa_ or conversa,
            vocabulario=vocab_regras[0],
            regras_bico=vocab_regras[1],
            extrator=extrator or ExtratorFalso(vocab_regras),
            redator=redator or RedatorFalso(),
            vetor=lambda texto: [0.1],
            sugestor=sugestor if sugestor is not None else SugestorFalso(),
            indice={},
        )
        return Orquestrador(servicos, sessoes.EstadoConversa(id="teste1"))

    return _montar


def test_descricao_pergunta_o_que_separa(montar):
    o = montar()
    resposta = o.processar("uma ave pequena")
    assert resposta == "Pergunta sobre a cor da barriga?"
    e = o.estado
    assert (e.etapa, e.pergunta, e.perguntas_feitas) == (
        "coleta",
        "cores_por_parte:barriga",
        ["cores_por_parte:barriga"],
    )
    assert e.descricoes == ["uma ave pequena"]
    assert o.s.sugestor.mensagens == ["uma ave pequena"]
    assert [m.papel for m in e.mensagens] == ["usuario", "assistente"]


def test_resposta_vai_com_a_pergunta_e_nao_entra_na_descricao(montar):
    o = montar()
    o.processar("uma ave pequena")
    resposta = o.processar("amarela")
    e = o.estado
    assert o.s.extrator.chamadas[1] == ("amarela", "Pergunta sobre a cor da barriga?")
    assert e.descricoes == ["uma ave pequena"]
    assert len(o.s.sugestor.mensagens) == 1  # resposta não refaz as sugestões
    # Nada mais separa (todas pequenas): apresenta, com a de barriga amarela primeiro.
    assert e.etapa == "confirmacao" and e.pergunta is None
    assert [c.species_id for c in e.candidatas] == ["sp_a", "sp_b", "sp_c"]
    assert e.modo_busca == "hibrido"
    assert "1. ave-sp_a (Ave sp_a) — combina: tamanho, cores, cores de cada parte" in resposta
    assert resposta.endswith(PEDIR_ESCOLHA)


def _ate_confirmacao(o):
    o.processar("uma ave pequena")
    o.processar("amarela")
    assert o.estado.etapa == "confirmacao"


def test_confirma_pelo_numero(montar):
    o = montar()
    _ate_confirmacao(o)
    assert o.processar("é a 2") == "Que bom! Então era ave-sp_b (Ave sp_b)."
    assert (o.estado.etapa, o.estado.confirmada, o.estado.motivo_fim) == (
        "fim",
        "sp_b",
        "confirmada",
    )
    assert o.processar("oi") == ENCERRADA


def test_sim_sem_numero_e_texto_solto(montar):
    o = montar()
    _ate_confirmacao(o)
    assert "Qual delas" in o.processar("sim")
    assert o.processar("talvez") == PEDIR_ESCOLHA
    assert o.estado.etapa == "confirmacao"


def test_nao_recusa_e_tenta_de_novo_ate_o_limite(montar):
    o = montar(banco=_banco([*ESPECIES, EXTRA]))
    _ate_confirmacao(o)
    resposta = o.processar("não")
    e = o.estado
    assert e.recusadas == ["sp_a", "sp_b", "sp_c"]
    assert e.rodada_confirmacao == 1
    assert resposta.startswith("Tudo bem, vou procurar de novo sem essas.")
    assert [c.species_id for c in e.candidatas] == ["sp_d"]  # só sobrou uma
    assert o.processar("nenhuma") == SEM_MAIS_RODADAS
    assert (e.etapa, e.motivo_fim, e.rodada_confirmacao) == ("fim", "sem_confirmacao", 2)


def test_fora_de_escopo_nao_muda_o_estado(vocab_regras, montar):
    o = montar(extrator=ExtratorFalso(vocab_regras, fora=True))
    assert o.processar("me conta uma piada") == FORA_DE_ESCOPO
    assert o.estado.descricoes == [] and o.estado.atributos == {}


def test_recusa_diz_se_a_mensagem_fala_de_ave(vocab_regras, montar):
    o = montar(extrator=ExtratorFalso(vocab_regras, fora=True))
    assert o.processar("me conta uma piada") == FORA_DE_ESCOPO  # sem detector: texto geral
    o.s.fala_de_ave = lambda texto: "tucano" in texto
    assert o.processar("quanto vive um tucano?") == FORA_SOBRE_AVE
    assert o.processar("qual a capital da França?") == FORA_DE_ESCOPO
    assert FORA_SOBRE_AVE != FORA_DE_ESCOPO and "melhorar" in FORA_SOBRE_AVE
    assert o.estado.descricoes == [] and o.estado.atributos == {}  # nenhuma das duas muda o estado


def test_mensagem_sem_nenhuma_caracteristica_pede_para_lembrar_sem_buscar(vocab_regras, montar):
    sugestor = SugestorFalso()
    o = montar(sugestor=sugestor)
    resposta = o.processar("quero identificar uma ave, mas não lembro de nada")
    assert resposta == NADA_DESCRITO and "cor" in resposta
    e = o.estado
    assert (e.etapa, e.descricoes, e.atributos, e.pergunta, e.buscas) == (
        "coleta",
        [],
        {},
        None,
        [],
    )
    assert sugestor.mensagens == []  # nem sugestões nem busca: nada para buscar ainda
    # A conversa segue normalmente quando a pessoa lembra de algo.
    assert o.processar("uma ave pequena") != NADA_DESCRITO
    assert o.estado.descricoes == ["uma ave pequena"]


def test_resposta_sem_informacao_a_uma_pergunta_nao_cai_no_pedido_para_lembrar(
    vocab_regras, montar
):
    o = montar()
    o.processar("uma ave pequena")  # a conversa já tem atributos e uma pergunta pendente
    assert o.estado.pergunta is not None
    assert o.processar("não sei") != NADA_DESCRITO


def test_falha_do_extrator(vocab_regras, montar):
    o = montar(extrator=ExtratorFalso(vocab_regras, falhar=True))
    assert o.processar("uma ave pequena") == NAO_ENTENDI
    assert o.estado.etapa == "coleta"


def test_falha_do_redator_usa_a_pergunta_de_reserva(montar):
    o = montar(redator=RedatorFalso(falhar=True))
    assert o.processar("uma ave pequena") == "Você lembra a cor da barriga?"


def test_sem_visuais_suficientes_usa_o_degradado(montar, config):
    _, conversa = config
    sem_perguntas = conversa.model_copy(
        update={"perguntas": conversa.perguntas.model_copy(update={"maximo": 0})}
    )
    o = montar(conversa_=sem_perguntas)
    resposta = o.processar("só cor")
    assert o.estado.modo_busca == "degradado"
    assert resposta.startswith("Com poucas características")


def test_sugestoes_entram_na_busca(montar):
    o = montar(sugestor=SugestorFalso(["sp_c"]))
    o.processar("uma ave pequena")
    o.processar("amarela")
    # sp_c ganha o bônus da sugestão, mas a barriga vermelha conflita.
    assert o.estado.sugeridas == ["sp_c"]
    assert "sp_c" in [c.species_id for c in o.estado.candidatas]


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("2", 2),
        ("é a número 3!", 3),
        ("Não", "nao"),
        ("nenhuma delas", "nao"),
        ("sim", "sim"),
        ("4", None),
        ("1 ou 2", None),
        ("", None),
    ],
)
def test_ler_confirmacao(texto, esperado):
    assert ler_confirmacao(texto) == esperado


# ---------------------------------------------------------------------------
# Estado em JSON
# ---------------------------------------------------------------------------


def test_estado_salva_e_retoma(tmp_path, montar):
    o = montar()
    o.processar("uma ave pequena")
    path = sessoes.salvar(o.estado, tmp_path)
    assert path == tmp_path / "teste1.json"
    assert not list(tmp_path.glob(".*.tmp"))  # sem temporário sobrando
    retomado = sessoes.carregar(tmp_path, "teste1")
    assert retomado.pergunta == "cores_por_parte:barriga"
    # Retoma de onde parou: a próxima mensagem é a resposta.
    o2 = Orquestrador(o.s, retomado)
    o2.processar("amarela")
    assert o2.estado.etapa == "confirmacao"


def test_sessao_inexistente_ou_id_invalido(tmp_path):
    with pytest.raises(FileNotFoundError):
        sessoes.carregar(tmp_path, "abc123")
    with pytest.raises(ValueError, match="inválido"):
        sessoes.carregar(tmp_path, "../x")
