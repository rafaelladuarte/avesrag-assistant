"""Como os valores do vocabulário aparecem na página."""

from __future__ import annotations

# O vocabulário usa ids sem acento (`area_urbana`); a página mostra o texto com acento.
EXIBICAO = {
    "andando_no_chao": "andando no chão",
    "area_urbana": "área urbana",
    "balancando_a_cauda": "balançando a cauda",
    "beira_de_agua": "beira d'água",
    "cabeca": "cabeça",
    "cacando_em_voo": "caçando em voo",
    "cerrado_campo": "cerrado ou campo",
    "chao": "chão",
    "frugivoro": "frugívoro",
    "granivoro": "granívoro",
    "insetivoro": "insetívoro",
    "limicola_sondador": "limícola sondador",
    "mascara": "máscara",
    "medio": "médio",
    "meio_da_arvore": "meio da árvore",
    "na_agua": "na água",
    "nectarivoro": "nectarívoro",
    "parque_jardim": "parque ou jardim",
    "pasto_plantacao": "pasto ou plantação",
    "vegetacao_baixa": "vegetação baixa",
}


def exibir(valor: object) -> str:
    texto = str(valor)
    return EXIBICAO.get(texto) or texto.replace("_", " ")
