"""Fichas para revisão manual: cada sinal, a ordem e o CSV."""

import csv

import pytest
from avesrag_builder.core.regras_fusao import carregar_regras_fusao

from avesrag_assistant.avaliacao import revisao as rev
from avesrag_assistant.busca.modelos import validar_atributos
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.settings import ROOT_DIR, get_settings


@pytest.fixture(scope="module")
def ctx(vocab_regras):
    voc, regras_bico = vocab_regras
    ranking = carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")
    fusao = carregar_regras_fusao(get_settings().builder_config_dir / "regras_fusao.yaml", voc)
    return voc, regras_bico, ranking, fusao


def ficha(sid="sp_x", nome="ave-x", cientifico="Ave x", familia="Thraupidae", **mudar):
    base = dict(
        species_id=sid, nome=nome, nome_cientifico=cientifico, familia=familia, status="apta",
        atributos={"cores": ["amarelo", "preto"], "cores_por_parte": [{"parte": "barriga", "cor": "amarelo"}],
                   "tamanho": ["pequeno"]},
        comprimento_cm=None, massa_g=None,
    )  # fmt: skip
    base.update(mudar)
    return rev.Ficha(**base)


def linhas(ctx, fichas, observacoes=()):
    voc, regras_bico, ranking, fusao = ctx
    return rev.montar_linhas(
        {f.species_id: f for f in fichas}, list(observacoes), ranking, regras_bico, fusao
    )


def tipos(rs):
    return {(r["species_id"], r["tipo"]) for r in rs}


def obs(ctx, origem, sid, pessoa=True, **atributos):
    voc, regras_bico, *_ = ctx
    return rev.Observacao(
        origem, sid, validar_atributos(atributos, voc, regras_bico), pessoa, "descrição da ave"
    )


def test_ficha_sem_problema_nao_gera_linha(ctx):
    assert linhas(ctx, [ficha()]) == []


def test_conflito_de_pessoa_e_alta_e_so_de_llm_e_media(ctx):
    f = ficha()
    r = linhas(
        ctx, [f], [obs(ctx, "p001", "sp_x", cores_por_parte=[{"parte": "barriga", "cor": "azul"}])]
    )
    assert [(x["prioridade"], x["tipo"], x["campo"]) for x in r] == [
        ("alta", "conflito_observado", "cores_por_parte")
    ]
    assert "barriga:azul" in r[0]["detalhe"] and r[0]["valor_na_ficha"] == "barriga:amarelo"
    r = linhas(
        ctx,
        [f],
        [obs(ctx, "g001", "sp_x", False, cores_por_parte=[{"parte": "barriga", "cor": "azul"}])],
    )
    assert r[0]["prioridade"] == "media" and "LLM" in r[0]["detalhe"]


def test_o_que_bate_ou_falta_na_ficha_nao_e_conflito(ctx):
    r = linhas(ctx, [ficha()], [obs(ctx, "p001", "sp_x", cores=["amarelo"], marcas=["topete"])])
    assert r == []  # a ficha não tem marcas: `sem_dado` não conflita


def test_varias_descricoes_do_mesmo_conflito_viram_uma_linha_com_a_contagem(ctx):
    o = [obs(ctx, f"p00{i}", "sp_x", tamanho=["grande"]) for i in range(1, 5)]
    r = linhas(ctx, [ficha()], o)
    assert len(r) == 1 and "4 descrição(ões)" in r[0]["detalhe"] and "(+1)" in r[0]["evidencia"]


def test_observacao_de_especie_que_nao_esta_na_base_e_ignorada(ctx):
    assert linhas(ctx, [ficha()], [obs(ctx, "p001", "sp_outra", tamanho=["grande"])]) == []


@pytest.mark.parametrize(
    ("mudar", "tipo"),
    [
        ({"atributos": {"cores": ["azul"], "bico_tipo": "frugivoro"}}, "bico_suspeito"),
        ({"familia": "Ardeidae", "atributos": {"cores": ["branco"], "ambiente": ["mata"]}}, "aquatica_sem_agua"),
        ({"atributos": {}}, "sem_cores"),
        ({"atributos": {"cores": ["azul"]}}, "sem_cor_por_parte"),
        ({"atributos": {"cores": ["preto", "branco", "cinza", "marrom", "bege", "verde", "azul"],
                        "cores_por_parte": [{"parte": "asas", "cor": "preto"}]}}, "cores_demais"),
        ({"comprimento_cm": 25.0, "massa_g": 17.0, "atributos": {"cores": ["azul"], "tamanho": ["medio"],
                                                                  "cores_por_parte": [{"parte": "asas", "cor": "azul"}]}},
         "tamanho_discordante"),
        ({"status": "incompleta", "faltas": ["cores (0 de 1)"]}, "ficha_incompleta"),
    ],
)  # fmt: skip
def test_sinais_de_coerencia(ctx, mudar, tipo):
    r = linhas(ctx, [ficha(**mudar)])
    assert tipo in {x["tipo"] for x in r}, [x["tipo"] for x in r]


