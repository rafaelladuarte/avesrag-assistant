"""Curadoria da ficha: correções manuais, histórico e reversão (banco e refundição falsos)."""

import copy

import pytest
from avesrag_builder.db.collections import SPECIES, SPECIES_OVERRIDES
from avesrag_builder.fuse import overrides as ov

from avesrag_assistant.api import especies
from avesrag_assistant.api.curadoria import Curadoria, ErroCuradoria
from test_orquestrador import _banco


class ColecaoOverridesFalsa:
    """O que a curadoria usa de `species_overrides`: find_one e update_one com upsert."""

    def __init__(self):
        self.docs = {}

    def find_one(self, filtro, projecao=None):
        return copy.deepcopy(self.docs.get(filtro["species_id"]))

    def update_one(self, filtro, update, upsert=False):
        sid = filtro["species_id"]
        doc = self.docs.get(sid)
        if doc is None:
            assert upsert
            doc = self.docs[sid] = {"species_id": sid, "campos": {}, "historico": []}
        for chave, valor in update.get("$set", {}).items():
            doc["campos"][chave.split(".", 1)[1]] = valor
        for chave in update.get("$unset", {}):
            doc["campos"].pop(chave.split(".", 1)[1], None)
        if "$push" in update:
            doc["historico"].append(update["$push"]["historico"])


def foto_da_fonte(n):
    return {
        "url": f"https://inaturalist-open-data.s3.amazonaws.com/photos/{n}/medium.jpeg",
        "autor": f"Autor {n}",
        "licenca": "CC BY 4.0",
        "licenca_url": "https://creativecommons.org/licenses/by/4.0/",
        "fonte": "inaturalist",
        "link": f"https://www.inaturalist.org/photos/{n}",
        "credito": f"Foto: Autor {n} · CC BY 4.0 · iNaturalist",
    }


def candidatas_falsas(species_id):
    """O que o builder acharia nas fontes: 3 do Brasil e 1 de fora."""
    return [("brasil", foto_da_fonte(1)), ("brasil", foto_da_fonte(2)), ("brasil", foto_da_fonte(3)),
            ("mundo", foto_da_fonte(4))]  # fmt: skip


def banco_com_curadoria():
    banco = _banco()
    banco.colecoes[SPECIES_OVERRIDES] = ColecaoOverridesFalsa()
    originais = {d["species_id"]: copy.deepcopy(d) for d in banco[SPECIES].docs}
    banco.originais = originais  # os testes que mudam a ficha de partida também mudam o original
    return banco, originais


def refundir_falso(banco, originais, vocab):
    """Refaz a ficha como o fuse: parte do original e aplica as correções de agora."""

    def refundir(db, species_id):
        doc = copy.deepcopy(originais[species_id])
        campos = (db[SPECIES_OVERRIDES].find_one({"species_id": species_id}) or {}).get(
            "campos", {}
        )
        ov.aplicar(doc, {}, {ov.caminho_de(k): v for k, v in campos.items()}, vocab)
        atual = next(d for d in db[SPECIES].docs if d["species_id"] == species_id)
        atual.clear()
        atual.update(doc)

    return refundir


@pytest.fixture
def curadoria(vocab_regras):
    banco, originais = banco_com_curadoria()
    voc, regras = vocab_regras
    refundir = refundir_falso(banco, originais, voc)
    return (
        Curadoria(banco, voc, regras, refundir=refundir, candidatas=candidatas_falsas),
        banco,
    )


def campo(retorno, caminho):
    return next(c for c in retorno["campos"] if c["caminho"] == caminho)


