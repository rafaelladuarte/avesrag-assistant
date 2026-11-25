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


# Categorias da lista vermelha da IUCN, como a página mostra.
IUCN = {
    "LC": "Pouco preocupante",
    "NT": "Quase ameaçada",
    "VU": "Vulnerável",
    "EN": "Em perigo",
    "CR": "Criticamente em perigo",
    "EW": "Extinta na natureza",
    "EX": "Extinta",
    "DD": "Dados insuficientes",
}


def exibir_iucn(codigo: str) -> str:
    return IUCN.get(codigo, codigo)


def exibir_massa(gramas: float) -> str:
    """62,9 g; 1,2 kg a partir de 1000 g (vírgula decimal)."""
    if gramas >= 1000:
        return f"{gramas / 1000:.1f} kg".replace(".", ",")
    return (f"{gramas:.0f}" if gramas >= 100 else f"{gramas:.1f}").replace(".", ",") + " g"
