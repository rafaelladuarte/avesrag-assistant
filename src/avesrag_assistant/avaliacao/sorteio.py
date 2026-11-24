"""Sorteio das aves e das fotos das sessões moderadas (plano_assistant2.md §14).

Regras:

- **Espécies elegíveis:** com documento de busca (alcançáveis) e fora da
  calibração do `casos.yaml` (a mesma espécie nunca em calibração e teste).
- **Faixas de popularidade:** tercis do número de fotos no WikiAves entre as
  elegíveis (comum, intermediária, pouco conhecida), como no formulário.
- **Sorteio:** por faixa, com semente, no máximo `max_por_familia` espécies da
  mesma família; ninguém escolhe à mão.
- **Fotos:** do iNaturalist tiradas no Brasil (`raw_inaturalist`), com licença
  aberta, e **nunca a mesma da ficha** (`midia.foto`), que aparece no cartão da
  candidata: se fosse igual, a pessoa só teria de achar a foto igual. Saem duas
  opções por espécie, para revisão; a primeira vai para `foto_url`.
"""

from __future__ import annotations

import csv
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from avesrag_builder.db.collections import SPECIES, SPECIES_REGISTRY, SPECIES_RETRIEVAL_DOCS
from pymongo.database import Database

FAIXAS = ("comum", "intermediaria", "pouco_conhecida")
LICENCAS = {"cc-by": "CC BY 4.0", "cc-by-sa": "CC BY-SA 4.0", "cc0": "CC0 1.0"}
COLUNAS = [
    "formulario",
    "ordem",
    "species_id",
    "nome",
    "nome_cientifico",
    "familia",
    "popularidade",
    "fotos_wikiaves",
    "foto_url",
    "foto_link",
    "foto_credito",
    "foto_licenca",
    "foto_local",
    "alternativa_url",
    "alternativa_link",
    "alternativa_credito",
]


@dataclass(frozen=True)
class Foto:
    id: int
    autor: str
    licenca: str  # "CC BY 4.0"
    local: str
    extensao: str = "jpg"  # a do arquivo no iNaturalist: jpg, jpeg ou png

    @property
    def url(self) -> str:
        return (
            f"https://inaturalist-open-data.s3.amazonaws.com/photos/{self.id}/large.{self.extensao}"
        )

    @property
    def link(self) -> str:
        return f"https://www.inaturalist.org/photos/{self.id}"

    @property
    def credito(self) -> str:
        return f"Foto: {self.autor} · {self.licenca} · iNaturalist"


@dataclass
class Especie:
    species_id: str
    nome: str
    nome_cientifico: str
    familia: str
    fotos_wikiaves: int
    fotos: list[Foto] = field(default_factory=list)  # elegíveis, já sem a da ficha
    faixa: str = ""


def id_da_foto(url: str | None) -> int | None:
    m = re.search(r"/photos/(\d+)/", url or "")
    return int(m.group(1)) if m else None


def fotos_elegiveis(fotos_brasil: Sequence[Mapping], id_da_ficha: int | None) -> list[Foto]:
    """As fotos com licença aberta e diferentes da foto da ficha, na ordem do iNaturalist."""
    saida = []
    for f in fotos_brasil:
        licenca = LICENCAS.get(str(f.get("licenca", "")).lower())
        if licenca is None or f.get("id") == id_da_ficha:
            continue
        extensao = (str(f.get("url") or "").rsplit(".", 1)[-1] or "jpg").lower()
        saida.append(
            Foto(
                int(f["id"]),
                f.get("autor") or f.get("login") or "?",
                licenca,
                f.get("local") or "",
                extensao if extensao in ("jpg", "jpeg", "png") else "jpg",
            )
        )
    return saida