def test_lista_todos_os_campos_editaveis_com_as_opcoes(curadoria):
    c, _ = curadoria
    r = c.campos("sp_a")
    assert [x["caminho"] for x in r["campos"]] == list(ov.EDITAVEIS)
    cores = campo(r, "atributos.cores")
    assert cores["tipo"] == "lista" and cores["valor"] == ["amarelo"]
    assert {"valor": "amarelo", "rotulo": "amarelo"} in cores["opcoes"]
    assert cores["manual"] is None and cores["automatico"] is None
    pares = campo(r, "atributos.cores_por_parte")
    assert {o["valor"] for o in pares["opcoes"]["partes"]} >= {"cabeca", "barriga"}
    assert {"valor": "cabeca", "rotulo": "cabeça"} in pares["opcoes"]["partes"]
    assert campo(r, "atributos.bico_tipo")["tipo"] == "escolha"
    bico = campo(r, "atributos.bico")
    assert bico["tipo"] == "bico" and bico["valor"] is None
    assert {"valor": "muito_longo", "rotulo": "muito longo"} in bico["opcoes"]["comprimento"]
    assert {o["valor"] for o in bico["opcoes"]["espessura"]} >= {"fino", "grosso"}
    assert campo(r, "descricao")["tipo"] == "texto_longo"


def test_corrigir_o_bico_medido_mantem_a_outra_dimensao(curadoria):
    c, banco = curadoria
    medido = {"comprimento": "longo", "espessura": "grosso"}
    atual = next(d for d in banco["species"].docs if d["species_id"] == "sp_a")
    for doc in (atual, banco.originais["sp_a"]):
        doc["atributos"]["bico"] = dict(medido)
    r = c.definir("sp_a", "atributos.bico", {"comprimento": "curto"}, "ana", "tucuruvi")
    assert campo(r, "atributos.bico")["manual"]["por"] == "ana"
    assert campo(r, "atributos.bico")["valor"] == {"comprimento": "curto", "espessura": "grosso"}
    with pytest.raises(ErroCuradoria):
        c.definir("sp_a", "atributos.bico", {"curvatura": "reto"}, "ana")


def test_duas_correcoes_parciais_do_bico_se_somam(curadoria):
    c, banco = curadoria
    medido = {"comprimento": "longo", "espessura": "grosso"}
    atual = next(d for d in banco["species"].docs if d["species_id"] == "sp_a")
    for doc in (atual, banco.originais["sp_a"]):
        doc["atributos"]["bico"] = dict(medido)
    c.definir("sp_a", "atributos.bico", {"comprimento": "curto"}, "ana")
    r = c.definir("sp_a", "atributos.bico", {"espessura": "fino"}, "ana")
    gravado = banco[SPECIES_OVERRIDES].docs["sp_a"]["campos"]["atributos/bico"]["valor"]
    assert gravado == {"comprimento": "curto", "espessura": "fino"}  # a 2ª não apagou a 1ª
    assert campo(r, "atributos.bico")["valor"] == {"comprimento": "curto", "espessura": "fino"}
    # Uma correção que repete a dimensão troca só aquela.
    c.definir("sp_a", "atributos.bico", {"comprimento": "medio"}, "ana")
    gravado = banco[SPECIES_OVERRIDES].docs["sp_a"]["campos"]["atributos/bico"]["valor"]
    assert gravado == {"comprimento": "medio", "espessura": "fino"}


def test_corrigir_um_campo_muda_a_ficha_e_guarda_o_historico(curadoria):
    c, banco = curadoria
    r = c.definir("sp_a", "atributos.cores", ["azul", "preto"], "ana", " cor errada ")
    cores = campo(r, "atributos.cores")
    assert cores["valor"] == ["preto", "azul"]  # na ordem do vocabulário
    assert cores["manual"]["por"] == "ana" and cores["manual"]["motivo"] == "cor errada"
    assert cores["automatico"] == ["amarelo"]
    assert banco[SPECIES].docs[0]["atributos"]["cores"] == ["preto", "azul"]
    historico = banco[SPECIES_OVERRIDES].docs["sp_a"]["historico"]
    assert [(h["acao"], h["anterior"], h["valor"]) for h in historico] == [
        ("definiu", ["amarelo"], ["preto", "azul"])
    ]