def test_tucano_e_frugivoro_de_verdade_e_ave_aquatica_com_brejo_estao_certos(ctx):
    certos = [
        ficha("sp_t", familia="Ramphastidae", atributos={"cores": ["preto"], "bico_tipo": "frugivoro",
                                                         "cores_por_parte": [{"parte": "bico", "cor": "laranja"}]}),
        ficha("sp_g", familia="Ardeidae", atributos={"cores": ["branco"], "ambiente": ["brejo"],
                                                     "cores_por_parte": [{"parte": "asas", "cor": "branco"}]}),
    ]  # fmt: skip
    assert linhas(ctx, certos) == []


def test_cor_composta_so_quando_a_ficha_guardou_o_tom(ctx):
    certa = ficha(evidencias=[("atributos.cores", "cinza", "o dorso é cinza-azulado")])
    assert linhas(ctx, [certa]) == []  # guardou a base
    errada = ficha(evidencias=[("atributos.cores", "azul", "o dorso é cinza-azulado")])
    (r,) = linhas(ctx, [errada])
    assert r["tipo"] == "cor_composta" and "`cinza`" in r["sugestao"]
    sozinho = ficha(evidencias=[("atributos.cores", "amarelo", "a barriga amarelada")])
    assert linhas(ctx, [sozinho]) == []  # o tom sozinho está certo
    fora = ficha(evidencias=[("atributos.cores", "verde", "as penas bronze-esverdeadas")])
    assert linhas(ctx, [fora]) == []  # "bronze" não é cor do vocabulário


def test_as_fichas_do_backlog_entram_primeiro_com_a_correcao_prevista(ctx):
    d = ficha("sp_d", "saí-azul", "Dacnis cayana", atributos={"cores": ["azul"], "bico_tipo": "frugivoro",
                                                           "cores_por_parte": [{"parte": "asas", "cor": "azul"}]})  # fmt: skip
    outra = ficha("sp_o", "ave-o", "Ave o", atributos={})
    r = linhas(ctx, [outra, d])
    assert (
        r[0]["species_id"] == "sp_d"
        and r[0]["prioridade"] == "alta"
        and r[0]["no_backlog"] == "B13"
    )
    assert "tucano" in r[0]["sugestao"]
    assert [x["tipo"] for x in r if x["species_id"] == "sp_d"].count(
        "bico"
    ) == 1  # sem duplicar o bico_suspeito


def test_ordem_prioridade_depois_numero_de_sinais(ctx):
    a = ficha("sp_a", "a", atributos={})  # sem_cores (média)
    b = ficha(
        "sp_b", "b", atributos={"cores": ["azul"], "bico_tipo": "frugivoro"}
    )  # 2 sinais (média, baixa)
    r = linhas(
        ctx, [a, b], [obs(ctx, "p001", "sp_a", tamanho=["grande"])]
    )  # conflito sem ficha: ignorado
    assert [x["prioridade"] for x in r] == sorted(
        (x["prioridade"] for x in r), key=rev.ORDEM_PRIORIDADE.get
    )
    assert r[0]["species_id"] == "sp_b"  # mais sinais primeiro dentro da mesma prioridade


def test_sessao_vira_observacao_da_especie_confirmada_ou_esperada(ctx):
    voc, regras_bico, *_ = ctx
    atributos = validar_atributos({"cores": ["azul"]}, voc, regras_bico)
    texto = (
        '{"id": "ab12", "confirmada": "sp_c", "esperada": "sp_e", "descricoes": ["uma ave azul"]}'
    )
    o = rev.sessao_como_observacoes(texto, atributos)
    assert (o.species_id, o.origem, o.de_pessoa, o.trecho) == (
        "sp_c",
        "sessão ab12",
        True,
        "uma ave azul",
    )
    texto = '{"id": "cd34", "confirmada": null, "esperada": "sp_e", "descricoes": []}'
    assert rev.sessao_como_observacoes(texto, atributos).species_id == "sp_e"
    assert rev.sessao_como_observacoes('{"id": "x", "descricoes": []}', atributos) is None
    vazio = validar_atributos({}, voc, regras_bico)
    assert rev.sessao_como_observacoes(texto, vazio) is None


def test_csv_tem_as_colunas_com_bom_e_campos_para_preencher(ctx, tmp_path):
    r = linhas(ctx, [ficha(atributos={})])
    destino = rev.gravar_csv(r, tmp_path / "x" / "revisao.csv")
    assert destino.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM do Excel
    lidas = list(csv.DictReader(destino.open(encoding="utf-8-sig")))
    assert (
        list(lidas[0]) == rev.COLUNAS
        and lidas[0]["revisado"] == ""
        and lidas[0]["observacao"] == ""
    )
    assert "1 linhas em 1 espécies" in rev.resumo(r)[0]
