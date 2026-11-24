"""Reordenação pelos atributos observados: bate, conflita, sem dado e a nota final."""

import pytest
import yaml
from avesrag_builder.core.models import Atributos

from avesrag_assistant.busca.pontuacao import (
    Entrada,
    RankingInvalido,
    carregar_ranking,
    melhores_por_atributos,
    normalizar,
    pontuar,
    reordenar,
)
from avesrag_assistant.settings import ROOT_DIR

PESOS = {
    "tamanho": 0.3,
    "cores": 0.4,
    "cores_por_parte": 0.6,
    "marcas": 0.4,
    "bico": 0.4,
    "cauda": 0.3,
    "ambiente": 0.2,
    "onde_estava": 0.2,
    "o_que_fazia": 0.2,
    "grupo": 0.1,
    "atividade": 0.3,
}
RANKING = {
    "versao": "teste",
    "vetor": {"peso": 1.0, "candidatos": 400, "documentos": 120, "especies": 40},
    "penalidade_conflito": 0.5,
    "pesos": PESOS,
}


def _gravar(tmp_path, dados, texto=None):
    path = tmp_path / "ranking.yaml"
    path.write_text(texto if texto is not None else yaml.safe_dump(dados), encoding="utf-8")
    return path


@pytest.fixture
def ranking(tmp_path):
    return carregar_ranking(_gravar(tmp_path, RANKING))


@pytest.fixture
def pontua(ranking, vocab_regras):
    _, regras = vocab_regras

    def _pontua(atributos, ficha):
        detalhes = pontuar(atributos, ficha, ranking, regras)
        assert len(detalhes) == 1
        return detalhes[0]

    return _pontua


# ---------------------------------------------------------------------------
# Listas
# ---------------------------------------------------------------------------


def test_lista_bate_total(obs, pontua):
    d = pontua(obs(cores=["amarelo", "preto"]), Atributos(cores=["amarelo", "preto", "branco"]))
    assert (d.resultado, d.pontos, d.bateram) == ("bate", pytest.approx(0.4), ["amarelo", "preto"])


def test_lista_bate_parcial_sem_penalidade(obs, pontua):
    d = pontua(obs(cores=["amarelo", "verde"]), Atributos(cores=["amarelo", "preto", "branco"]))
    assert d.resultado == "bate"
    assert d.pontos == pytest.approx(0.4 * 0.5)
    assert d.conflitaram == ["verde"]


def test_lista_conflita(obs, pontua):
    d = pontua(obs(marcas=["topete"]), Atributos(marcas=["sobrancelha"]))
    assert (d.resultado, d.pontos) == ("conflita", pytest.approx(-0.4 * 0.5))


def test_lista_sem_dado_e_neutra(obs, pontua):
    d = pontua(obs(cauda=["longa"]), Atributos(cores=["preto"]))
    assert (d.resultado, d.pontos) == ("sem_dado", 0.0)


# ---------------------------------------------------------------------------
# Tamanho
# ---------------------------------------------------------------------------


def test_tamanho_com_interseção_bate(obs, pontua):
    d = pontua(obs(tamanho=["medio"]), Atributos(tamanho=["pequeno", "medio"]))
    assert (d.resultado, d.pontos) == ("bate", pytest.approx(0.3))


def test_tamanho_com_duas_faixas_informadas_bate_inteiro(obs, pontua):
    d = pontua(obs(tamanho=["pequeno", "medio"]), Atributos(tamanho=["pequeno"]))
    assert d.pontos == pytest.approx(0.3)


def test_tamanho_sem_interseção_conflita(obs, pontua):
    d = pontua(obs(tamanho=["grande"]), Atributos(tamanho=["pequeno", "medio"]))
    assert d.resultado == "conflita"


# ---------------------------------------------------------------------------
# Cores por parte
# ---------------------------------------------------------------------------

BEM_TE_VI = Atributos(
    cores=["amarelo", "preto", "branco", "marrom"],
    cores_por_parte=[
        {"parte": "barriga", "cor": "amarelo"},
        {"parte": "cabeca", "cor": "preto"},
        {"parte": "cabeca", "cor": "branco"},
    ],
)


def _cpp(obs, parte, cor):
    return obs(cores_por_parte=[{"parte": parte, "cor": cor}])


def test_cor_por_parte_inteira(obs, pontua):
    d = pontua(_cpp(obs, "barriga", "amarelo"), BEM_TE_VI)
    assert (d.resultado, d.pontos, d.bateram) == ("bate", pytest.approx(0.6), ["barriga:amarelo"])


def test_cor_em_outra_parte_vale_meio(obs, pontua):
    d = pontua(_cpp(obs, "peito", "amarelo"), BEM_TE_VI)
    assert (d.resultado, d.pontos) == ("bate", pytest.approx(0.3))


def test_cor_so_em_cores_vale_meio(obs, pontua):
    d = pontua(_cpp(obs, "dorso", "marrom"), BEM_TE_VI)
    assert d.pontos == pytest.approx(0.3)


