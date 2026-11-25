"""Fotos e ficha pública de uma espécie (plano_assistant4.md §5.4).

A ficha mostra só atributos e mídia com licença aberta: nada do texto do
WikiAves (A11), nada do `_meta` nem da evidência da extração, e a foto do
WikiAves nunca aparece (B8). O WikiAves entra só como link.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from avesrag_builder.db.collections import SPECIES
from pymongo.database import Database

from avesrag_assistant.api.exibicao import exibir as _texto
from avesrag_assistant.api.exibicao import exibir_iucn, exibir_massa
from avesrag_assistant.api.guardrails import conferir_catalogo, conferir_especie

RODAPE_ILUSTRACAO = "Ilustração gerada por IA · Bird Album · CC BY 4.0"


def _lista(valores: Sequence[Any] | None) -> str:
    return ", ".join(_texto(v) for v in valores or [])


def _foto(f: dict[str, Any] | None, miniatura: bool = False) -> dict[str, str] | None:
    """O que a página precisa de uma foto: imagem, crédito e link da origem."""
    if not f:
        return None
    url = f["url"]
    if miniatura and "inaturalist-open-data" in url:
        url = url.replace("/medium.", "/small.")  # 240 px, para as listas
    return {"url": url, "credito": f["credito"], "link": f["link"]}


def fotos(db: Database, species_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """species_id → {url, credito, link}, das espécies que têm `midia.foto`."""
    cursor = db[SPECIES].find(
        {"species_id": {"$in": list(species_ids)}}, {"_id": 0, "species_id": 1, "midia.foto": 1}
    )
    saida = {}
    for d in cursor:
        foto = _foto((d.get("midia") or {}).get("foto"))
        if foto:
            saida[d["species_id"]] = foto
    return saida


def _como_reconhecer(atributos: dict[str, Any]) -> list[dict[str, str]]:
    itens = []
    if atributos.get("cores"):
        itens.append({"rotulo": "Cores", "valor": _lista(atributos["cores"])})
    for p in atributos.get("cores_por_parte") or []:
        itens.append({"rotulo": _texto(p["parte"]).capitalize(), "valor": _texto(p["cor"])})
    if atributos.get("marcas"):
        itens.append({"rotulo": "Marcas", "valor": _lista(atributos["marcas"])})
    if atributos.get("cauda"):
        itens.append({"rotulo": "Cauda", "valor": _lista(atributos["cauda"])})
    return itens


def _ficha(doc: dict[str, Any]) -> list[dict[str, str]]:
    atributos = doc.get("atributos") or {}
    itens = []
    tamanho = _lista(atributos.get("tamanho"))
    if atributos.get("comprimento_cm"):
        cm = f"{atributos['comprimento_cm']:g} cm"
        tamanho = f"{tamanho} ({cm})" if tamanho else cm
    if tamanho:
        itens.append({"rotulo": "Tamanho", "valor": tamanho})
    perfil = doc.get("perfil") or {}
    if perfil.get("massa_g"):
        itens.append({"rotulo": "Peso", "valor": exibir_massa(perfil["massa_g"])})
    if perfil.get("dieta"):
        itens.append({"rotulo": "Dieta", "valor": perfil["dieta"].capitalize()})
    taxonomia = doc.get("taxonomia") or {}
    if taxonomia.get("familia"):
        itens.append({"rotulo": "Família", "valor": taxonomia["familia"]})
    if taxonomia.get("ordem"):
        itens.append({"rotulo": "Ordem", "valor": taxonomia["ordem"]})
    if atributos.get("ambiente"):
        itens.append({"rotulo": "Ambiente", "valor": _lista(atributos["ambiente"])})
    biomas = (doc.get("ocorrencia") or {}).get("biomas")
    if biomas:
        itens.append({"rotulo": "Biomas", "valor": _lista(biomas)})
    if doc.get("iucn"):
        itens.append({"rotulo": "Conservação", "valor": exibir_iucn(doc["iucn"])})
    return itens


def ficha_publica(db: Database, species_id: str) -> dict[str, Any] | None:
    doc = db[SPECIES].find_one(
        {"species_id": species_id},
        {
            "_id": 0,
            "species_id": 1,
            "nome_cientifico": 1,
            "nomes": 1,
            "taxonomia": 1,
            "iucn": 1,
            "atributos": 1,
            "perfil": 1,
            "ocorrencia.biomas": 1,
            "midia": 1,
        },
    )
    if doc is None:
        return None
    midia = doc.get("midia") or {}
    ilustracao = midia.get("ilustracao")
    galeria = midia.get("fotos") or ([midia["foto"]] if midia.get("foto") else [])
    saida = {
        "species_id": doc["species_id"],
        "nome": ((doc.get("nomes") or {}).get("pt") or [doc["nome_cientifico"]])[0],
        "nome_en": ((doc.get("nomes") or {}).get("en") or [None])[0],
        "nome_cientifico": doc["nome_cientifico"],
        "familia": (doc.get("taxonomia") or {}).get("familia"),
        "foto": _foto(midia.get("foto")),
        "fotos": [_foto(f) for f in galeria],
        "ilustracao": (
            {
                "url": ilustracao["url"],
                "credito": ilustracao["credito"],
                "link": ilustracao["link"],
                "rotulo": RODAPE_ILUSTRACAO,
            }
            if ilustracao
            else None
        ),
        "como_reconhecer": _como_reconhecer(doc.get("atributos") or {}),
        "ficha": _ficha(doc),
        "url_wikiaves": midia.get("url_wikiaves"),
    }
    return conferir_especie(saida)


def catalogo(db: Database, familias: Mapping[str, str]) -> list[dict[str, Any]]:
    """As espécies aptas, para o "Explorar aves": nome, família e uma miniatura, por ordem de nome."""
    cursor = db[SPECIES].find(
        {"_meta.dq_status": "apta"},
        {"_id": 0, "species_id": 1, "nome_cientifico": 1, "nomes.pt": 1, "taxonomia.familia": 1}
        | {"midia.foto": 1},
    )
    itens = []
    for d in cursor:
        familia = (d.get("taxonomia") or {}).get("familia") or ""
        itens.append(
            {
                "species_id": d["species_id"],
                "nome": ((d.get("nomes") or {}).get("pt") or [d["nome_cientifico"]])[0],
                "nome_cientifico": d["nome_cientifico"],
                "familia": familia,
                "familia_popular": familias.get(familia, familia),
                "foto": _foto((d.get("midia") or {}).get("foto"), miniatura=True),
            }
        )
    itens.sort(key=lambda i: (i["nome"].casefold(), i["nome_cientifico"]))
    return conferir_catalogo(itens)
