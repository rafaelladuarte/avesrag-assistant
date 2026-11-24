"""Sorteio das aves e das fotos das sessões moderadas (funções puras)."""

import csv

from avesrag_assistant.avaliacao.sorteio import (
    Especie,
    Foto,
    fotos_elegiveis,
    gravar_csv,
    id_da_foto,
    sortear,
    tercis,
)
from avesrag_assistant.web.servidor import carregar_aves


def _foto(id_, licenca="cc-by", ext="jpeg"):
    return {
        "id": id_,
        "url": f"https://x/photos/{id_}/square.{ext}",
        "licenca": licenca,
        "autor": "Fulana",
        "local": "Uberlândia",
    }


def test_fotos_elegiveis_tiram_a_da_ficha_e_as_fechadas():
    fotos = [_foto(1), _foto(2, "cc-by-nc"), _foto(3, "cc0", "jpg"), _foto(4, "cc-by-sa")]
    saida = fotos_elegiveis(fotos, id_da_ficha=1)
    assert [f.id for f in saida] == [3, 4]
    assert saida[0].url.endswith("/photos/3/large.jpg")
    assert saida[1].credito == "Foto: Fulana · CC BY-SA 4.0 · iNaturalist"
    assert id_da_foto("https://x/photos/315086825/medium.jpg") == 315086825
    assert id_da_foto(None) is None


def _especie(sid, fotos_wa, familia="F", n_fotos=2):
    fotos = [Foto(i, "a", "CC BY 4.0", "") for i in range(n_fotos)]
    return Especie(sid, sid, sid, familia, fotos_wa, fotos)


def test_tercis():
    especies = [_especie(f"sp_{i}", 1000 * i) for i in range(1, 7)]
    tercis(especies)
    faixas = {e.species_id: e.faixa for e in especies}
    assert faixas["sp_6"] == faixas["sp_5"] == "comum"
    assert faixas["sp_4"] == faixas["sp_3"] == "intermediaria"
    assert faixas["sp_2"] == faixas["sp_1"] == "pouco_conhecida"


def test_sortear_respeita_faixa_familia_e_fotos():
    especies = [_especie(f"sp_{i}", 9000, familia="A") for i in range(5)]
    especies += [_especie("sp_b", 9000, familia="B"), _especie("sp_c", 9000, "C", n_fotos=1)]
    for e in especies:
        e.faixa = "comum"
    escolhidas = sortear(especies, {"comum": 4}, semente=1, max_por_familia=2)
    familias = [e.familia for e in escolhidas]
    assert familias.count("A") == 2 and "B" in familias
    assert "sp_c" not in [e.species_id for e in escolhidas]  # só uma foto elegível
    assert len(escolhidas) == 3  # não há 4 respeitando o limite
    # Mesma semente, mesmo sorteio.
    de_novo = sortear(especies, {"comum": 4}, semente=1, max_por_familia=2)
    assert [e.species_id for e in de_novo] == [e.species_id for e in escolhidas]


def test_gravar_csv_e_ler_na_pagina(tmp_path):
    e = _especie("sp_x", 5000)
    e.faixa = "intermediaria"
    path = gravar_csv([e], tmp_path / "aves.csv")
    linha = next(csv.DictReader(path.open(encoding="utf-8")))
    assert (linha["formulario"], linha["ordem"], linha["popularidade"]) == (
        "S",
        "1",
        "intermediaria",
    )
    aves = carregar_aves(path)
    assert list(aves) == ["S1"] and aves["S1"].species_id == "sp_x"


def test_csv_real_das_sessoes():
    from avesrag_assistant.settings import ROOT_DIR

    aves = carregar_aves(ROOT_DIR / "avaliacao" / "especies_sessoes.csv")
    assert len(aves) == 20
    assert all(a.foto_url.startswith("https://inaturalist-open-data") for a in aves.values())


def test_aves_das_sessoes_fora_da_calibracao(vocab_regras):
    from avesrag_assistant.avaliacao.casos import carregar_casos
    from avesrag_assistant.settings import ROOT_DIR

    casos = carregar_casos(ROOT_DIR / "avaliacao" / "casos.yaml", *vocab_regras)
    calibracao = {c.esperada for c in casos if c.uso == "calibracao"}
    aves = carregar_aves(ROOT_DIR / "avaliacao" / "especies_sessoes.csv")
    assert not {a.species_id for a in aves.values()} & calibracao
