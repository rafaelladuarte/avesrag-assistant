"""Precisão e cobertura do extrator contra o gabarito (E1)."""

from avesrag_assistant.avaliacao.casos import Caso
from avesrag_assistant.avaliacao.extrator import comparar, diferencas, itens


def _caso(cid, atributos):
    return Caso.model_construct(
        id=cid,
        descricao="x",
        esperada="sp_a",
        autoria="t",
        origem="foto",
        uso="calibracao",
        atributos=atributos,
    )


def test_itens_de_cada_tipo(obs):
    a = obs(
        cores=["preto"],
        cores_por_parte=[{"parte": "bico", "cor": "laranja"}],
        bico={"comprimento": "curto", "extra": ["robusto"]},
    )
    assert itens(a) == {
        ("cores", "preto"),
        ("cores_por_parte", "bico:laranja"),
        ("bico", "comprimento:curto"),
        ("bico", "extra:robusto"),
    }


def test_comparar_precisao_e_cobertura(obs):
    casos = [
        _caso("p1", obs(cores=["preto", "branco"], marcas=["topete"])),
        _caso("p2", obs(cores=["azul"])),
        _caso("p3", obs(cores=["verde"])),  # sem extração: fica de fora
    ]
    extraidos = {"p1": obs(cores=["preto"], cauda=["longa"]), "p2": obs(cores=["azul"])}
    a = comparar(casos, extraidos)
    assert (a["cores"].extraidos, a["cores"].esperados, a["cores"].certos) == (2, 3, 2)
    assert a["marcas"].precisao is None and a["marcas"].cobertura == 0
    assert a["cauda"].precisao == 0 and a["cauda"].cobertura is None
    assert (a["total"].extraidos, a["total"].esperados, a["total"].certos) == (3, 4, 2)
    assert diferencas(casos[0], extraidos["p1"]) == (
        [("cauda", "longa")],
        [("cores", "branco"), ("marcas", "topete")],
    )
