"""O conjunto de escopo (avaliacao/escopo.yaml): formato e coerência (GR3)."""

from collections import Counter

import pytest
import yaml

from avesrag_assistant.api.config import carregar_api
from avesrag_assistant.api.guardrails import compilar_padroes, detectar_injecao
from avesrag_assistant.avaliacao.escopo import (
    EscopoInvalido,
    carregar_escopo,
    gravar_tsv_escopo,
    medir,
    resumir,
    tabela_escopo,
)
from avesrag_assistant.conversa.escopo import carregar_escopo_config, criar_detector, palavras
from avesrag_assistant.llm.cache import ErroLLM
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


# ---------------------------------------------------------------------------
# Qual texto de recusa: "sobre ave" ou "nada a ver" (conversa/escopo.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fala_de_ave():
    config = carregar_escopo_config(ROOT_DIR / "config" / "escopo.yaml")
    return criar_detector(config.palavras_de_ave)


def test_o_detector_acerta_as_mensagens_fora_do_conjunto(mensagens, fala_de_ave):
    # Guarda contra regressão (a lista foi escrita vendo o conjunto, então 100% não prova
    # nada sobre mensagens novas): confere que a anotação e a regra concordam.
    erradas = [
        m.id for m in mensagens if m.esperado == "fora" and fala_de_ave(m.texto) != m.fala_de_ave
    ]
    assert erradas == []


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("quantos anos vive um tucano?", True),
        ("o que os tucanos comem?", True),  # plural
        ("as aves migram?", True),
        ("o beija-flor bebe água?", True),
        ("o bem te vi canta de noite?", True),  # sem hífen
        ("E o joão de barro, faz ninho onde?", True),
        ("QUANTO TEMPO VIVE UM PÁSSARO?", True),  # maiúsculas e acento
        ("me conta uma piada", False),
        ("qual a capital da França?", False),
        ("vi uma cobra verde no jardim", False),
        ("oi", False),
        ("o aviao atrasou", False),  # "ave" não é pedaço de palavra
        ("trave de futebol", False),
    ],
)
def test_detector_de_ave(fala_de_ave, texto, esperado):
    assert fala_de_ave(texto) is esperado


def test_o_detector_usa_os_nomes_das_fichas():
    detector = criar_detector(["ave"], ["bem te vi", "Pitangus sulphuratus", "sabiá-laranjeira"])
    assert detector("o sabiá laranjeira canta?") and detector("o Pitangus sulphuratus vive onde?")
    assert not detector("o sabiá canta?")  # só o nome inteiro de uma ficha; "sabiá" vem da lista


def test_palavras_ignora_acento_e_pontuacao():
    assert palavras("Bem-te-vi, é?") == ("bem", "te", "vi", "e")


# ---------------------------------------------------------------------------
# A medida do fora_de_escopo (GR3)
# ---------------------------------------------------------------------------


class ExtratorDeEscopo:
    """Responde `fora_de_escopo` por uma regra simples, e anota o que recebeu."""

    def __init__(self, fora=(), falhar=()):
        self.fora, self.falhar, self.recebidas = set(fora), set(falhar), []

    def extrair(self, mensagem, atributos=None, descricao=None, pergunta=None):
        self.recebidas.append((mensagem, atributos, descricao, pergunta))
        if mensagem in self.falhar:
            raise ErroLLM("falhou")

        class Extracao:
            fora_de_escopo = mensagem in self.fora

        return Extracao()


def test_medir_manda_a_pergunta_pendente_so_nas_respostas_curtas(mensagens):
    extrator = ExtratorDeEscopo()
    medir(extrator, mensagens, [("c001", "Vi uma ave amarela")], ErroLLM)
    com_pergunta = {m for m, _, _, p in extrator.recebidas if p}
    assert com_pergunta == {m.texto for m in mensagens if m.grupo == "resposta_curta"}
    assert ("Vi uma ave amarela", None, None, None) in extrator.recebidas


def test_extrator_perfeito_aprova(mensagens):
    fora = {m.texto for m in mensagens if m.esperado == "fora"}
    resultados = medir(ExtratorDeEscopo(fora=fora), mensagens, [("c001", "Vi uma ave")], ErroLLM)
    resumo = resumir(resultados)
    assert resumo.aprovado and resumo.fronteira == (20, 20)
    assert resumo.falso_positivo[0] == 0 and resumo.acerto_fora[0] == resumo.acerto_fora[1]
    assert resumo.por_grupo["casos"] == (1, 1)
    assert tabela_escopo(resumo)[-1] == "veredito: APROVADO"


def test_falso_positivo_acima_de_2_por_cento_reprova(mensagens):
    fora = {m.texto for m in mensagens if m.esperado == "fora"}
    curtas = [m.texto for m in mensagens if m.grupo == "resposta_curta"]
    # 3 respostas curtas barradas por engano em ~58 do escopo: ~5%.
    resumo = resumir(medir(ExtratorDeEscopo(fora=fora | set(curtas[:3])), mensagens, [], ErroLLM))
    assert resumo.falso_positivo[0] == 3 and resumo.taxa_falso_positivo > 0.02
    assert not resumo.aprovado and "REPROVADO" in tabela_escopo(resumo)[-1]


def test_fora_que_passa_acima_de_10_por_cento_reprova(mensagens):
    fora = [m.texto for m in mensagens if m.esperado == "fora" and m.grupo == "fora"]
    resumo = resumir(
        medir(ExtratorDeEscopo(fora=fora[10:]), mensagens, [], ErroLLM)
    )  # 10 de 30 passam
    assert resumo.taxa_acerto_fora < 0.90 and not resumo.aprovado


def test_fronteira_nao_conta_para_o_veredito(mensagens):
    nucleo_fora = {m.texto for m in mensagens if m.esperado == "fora" and m.grupo != "fronteira"}
    resumo = resumir(medir(ExtratorDeEscopo(fora=nucleo_fora), mensagens, [], ErroLLM))
    assert (
        resumo.fronteira[0] < resumo.fronteira[1] and resumo.aprovado
    )  # errou a fronteira, e aprova


def test_chamada_que_falha_reprova_e_nao_derruba(mensagens):
    um = mensagens[0].texto
    resumo = resumir(medir(ExtratorDeEscopo(falhar={um}), mensagens, [], ErroLLM))
    assert resumo.erros == 1 and not resumo.aprovado


def test_relatorio_tsv_traz_as_erradas(mensagens, tmp_path):
    resultados = medir(
        ExtratorDeEscopo(), mensagens, [], ErroLLM
    )  # nada é fora: erra todas as fora
    caminho = gravar_tsv_escopo(resultados, tmp_path / "x" / "escopo.tsv")
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    assert linhas[0].split("\t")[:5] == ["id", "grupo", "esperado", "obtido", "certo"]
    assert any(linha.startswith("f001\tfora\tfora\tescopo\tNAO") for linha in linhas)
    assert any(linha.startswith("r001\tresposta_curta\tescopo\tescopo\tsim") for linha in linhas)
