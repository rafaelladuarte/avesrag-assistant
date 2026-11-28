"""O conjunto de escopo (avaliacao/escopo.yaml): formato e coerência (GR3)."""

from collections import Counter

import pytest
import yaml

from avesrag_assistant.api.config import carregar_api
from avesrag_assistant.api.guardrails import compilar_padroes, detectar_injecao
from avesrag_assistant.avaliacao.escopo import EscopoInvalido, carregar_escopo
from avesrag_assistant.settings import ROOT_DIR

ARQUIVO = ROOT_DIR / "avaliacao" / "escopo.yaml"


@pytest.fixture(scope="module")
def mensagens():
    return carregar_escopo(ARQUIVO)


def test_arquivo_real_carrega_e_tem_os_quatro_grupos(mensagens):
    grupos = Counter(m.grupo for m in mensagens)
    assert set(grupos) == {"resposta_curta", "descricao_atipica", "fora", "fronteira"}
    assert grupos["resposta_curta"] >= 30 and grupos["fora"] >= 25 and grupos["fronteira"] >= 15


def test_fronteira_toda_para_revisar_e_o_resto_nao(mensagens):
    assert all(m.revisar for m in mensagens if m.grupo == "fronteira")
    assert not any(m.revisar for m in mensagens if m.grupo != "fronteira")


def test_o_conjunto_mede_o_c01_e_nao_o_filtro_de_injecao(mensagens):
    padroes = compilar_padroes(
        carregar_api(ROOT_DIR / "config" / "api.yaml").guardrails.padroes_injecao
    )
    barradas = [m.id for m in mensagens if detectar_injecao(m.texto, padroes)]
    assert barradas == []


def test_perguntas_das_respostas_curtas_sao_do_assistente(mensagens):
    perguntas = {m.pergunta for m in mensagens if m.grupo == "resposta_curta"}
    assert len(perguntas) >= 8 and all(p.endswith("?") for p in perguntas)


def _gravar(tmp_path, mensagens):
    arquivo = tmp_path / "escopo.yaml"
    arquivo.write_text(yaml.safe_dump({"versao": "1", "mensagens": mensagens}), encoding="utf-8")
    return arquivo


def _m(**mudar):
    base = {"id": "f001", "grupo": "fora", "esperado": "fora", "texto": "qual a capital da França?"}
    return {**base, **mudar}


@pytest.mark.parametrize(
    "mensagens, trecho",
    [
        ([_m(), _m()], "repetido"),
        ([_m(id="a001")], "começa com"),
        ([_m(pergunta="Qual a cor?")], "só a resposta_curta"),
        ([_m(id="r001", grupo="resposta_curta", esperado="escopo")], "só a resposta_curta"),
        ([_m(id="b001", grupo="fronteira")], "revisar"),
        ([_m(esperado="escopo")], "sempre fora"),
        ([_m(texto="x" * 1001)], "texto"),
        ([_m(extra="x")], "extra"),
    ],
)
def test_arquivo_invalido_e_recusado(tmp_path, mensagens, trecho):
    with pytest.raises(EscopoInvalido, match=trecho):
        carregar_escopo(_gravar(tmp_path, mensagens))