def test_parte_com_outra_cor_conflita(obs, pontua):
    d = pontua(_cpp(obs, "barriga", "branco"), BEM_TE_VI)
    assert (d.resultado, d.pontos) == ("conflita", pytest.approx(-0.3))


def test_cor_por_parte_sem_dado(obs, pontua):
    d = pontua(_cpp(obs, "dorso", "verde"), BEM_TE_VI)
    assert d.resultado == "sem_dado"


def test_cor_por_parte_mistura_media_dos_pares_com_dado(obs, pontua):
    a = obs(
        cores_por_parte=[
            {"parte": "barriga", "cor": "amarelo"},  # 1
            {"parte": "peito", "cor": "preto"},  # 0,5
            {"parte": "barriga", "cor": "branco"},  # conflito (0)
            {"parte": "dorso", "cor": "verde"},  # sem dado: fora da média
        ]
    )
    d = pontua(a, BEM_TE_VI)
    assert d.pontos == pytest.approx(0.6 * 1.5 / 3)


# ---------------------------------------------------------------------------
# Bico
# ---------------------------------------------------------------------------


def test_bico_compativel_com_o_perfil(obs, pontua):
    a = obs(bico={"comprimento": "medio", "curvatura": "reto", "extra": ["pontiagudo"]})
    d = pontua(a, Atributos(bico_tipo="generalista"))
    assert (d.resultado, d.pontos) == ("bate", pytest.approx(0.4))


def test_bico_incompativel_conflita(obs, pontua):
    d = pontua(obs(bico={"curvatura": "gancho"}), Atributos(bico_tipo="generalista"))
    assert (d.resultado, d.pontos, d.conflitaram) == (
        "conflita",
        pytest.approx(-0.2),
        ["curvatura:gancho"],
    )


def test_bico_compativel_em_parte(obs, pontua):
    d = pontua(
        obs(bico={"comprimento": "curto", "espessura": "fino"}), Atributos(bico_tipo="generalista")
    )
    assert d.resultado == "conflita"  # nenhuma das duas dimensões bate
    d = pontua(
        obs(bico={"comprimento": "medio", "espessura": "fino"}), Atributos(bico_tipo="generalista")
    )
    assert (d.resultado, d.pontos) == ("bate", pytest.approx(0.2))


def test_bico_extra_sem_perfil_e_neutro(obs, pontua):
    a = obs(bico={"espessura": "fino", "extra": ["pontiagudo"]})
    d = pontua(a, Atributos(bico_tipo="insetivoro"))  # o perfil não define extra
    assert (d.resultado, d.pontos) == ("bate", pytest.approx(0.4))


def test_bico_ficha_sem_bico_tipo(obs, pontua):
    d = pontua(obs(bico={"espessura": "grosso"}), Atributos(cores=["preto"]))
    assert d.resultado == "sem_dado"


# ---------------------------------------------------------------------------
# Nota final
# ---------------------------------------------------------------------------


def test_pontuar_sem_atributos(obs, ranking, vocab_regras):
    assert pontuar(obs(), BEM_TE_VI, ranking, vocab_regras[1]) == []


def test_normalizar():
    assert normalizar({"a": 0.7, "b": 0.8, "c": 0.75}) == pytest.approx(
        {"a": 0.0, "b": 1.0, "c": 0.5}
    )
    assert normalizar({"a": 0.7, "b": 0.7}) == {"a": 1.0, "b": 1.0}
    assert normalizar({}) == {}


def test_reordenar_atributos_tiram_do_primeiro_lugar(obs, ranking, vocab_regras):
    entradas = [
        Entrada("sp_a", 0.80, Atributos(cores=["verde"], marcas=["topete"])),
        Entrada("sp_b", 0.78, BEM_TE_VI.model_copy(update={"marcas": ["sobrancelha"]})),
        Entrada("sp_c", 0.70, Atributos(cores=["amarelo"])),
    ]
    a = obs(cores=["amarelo"], marcas=["sobrancelha"])
    saida = reordenar(entradas, a, ranking, vocab_regras[1])
    assert [p.species_id for p in saida] == ["sp_b", "sp_a", "sp_c"]
    b = saida[0]
    assert b.nota_vetor_normalizada == pytest.approx(0.8)
    assert b.pontos == pytest.approx(0.8)
    assert b.nota_final == pytest.approx(1.6)
    assert saida[1].nota_final == pytest.approx(1.0 - 0.2 - 0.2)  # sp_a: dois conflitos
    assert saida[2].nota_final == pytest.approx(0.0 + 0.4)  # sp_c: só a cor bate


def test_reordenar_desempata_pelo_species_id(obs, ranking, vocab_regras):
    entradas = [Entrada(sid, 0.7, Atributos()) for sid in ("sp_c", "sp_a", "sp_b")]
    saida = reordenar(entradas, obs(), ranking, vocab_regras[1])
    assert [p.species_id for p in saida] == ["sp_a", "sp_b", "sp_c"]


# ---------------------------------------------------------------------------
# config/ranking.yaml
# ---------------------------------------------------------------------------