def test_corrigir_de_novo_mantem_o_valor_automatico_original(curadoria):
    c, _ = curadoria
    c.definir("sp_a", "atributos.cores", ["azul"], "ana")
    r = c.definir("sp_a", "atributos.cores", ["verde"], "ana")
    assert campo(r, "atributos.cores")["automatico"] == ["amarelo"]
    assert campo(r, "atributos.cores")["valor"] == ["verde"]


def test_remover_o_campo_da_ficha(curadoria):
    c, banco = curadoria
    r = c.definir("sp_a", "atributos.cores", None, "ana", "a ave não tem cor definida")
    assert campo(r, "atributos.cores")["valor"] is None
    assert "cores" not in banco[SPECIES].docs[0]["atributos"]
    assert banco[SPECIES_OVERRIDES].docs["sp_a"]["historico"][0]["acao"] == "removeu"


def test_reverter_volta_ao_valor_das_fontes(curadoria):
    c, banco = curadoria
    c.definir("sp_a", "atributos.cores", ["azul"], "ana")
    r = c.reverter("sp_a", "atributos.cores", "ana")
    cores = campo(r, "atributos.cores")
    assert cores["valor"] == ["amarelo"] and cores["manual"] is None
    assert banco[SPECIES].docs[0]["atributos"]["cores"] == ["amarelo"]
    assert [h["acao"] for h in banco[SPECIES_OVERRIDES].docs["sp_a"]["historico"]] == [
        "definiu",
        "reverteu",
    ]


def test_reverter_sem_correcao_e_erro(curadoria):
    c, _ = curadoria
    with pytest.raises(ErroCuradoria, match="não tem correção"):
        c.reverter("sp_a", "atributos.cores", "ana")
    with pytest.raises(ErroCuradoria, match="não é editável"):
        c.reverter("sp_a", "nomes.pt", "ana")


@pytest.mark.parametrize(
    ("caminho", "valor"),
    [
        ("atributos.cores", ["fucsia"]),
        ("atributos.cores", []),
        ("nomes.pt", ["x"]),
        ("perfil.massa_g", -3),
        ("descricao", " "),
    ],
)
def test_valor_invalido_nao_grava_nada(curadoria, caminho, valor):
    c, banco = curadoria
    with pytest.raises(ErroCuradoria):
        c.definir("sp_a", caminho, valor, "ana")
    assert banco[SPECIES_OVERRIDES].docs == {}


def test_especie_que_nao_existe(curadoria):
    c, _ = curadoria
    with pytest.raises(ErroCuradoria, match="Não encontrei"):
        c.definir("sp_zzz", "descricao", "Texto.", "ana")
    with pytest.raises(ErroCuradoria, match="Não encontrei"):
        c.campos("sp_zzz")


def textos(banco, **kw):
    return {x["chave"]: x for x in especies.ficha_publica(banco, "sp_a", **kw)["textos"]}


def test_a_descricao_escrita_aparece_na_ficha_publica(curadoria):
    c, banco = curadoria
    assert textos(banco, exibir_gerados=False) == {}
    c.definir("sp_a", "descricao", "  Ave comum em fios e antenas.  ", "ana")
    d = textos(banco, exibir_gerados=False)["descricao"]
    assert (d["texto"], d["origem"], d["titulo"]) == (
        "Ave comum em fios e antenas.",
        "manual",
        "Descrição",
    )


def test_correcao_do_peso_e_da_dieta(curadoria):
    c, banco = curadoria
    c.definir("sp_a", "perfil.massa_g", 70.04, "ana")
    r = c.definir("sp_a", "perfil.dieta", " Insetívora  e frugívora ", "ana")
    assert banco[SPECIES].docs[0]["perfil"] == {"massa_g": 70.0, "dieta": "Insetívora e frugívora"}
    assert campo(r, "perfil.massa_g")["manual"]["por"] == "ana"