def tercis(especies: Sequence[Especie]) -> None:
    """Põe a faixa em cada espécie, pelos tercis do número de fotos no WikiAves."""
    ordem = sorted(especies, key=lambda e: (-e.fotos_wikiaves, e.species_id))
    n = len(ordem)
    for i, e in enumerate(ordem):
        e.faixa = FAIXAS[min(3 * i // n, 2)] if n else ""


def sortear(
    especies: Sequence[Especie],
    por_faixa: Mapping[str, int],
    semente: int,
    max_por_familia: int = 2,
) -> list[Especie]:
    """Sorteio por faixa, com o limite por família; só espécies com 2 fotos elegíveis."""
    rng = random.Random(semente)
    por_familia: dict[str, int] = {}
    escolhidas: list[Especie] = []
    for faixa in FAIXAS:
        candidatas = sorted(
            (e for e in especies if e.faixa == faixa and len(e.fotos) >= 2),
            key=lambda e: e.species_id,
        )
        rng.shuffle(candidatas)
        n = 0
        for e in candidatas:
            if n == por_faixa.get(faixa, 0):
                break
            if por_familia.get(e.familia, 0) >= max_por_familia:
                continue
            por_familia[e.familia] = por_familia.get(e.familia, 0) + 1
            escolhidas.append(e)
            n += 1
    return escolhidas


def carregar_especies(db: Database, fora: set[str]) -> list[Especie]:
    """As espécies alcançáveis fora de `fora`, com as fotos do WikiAves e do iNaturalist."""
    ids = sorted(set(db[SPECIES_RETRIEVAL_DOCS].distinct("species_id", {})) - fora)
    fichas = {
        d["species_id"]: d
        for d in db[SPECIES].find(
            {"species_id": {"$in": ids}},
            {"species_id": 1, "nome_cientifico": 1, "nomes.pt": 1, "taxonomia": 1, "midia.foto": 1},
        )
    }
    chaves = {
        d["species_id"]: d.get("chaves") or {}
        for d in db[SPECIES_REGISTRY].find(
            {"species_id": {"$in": ids}}, {"species_id": 1, "chaves": 1}
        )
    }
    wa_ids = [c.get("wikiaves") for c in chaves.values() if c.get("wikiaves")]
    wikiaves = {
        d["_id"]: d["dados"]["lista"]
        for d in db["raw_wikiaves"].find({"_id": {"$in": wa_ids}}, {"dados.lista": 1})
    }
    especies = []
    for sid in ids:
        ficha = fichas.get(sid)
        lista = wikiaves.get(chaves.get(sid, {}).get("wikiaves"))
        if ficha is None or lista is None:
            continue
        inat = db["raw_inaturalist"].find_one(
            {"dados.taxon.nome": ficha["nome_cientifico"]}, {"dados.fotos_brasil": 1}
        )
        fotos_brasil = (inat or {}).get("dados", {}).get("fotos_brasil") or []
        foto_ficha = id_da_foto(((ficha.get("midia") or {}).get("foto") or {}).get("url"))
        especies.append(
            Especie(
                species_id=sid,
                nome=((ficha.get("nomes") or {}).get("pt") or [ficha["nome_cientifico"]])[0],
                nome_cientifico=ficha["nome_cientifico"],
                familia=(ficha.get("taxonomia") or {}).get("familia")
                or lista.get("familia")
                or "?",
                fotos_wikiaves=int(lista.get("fotos") or 0),
                fotos=fotos_elegiveis(fotos_brasil, foto_ficha),
            )
        )
    return especies


def gravar_csv(escolhidas: Sequence[Especie], path: Path, prefixo: str = "S") -> Path:
    """O CSV no formato do `especies_formulario.csv`, com o código S1, S2…"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUNAS, lineterminator="\n")
        w.writeheader()
        for i, e in enumerate(escolhidas, start=1):
            f1, f2 = e.fotos[0], e.fotos[1]
            w.writerow(
                {
                    "formulario": prefixo,
                    "ordem": i,
                    "species_id": e.species_id,
                    "nome": e.nome,
                    "nome_cientifico": e.nome_cientifico,
                    "familia": e.familia,
                    "popularidade": e.faixa,
                    "fotos_wikiaves": e.fotos_wikiaves,
                    "foto_url": f1.url,
                    "foto_link": f1.link,
                    "foto_credito": f1.credito,
                    "foto_licenca": f1.licenca,
                    "foto_local": f1.local,
                    "alternativa_url": f2.url,
                    "alternativa_link": f2.link,
                    "alternativa_credito": f2.credito,
                }
            )
    return path
