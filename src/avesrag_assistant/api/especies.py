"""Fotos e ficha pública de uma espécie (plano_assistant4.md §5.4).

A ficha mostra só atributos e mídia com licença aberta: nada do texto do
WikiAves (A11), nada do `_meta` nem da evidência da extração, e a foto do
WikiAves nunca aparece (B8). O WikiAves entra só como link.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from avesrag_builder.db.collections import SPECIES
from pymongo.database import Database

from avesrag_assistant.api.exibicao import exibir as _texto
from avesrag_assistant.api.guardrails import conferir_especie

RODAPE_ILUSTRACAO = "Ilustração gerada por IA · Bird Album · CC BY 4.0"


def _lista(valores: Sequence[Any] | None) -> str:
    return ", ".join(_texto(v) for v in valores or [])


def fotos(db: Database, species_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """species_id → {url, credito, link}, das espécies que têm `midia.foto`."""
    cursor = db[SPECIES].find(
        {"species_id": {"$in": list(species_ids)}}, {"_id": 0, "species_id": 1, "midia.foto": 1}
    )
    saida = {}
    for d in cursor:
        foto = (d.get("midia") or {}).get("foto")
        if foto:
            saida[d["species_id"]] = {
                "url": foto["url"],
                "credito": foto["credito"],
                "link": foto["link"],
            }
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
    familia = (doc.get("taxonomia") or {}).get("familia")
    if familia:
        itens.append({"rotulo": "Família", "valor": familia})
    if atributos.get("ambiente"):
        itens.append({"rotulo": "Ambiente", "valor": _lista(atributos["ambiente"])})
    biomas = (doc.get("ocorrencia") or {}).get("biomas")
    if biomas:
        itens.append({"rotulo": "Biomas", "valor": _lista(biomas)})
    return itens


def ficha_publica(db: Database, species_id: str) -> dict[str, Any] | None:
    doc = db[SPECIES].find_one(
        {"species_id": species_id},
        {
            "_id": 0,
            "species_id": 1,
            "nome_cientifico": 1,
            "nomes.pt": 1,
            "taxonomia.familia": 1,
            "atributos": 1,
            "ocorrencia.biomas": 1,
            "midia": 1,
        },
    )
    if doc is None:
        return None
    midia = doc.get("midia") or {}
    foto = midia.get("foto")
    ilustracao = midia.get("ilustracao")
    saida = {
        "species_id": doc["species_id"],
        "nome": ((doc.get("nomes") or {}).get("pt") or [doc["nome_cientifico"]])[0],
        "nome_cientifico": doc["nome_cientifico"],
        "familia": (doc.get("taxonomia") or {}).get("familia"),
        "foto": (
            {"url": foto["url"], "credito": foto["credito"], "link": foto["link"]} if foto else None
        ),
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