# -- textos gerados (os que foram para o embedding) -----------------------------


def com_textos_gerados(banco):
    for dominio, texto in (
        ("aparencia", "Mede 20 cm. Barriga amarela."),
        ("comportamento_ambiente", "Vive em áreas urbanas."),
        ("voz_habitos", "O canto lembra bem-te-vi."),
    ):
        banco["species_retrieval_docs"].docs.append(
            {"species_id": "sp_a", "dominio": dominio, "nota": 0.1, "texto": texto}
        )


def test_sem_correcao_a_ficha_mostra_os_textos_gerados_com_a_origem(curadoria):
    _, banco = curadoria
    com_textos_gerados(banco)
    t = textos(banco)
    assert [(k, v["origem"]) for k, v in t.items()] == [
        ("descricao", "ia"),
        ("comportamento", "ia"),
        ("voz", "ia"),
    ]
    assert t["descricao"]["texto"] == "Mede 20 cm. Barriga amarela."
    assert t["voz"]["titulo"] == "Voz e hábitos"


def test_o_texto_escrito_vence_o_gerado_so_naquele_campo(curadoria):
    c, banco = curadoria
    com_textos_gerados(banco)
    c.definir("sp_a", "comportamento", "Texto da curadoria.", "ana")
    t = textos(banco)
    assert (t["comportamento"]["texto"], t["comportamento"]["origem"]) == (
        "Texto da curadoria.",
        "manual",
    )
    assert t["descricao"]["origem"] == "ia"


def test_com_a_exibicao_desligada_so_o_texto_manual_aparece(curadoria):
    c, banco = curadoria
    com_textos_gerados(banco)
    c.definir("sp_a", "voz", "Canta ao amanhecer.", "ana")
    assert list(textos(banco, exibir_gerados=False)) == ["voz"]


def test_a_edicao_oferece_o_texto_gerado_como_sugestao(curadoria):
    c, banco = curadoria
    com_textos_gerados(banco)
    r = c.campos("sp_a")
    d = campo(r, "descricao")
    assert d["valor"] is None and d["sugestao"] == "Mede 20 cm. Barriga amarela."
    assert campo(r, "atributos.cores")["sugestao"] is None
    r = c.definir("sp_a", "descricao", "Meu texto.", "ana")
    assert campo(r, "descricao")["sugestao"] == "Mede 20 cm. Barriga amarela."  # continua à mão


# -- fotos --------------------------------------------------------------------------


def banco_com_fotos(banco):
    foto = foto_da_fonte(1)
    midia = {"foto": foto, "fotos": [foto, foto_da_fonte(2)]}
    banco[SPECIES].docs[0]["midia"] = midia
    banco.originais["sp_a"]["midia"] = copy.deepcopy(midia)


def links(retorno):
    return [f["link"].rsplit("/", 1)[1] for f in campo(retorno, "midia.fotos")["valor"]]


def test_a_edicao_lista_as_fotos_da_ficha_e_as_disponiveis(curadoria):
    c, banco = curadoria
    banco_com_fotos(banco)
    f = campo(c.campos("sp_a"), "midia.fotos")
    assert f["tipo"] == "fotos" and [x["link"][-1] for x in f["valor"]] == ["1", "2"]
    assert [o["link"][-1] for o in f["opcoes"]["candidatas"]] == ["1", "2", "3", "4"]
    assert f["opcoes"]["candidatas"][3]["origem"] == "iNaturalist, fora do Brasil"
    assert f["opcoes"]["candidatas"][0]["url"].endswith("/small.jpeg")  # miniatura
    assert "CC BY 4.0" in f["opcoes"]["licencas"] and "CC BY-NC 4.0" not in f["opcoes"]["licencas"]
    assert set(f["valor"][0]) == {"url", "credito", "link"}  # só o que a página precisa


