"""Contrato de entrada do retriever: AtributosObservados e Consulta contra o vocabulário."""

import pytest
from pydantic import ValidationError

from avesrag_assistant.busca.modelos import (
    ATRIBUTOS_LISTA,
    ATRIBUTOS_NAO_OBSERVADOS,
    AtributosObservados,
    validar_consulta,
)


def test_valores_validos_passam(obs):
    a = obs(
        tamanho=["pequeno", "medio"],
        cores=["amarelo", "preto"],
        cores_por_parte=[{"parte": "barriga", "cor": "amarelo"}],
        bico={"comprimento": "medio", "extra": ["pontiagudo"]},
    )
    assert a.informados() == ["tamanho", "cores", "cores_por_parte", "bico"]


@pytest.mark.parametrize(
    ("dados", "caminho"),
    [
        ({"cores": ["amarelo", "roxo_neon"]}, "cores[1]: 'roxo_neon'"),
        ({"cores_por_parte": [{"parte": "joelho", "cor": "preto"}]}, "cores_por_parte[0].parte"),
        ({"cores_por_parte": [{"parte": "barriga", "cor": "xadrez"}]}, "cores_por_parte[0].cor"),
        ({"bico": {"espessura": "gordo"}}, "bico.espessura: 'gordo'"),
        ({"bico": {"extra": ["serrilhado"]}}, "bico.extra[0]"),
    ],
)
def test_valor_fora_do_vocabulario_e_recusado_com_o_caminho(obs, dados, caminho):
    with pytest.raises(ValidationError, match=caminho.replace("[", r"\[").replace("]", r"\]")):
        obs(**dados)


def test_sinonimo_e_recusado(obs):
    with pytest.raises(ValidationError, match="'amarela' não está no vocabulário"):
        obs(cores=["amarela"])


def test_varios_erros_saem_juntos(obs):
    with pytest.raises(ValidationError) as exc:
        obs(cores=["roxo_neon"], marcas=["nada"])
    assert "cores[0]" in str(exc.value) and "marcas[0]" in str(exc.value)


def test_sem_contexto_falha():
    with pytest.raises(ValidationError, match="precisa do vocabulário"):
        AtributosObservados.model_validate({"cores": ["amarelo"]})
    with pytest.raises(ValidationError, match="precisa do vocabulário"):
        AtributosObservados(cores=["amarelo"])


def test_lista_vazia_e_bico_vazio_viram_none(obs):
    a = obs(cores=[], bico={"extra": []}, marcas=None)
    assert a.cores is None and a.bico is None and a.informados() == []


def test_repetidos_sao_removidos_mantendo_a_ordem(obs):
    a = obs(
        cores=["preto", "amarelo", "preto"],
        cores_por_parte=[{"parte": "barriga", "cor": "amarelo"}] * 2,
    )
    assert a.cores == ["preto", "amarelo"]
    assert len(a.cores_por_parte) == 1


def test_campo_extra_e_recusado(obs):
    with pytest.raises(ValidationError, match="bico_tipo"):
        obs(bico_tipo="generalista")


def test_campos_batem_com_o_vocabulario(vocab_regras):
    """Um atributo novo no builder quebra aqui, em vez de ser ignorado em silêncio."""
    vocab, _ = vocab_regras
    esperados = set(vocab.atributos) - set(ATRIBUTOS_NAO_OBSERVADOS)
    assert set(ATRIBUTOS_LISTA) == esperados
    assert set(AtributosObservados.model_fields) == esperados | {"cores_por_parte", "bico"}


def test_exemplo_do_c01_valida(vocab_regras):
    """O exemplo do `c01_extrator` no Modelo de Dados."""
    c = validar_consulta(
        {
            "descricao": "ave pequena, barriga amarela, cabeça preta, bico grosso",
            "atributos": {
                "tamanho": ["pequeno"],
                "cores_por_parte": [
                    {"parte": "barriga", "cor": "amarelo"},
                    {"parte": "cabeca", "cor": "preto"},
                ],
                "bico": {"espessura": "grosso"},
                "o_que_fazia": ["pousada"],
            },
        },
        *vocab_regras,
    )
    assert c.atributos.bico.espessura == "grosso"
    assert c.recusadas == []


def test_consulta_sem_atributos(vocab_regras):
    c = validar_consulta({"descricao": "  uma ave  "}, *vocab_regras)
    assert c.descricao == "uma ave"
    assert c.atributos.informados() == []


@pytest.mark.parametrize(
    "dados",
    [
        {"descricao": "   "},
        {"descricao": "ave", "recusadas": ["Pitangus sulphuratus"]},
    ],
)
def test_consulta_invalida(vocab_regras, dados):
    with pytest.raises(ValidationError):
        validar_consulta(dados, *vocab_regras)