def test_ranking_real_carrega():
    r = carregar_ranking(ROOT_DIR / "config" / "ranking.yaml")
    assert r.vetor.especies == 40


def _sem(dic, chave):
    return {k: v for k, v in dic.items() if k != chave}


@pytest.mark.parametrize(
    ("dados", "erro"),
    [
        ({**RANKING, "pesos": _sem(PESOS, "cauda")}, "faltam pesos para \\['cauda'\\]"),
        ({**RANKING, "pesos": {**PESOS, "bico_tipo": 0.1}}, "desconhecidos: \\['bico_tipo'\\]"),
        ({**RANKING, "pesos": {**PESOS, "cores": 0}}, "pesos.cores"),
        ({**RANKING, "pesos": {**PESOS, "cores": -0.1}}, "pesos.cores"),
        ({**RANKING, "penalidade_conflito": -1}, "penalidade_conflito"),
        ({**RANKING, "vetor": {**RANKING["vetor"], "especies": 200}}, "vetor: .*especies"),
        ({**RANKING, "extra": 1}, "extra"),
    ],
)
def test_ranking_invalido_e_recusado_com_o_caminho(tmp_path, dados, erro):
    with pytest.raises(RankingInvalido, match=erro):
        carregar_ranking(_gravar(tmp_path, dados))


def test_ranking_com_chave_duplicada_e_recusado(tmp_path):
    texto = yaml.safe_dump(RANKING) + "penalidade_conflito: 0.3\n"
    with pytest.raises(RankingInvalido, match="chave duplicada"):
        carregar_ranking(_gravar(tmp_path, None, texto))


# ---------------------------------------------------------------------------
# Seção hibrido
# ---------------------------------------------------------------------------

HIBRIDO = {
    "especies": 20,
    "niveis": [
        {"nome": "completo", "atributos": "todos"},
        {"nome": "cores", "atributos": ["cores", "cores_por_parte"]},
    ],
}


def test_ranking_sem_hibrido_e_valido(ranking):
    assert ranking.hibrido is None


def test_hibrido_valido_e_campos(tmp_path):
    r = carregar_ranking(_gravar(tmp_path, {**RANKING, "hibrido": HIBRIDO}))
    completo, cores = r.hibrido.niveis
    assert "bico" not in completo.campos() and "marcas" in completo.campos()
    assert cores.campos() == ("cores", "cores_por_parte")


@pytest.mark.parametrize(
    ("atributos", "mensagem"),
    [
        (["cor"], "atributos desconhecidos"),
        (["cores", "bico"], "não filtra"),
        (["cores", "cores"], "repetido"),
    ],
)
def test_hibrido_nivel_invalido(tmp_path, atributos, mensagem):
    hibrido = {"especies": 20, "niveis": [{"nome": "x", "atributos": atributos}]}
    with pytest.raises(RankingInvalido, match=mensagem):
        carregar_ranking(_gravar(tmp_path, {**RANKING, "hibrido": hibrido}))


def test_hibrido_niveis_com_o_mesmo_nome(tmp_path):
    nivel = {"nome": "x", "atributos": ["cores"]}
    hibrido = {"especies": 20, "niveis": [nivel, nivel]}
    with pytest.raises(RankingInvalido, match="mesmo nome"):
        carregar_ranking(_gravar(tmp_path, {**RANKING, "hibrido": hibrido}))


# ---------------------------------------------------------------------------
# Espécies pelos atributos (fonte do modo hibrido)
# ---------------------------------------------------------------------------

UNIVERSO = {
    "sp_a": Atributos(cores=["preto", "vermelho"]),
    "sp_b": Atributos(cores=["preto"]),
    "sp_c": Atributos(cores=["preto", "branco"]),
    "sp_d": Atributos(cores=["preto"], marcas=["topete"]),
}


def test_melhores_por_atributos(obs, ranking, vocab_regras):
    a = obs(cores=["preto"], marcas=["topete"])
    # sp_d bate as duas; as outras, só a cor (a marca é sem_dado); desempate pelo id.
    assert melhores_por_atributos(UNIVERSO, a, ranking, vocab_regras[1], 2) == ["sp_d", "sp_a"]
    assert melhores_por_atributos(UNIVERSO, obs(), ranking, vocab_regras[1], 2) == []
    assert melhores_por_atributos(UNIVERSO, a, ranking, vocab_regras[1], 0) == []
    # Só entram espécies com pontos > 0.
    assert melhores_por_atributos(UNIVERSO, obs(cores=["azul"]), ranking, vocab_regras[1], 4) == []


def test_hibrido_atributos_padrao_e_invalido(tmp_path):
    r = carregar_ranking(_gravar(tmp_path, {**RANKING, "hibrido": HIBRIDO}))
    assert r.hibrido.atributos == 0
    with pytest.raises(RankingInvalido, match="atributos"):
        carregar_ranking(_gravar(tmp_path, {**RANKING, "hibrido": {**HIBRIDO, "atributos": -1}}))