def test_com_uma_foto_so_a_lista_tem_a_principal(curadoria):
    c, banco = curadoria
    banco[SPECIES].docs[0]["midia"] = {"foto": foto_da_fonte(5)}
    assert links(c.campos("sp_a")) == ["5"]


def test_escolher_e_reordenar_as_fotos_pelos_links(curadoria):
    c, banco = curadoria
    banco_com_fotos(banco)
    pedido = [
        {"link": "https://www.inaturalist.org/photos/3"},
        {"link": "https://www.inaturalist.org/photos/1"},
    ]
    r = c.definir("sp_a", "midia.fotos", pedido, "ana", "a 3 é mais nítida")
    assert links(r) == ["3", "1"]
    midia = banco[SPECIES].docs[0]["midia"]
    assert midia["foto"]["link"].endswith("/3")  # a primeira vira a principal
    assert midia["foto"]["credito"] == "Foto: Autor 3 · CC BY 4.0 · iNaturalist"  # o servidor monta
    assert campo(r, "midia.fotos")["manual"]["por"] == "ana"
    assert [f["link"][-1] for f in campo(r, "midia.fotos")["automatico"]] == ["1", "2"]


def test_foto_nova_por_endereco_leva_o_credito_montado_no_servidor(curadoria):
    c, banco = curadoria
    banco_com_fotos(banco)
    nova = {"url": "https://exemplo.org/a.jpg", "autor": " Beltrana ", "licenca": "CC BY-SA 4.0",
            "link": "https://exemplo.org/pagina/a"}  # fmt: skip
    r = c.definir(
        "sp_a", "midia.fotos", [nova, {"link": "https://www.inaturalist.org/photos/1"}], "ana"
    )
    foto = banco[SPECIES].docs[0]["midia"]["foto"]
    assert (foto["fonte"], foto["credito"]) == ("manual", "Foto: Beltrana · CC BY-SA 4.0")
    assert foto["licenca_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert links(r) == ["a", "1"]
    # A foto nova continua disponível ao reeditar (vem da ficha), sem estar nas fontes.
    r = c.definir("sp_a", "midia.fotos", [{"link": "https://exemplo.org/pagina/a"}], "ana")
    assert links(r) == ["a"] and "fotos" not in banco[SPECIES].docs[0]["midia"]


@pytest.mark.parametrize(
    "pedido",
    [
        [{"link": "https://www.inaturalist.org/photos/999"}],
        [{"link": "x", "url": "y"}],
        ["https://www.inaturalist.org/photos/1"],
        [
            {
                "url": "https://e.org/a.jpg",
                "autor": "A",
                "licenca": "CC BY-NC 4.0",
                "link": "https://e.org/a",
            }
        ],
        [
            {
                "url": "http://e.org/a.jpg",
                "autor": "A",
                "licenca": "CC BY 4.0",
                "link": "https://e.org/a",
            }
        ],
        [
            {
                "url": "https://e.org/a.jpg",
                "autor": " ",
                "licenca": "CC BY 4.0",
                "link": "https://e.org/a",
            }
        ],
        [],
        None,
    ],
)
def test_pedido_de_foto_invalido_nao_grava(curadoria, pedido):
    c, banco = curadoria
    banco_com_fotos(banco)
    with pytest.raises(ErroCuradoria):
        c.definir("sp_a", "midia.fotos", pedido, "ana")
    assert banco[SPECIES_OVERRIDES].docs == {}


def test_reverter_as_fotos_volta_as_das_fontes(curadoria):
    c, banco = curadoria
    banco_com_fotos(banco)
    c.definir("sp_a", "midia.fotos", [{"link": "https://www.inaturalist.org/photos/3"}], "ana")
    r = c.reverter("sp_a", "midia.fotos", "ana")
    assert campo(r, "midia.fotos")["manual"] is None
    # (a fake refaz a partir do original, como o fuse faria)
    assert links(r) == ["1", "2"]
