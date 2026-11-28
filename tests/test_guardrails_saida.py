"""Conferência do texto livre do LLM na saída (P3c, planos/plano_guardrails.md §4.1)."""

import logging

import pytest
import yaml
from avesrag_builder.llm.base import Resposta

from avesrag_assistant.api.config import ConfigInvalida, carregar_api
from avesrag_assistant.api.fabrica import _conferir_saida_do_redator
from avesrag_assistant.api.guardrails import (
    RegrasTextoLivre,
    conferidor_da_pergunta,
    conferir_texto_livre,
    nomes_das_aves,
    palavras,
    trechos_do_texto,
)
from avesrag_assistant.llm.cache import CacheLLM, ErroLLM
from avesrag_assistant.llm.pergunta import RedatorPergunta, reserva
from avesrag_assistant.llm.prompts import carregar_prompt
from avesrag_assistant.settings import ROOT_DIR

PROMPT_C02 = ROOT_DIR / "config" / "prompts" / "c02_pergunta.yaml"
SYSTEM = "Você escreve uma pergunta curta, em português do Brasil, num tom simples e gentil."


def regras(**mudar):
    base = {
        "maximo_caracteres": 120,
        "terminar_com": "?",
        "marcas_proibidas": ("http", "www.", "`", "<", ">", "{", "}", "[", "]", "|"),
        "nomes": frozenset(
            {
                tuple(palavras(n))
                for n in ["bem-te-vi", "sabiá-laranjeira", "Pitangus sulphuratus", "tucanuçu"]
            }
        ),
        "trechos": trechos_do_texto(SYSTEM, 6),
        "palavras_do_prompt": 6,
        "tema": "aviso",
    }
    return RegrasTextoLivre(**{**base, **mudar})


# ---------------------------------------------------------------------------
# A função pura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto, motivo",
    [
        ("Qual era a cor da barriga?", None),
        ("Certo! E o bico, era curto ou comprido?", None),
        ("x" * 121 + "?", "grande"),
        ("Qual era a cor da barriga", "sem_final"),
        ("Era um bem-te-vi?", "nome_de_ave"),
        ("Será que era um Bem Te Vi mesmo?", "nome_de_ave"),  # sem hífen e com maiúsculas
        ("Era o SABIÁ-LARANJEIRA?", "nome_de_ave"),
        ("Parece o Pitangus sulphuratus?", "nome_de_ave"),
        ("Era um tucanuçu?", "nome_de_ave"),
        ("Veja www.wikiaves.com.br, ok?", "marcacao"),
        ("Qual a <pedido> cor?", "marcacao"),
        ("```cor```?", "marcacao"),
        ("Você escreve uma pergunta curta, em português do Brasil, certo?", "vazamento"),
    ],
)
def test_conferir_texto_livre(texto, motivo):
    assert conferir_texto_livre(texto, regras()) == motivo


def test_nome_de_ave_so_vale_inteiro():
    # "bem" e "vi" soltos não são o nome; "sabiá" sozinho não é "sabiá-laranjeira".
    assert conferir_texto_livre("Você viu bem a ave? Era um sabiá?", regras()) is None
    assert conferir_texto_livre("Era um sabiazinho?", regras()) is None


def test_tema_aviso_recusa_e_desligado():
    pergunta, pedido = "Onde ela estava?", "a cor da barriga"
    assert conferir_texto_livre(pergunta, regras(tema="aviso"), pedido) is None
    assert conferir_texto_livre(pergunta, regras(tema="desligado"), pedido) is None
    assert conferir_texto_livre(pergunta, regras(tema="recusa"), pedido) == "tema"
    assert conferir_texto_livre("De que cor era a barriga?", regras(tema="recusa"), pedido) is None


def test_tema_em_aviso_so_registra_no_log_sem_o_texto(caplog):
    conferir = conferidor_da_pergunta(regras(tema="aviso"))
    with caplog.at_level(logging.WARNING):
        assert conferir("Onde ela estava?", "a cor da barriga") is None
    assert "fora do tema" in caplog.text and "Onde ela estava" not in caplog.text


def test_trechos_ignoram_os_marcadores_do_prompt():
    trechos = trechos_do_texto("abra a {vocabulario} agora mesmo depois", 3)
    assert ("abra", "a", "vocabulario") not in trechos


def test_nomes_das_aves_vem_das_fichas():
    class Colecao:
        def find(self, *_):
            return [
                {
                    "nome_cientifico": "Pitangus sulphuratus",
                    "nomes": {"pt": ["bem-te-vi"], "en": ["Great Kiskadee"]},
                },
                {"nome_cientifico": "Rhea americana", "nomes": {}},
            ]

    nomes = nomes_das_aves({"species": Colecao()})
    assert ("bem", "te", "vi") in nomes and ("great", "kiskadee") in nomes
    assert ("rhea", "americana") in nomes and () not in nomes


# ---------------------------------------------------------------------------
# O redator
# ---------------------------------------------------------------------------


class Falso:
    nome = "falso"

    def __init__(self, *textos):
        self.textos = list(textos)
        self.pedidos = []

    def gerar(self, pedido):
        self.pedidos.append(pedido)
        return Resposta(
            id=pedido.id,
            conteudo=f'{{"pergunta": "{self.textos.pop(0)}"}}',
            tokens_entrada=1,
            tokens_saida=1,
        )


def _redator(tmp_path, vocab_regras, *textos, conferidor=True):
    prompt = carregar_prompt(PROMPT_C02, vocab_regras[0])
    falso = Falso(*textos)
    redator = RedatorPergunta(prompt, CacheLLM(tmp_path / "llm.json", falso), *vocab_regras, "m")
    if conferidor:
        redator.conferidor = conferidor_da_pergunta(
            regras(trechos=trechos_do_texto(redator.system, 6))
        )
    return redator, falso, tmp_path / "llm.json"


def test_redator_pede_de_novo_quando_cita_uma_ave(tmp_path, vocab_regras, obs):
    redator, falso, _ = _redator(
        tmp_path, vocab_regras, "Era um bem-te-vi?", "Que cor era a barriga?"
    )
    assert redator.redigir("a cor da barriga", obs()) == "Que cor era a barriga?"
    assert len(falso.pedidos) == 2


def test_redator_sem_resposta_valida_levanta_errollm_e_nao_guarda_nada(tmp_path, vocab_regras, obs):
    redator, falso, arquivo = _redator(
        tmp_path, vocab_regras, "Era um bem-te-vi?", "Parece o Pitangus sulphuratus?"
    )
    with pytest.raises(ErroLLM):  # quem chama (o orquestrador) cai na pergunta de reserva
        redator.redigir("a cor da barriga", obs())
    assert not arquivo.exists() or "bem" not in arquivo.read_text(encoding="utf-8")


def test_resposta_ruim_que_ja_estava_no_cache_vira_errollm(tmp_path, vocab_regras, obs):
    # Guardada quando ainda não havia conferência: agora não passa, e não pode virar um 500.
    antigo, _, _ = _redator(tmp_path, vocab_regras, "Era um bem-te-vi?", conferidor=False)
    assert antigo.redigir("a cor da barriga", obs()) == "Era um bem-te-vi?"
    novo, falso, _ = _redator(tmp_path, vocab_regras, conferidor=True)
    with pytest.raises(ErroLLM):
        novo.redigir("a cor da barriga", obs())
    assert falso.pedidos == []  # veio do cache; não foi pedida outra


def test_recusa_no_log_leva_o_motivo_e_nao_o_texto(tmp_path, vocab_regras, obs, caplog):
    redator, _, _ = _redator(tmp_path, vocab_regras, "Era um bem-te-vi?", "Que cor era a barriga?")
    with caplog.at_level(logging.WARNING):
        redator.redigir("a cor da barriga", obs())
    assert "nome_de_ave" in caplog.text and "bem-te-vi" not in caplog.text


def test_reserva_passa_na_conferencia():
    conferir = conferidor_da_pergunta(regras(tema="recusa"))
    for pedido in ("o tamanho da ave", "como era a cauda (o rabo), no tamanho e no formato"):
        assert conferir(reserva(pedido), pedido) is None


# ---------------------------------------------------------------------------
# Configuração e ligação
# ---------------------------------------------------------------------------


def test_config_real_traz_a_saida():
    saida = carregar_api(ROOT_DIR / "config" / "api.yaml").guardrails.saida
    assert (saida.maximo_caracteres, saida.terminar_com, saida.tema) == (120, "?", "aviso")


def test_config_recusa_tema_desconhecido(tmp_path):
    bruto = yaml.safe_load((ROOT_DIR / "config" / "api.yaml").read_text(encoding="utf-8"))
    bruto["guardrails"]["saida"]["tema"] = "talvez"
    arquivo = tmp_path / "api.yaml"
    arquivo.write_text(yaml.safe_dump(bruto, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigInvalida):
        carregar_api(arquivo)


def test_fabrica_liga_o_conferidor_so_ao_redator_que_aceita(tmp_path, vocab_regras):
    class Colecao:
        def find(self, *_):
            return [{"nome_cientifico": "Pitangus sulphuratus", "nomes": {"pt": ["bem-te-vi"]}}]

    class Servicos:
        db = {"species": Colecao()}

    config = carregar_api(ROOT_DIR / "config" / "api.yaml")
    redator, _, _ = _redator(tmp_path, vocab_regras, conferidor=False)
    servicos = Servicos()
    servicos.redator = redator
    _conferir_saida_do_redator(servicos, config)
    assert redator.conferidor("Era um bem-te-vi?", "a cor") == "nome_de_ave"
    assert redator.conferidor("Qual era a cor da barriga?", "a cor da barriga") is None

    servicos.redator = object()  # um redator falso: nada a ligar, e nada quebra
    _conferir_saida_do_redator(servicos, config)
