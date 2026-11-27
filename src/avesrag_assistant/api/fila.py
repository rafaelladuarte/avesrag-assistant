"""Fila de revisão das fichas: as pendentes da planilha de revisão manual (só a curadoria vê).

O arquivo `avaliacao/fila_revisao.json` traz uma entrada por espécie, com os motivos da
revisão. A fila some sozinha com o que já foi corrigido: o motivo `sem_cores` sai quando a
ficha passa a ter cor, e a espécie sai quando não sobra motivo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from avesrag_builder.db.collections import SPECIES
from pymongo.database import Database

CONCLUIDAS = "revisao_fila_concluidas"  # espécies que a curadoria tirou da fila
COLECAO = "revisao_texto_wikiaves"  # temporária: o texto colado à mão, até a extração
MAX_TEXTO = 60_000

ARQUIVO = Path(__file__).resolve().parents[3] / "avaliacao" / "fila_revisao.json"


def pendentes(db: Database, arquivo: Path = ARQUIVO) -> list[dict[str, Any]]:
    if not arquivo.exists():
        return []
    entradas = json.loads(arquivo.read_text(encoding="utf-8"))
    docs = {
        d["species_id"]: d
        for d in db[SPECIES].find(
            {"species_id": {"$in": [e["species_id"] for e in entradas]}},
            {"_id": 0, "species_id": 1, "nome_cientifico": 1, "atributos.cores": 1},
        )
    }
    concluidas = {d["_id"] for d in db[CONCLUIDAS].find({}, {"_id": 1})}
    anexados = {
        d["_id"]: len(d.get("texto") or "")
        for d in db[COLECAO].find({"_id": {"$in": list(docs)}}, {"texto": 1})
    }
    saida = []
    for e in entradas:
        doc = docs.get(e["species_id"])
        if doc is None or e["species_id"] in concluidas:
            continue
        motivos = [
            m
            for m in e["motivos"]
            if not (m["tipo"] == "sem_cores" and (doc.get("atributos") or {}).get("cores"))
        ]
        if motivos:
            saida.append(
                {
                    **e,
                    "nome_cientifico": doc["nome_cientifico"],
                    "motivos": motivos,
                    "texto_chars": anexados.get(e["species_id"], 0),
                }
            )
    saida.sort(key=lambda e: (e.get("ordem", 0), e["nome"].casefold()))
    return saida


def ler_texto(db: Database, species_id: str) -> dict[str, Any]:
    d = db[COLECAO].find_one({"_id": species_id}) or {}
    return {
        "species_id": species_id,
        "texto": d.get("texto", ""),
        "atualizado_em": d.get("atualizado_em"),
        "atualizado_por": d.get("atualizado_por"),
    }


def gravar_texto(db: Database, species_id: str, texto: str, quem: str) -> dict[str, Any]:
    """Guarda (ou apaga, se vazio) o texto completo do WikiAves colado na revisão."""
    if db[SPECIES].count_documents({"species_id": species_id}, limit=1) == 0:
        raise ValueError("espécie não encontrada")
    texto = texto.strip()
    if len(texto) > MAX_TEXTO:
        raise ValueError(f"texto grande demais (máximo {MAX_TEXTO} caracteres)")
    if not texto:
        db[COLECAO].delete_one({"_id": species_id})
        return ler_texto(db, species_id)
    db[COLECAO].update_one(
        {"_id": species_id},
        {
            "$set": {
                "species_id": species_id,
                "texto": texto,
                "atualizado_por": quem,
                "atualizado_em": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        },
        upsert=True,
    )
    return ler_texto(db, species_id)


def concluir(db: Database, species_id: str, quem: str) -> dict[str, Any]:
    """Tira a espécie da fila (a revisão dela terminou)."""
    if db[SPECIES].count_documents({"species_id": species_id}, limit=1) == 0:
        raise ValueError("espécie não encontrada")
    db[CONCLUIDAS].update_one(
        {"_id": species_id},
        {
            "$set": {
                "species_id": species_id,
                "por": quem,
                "em": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        },
        upsert=True,
    )
    return {"species_id": species_id, "concluida": True}


def reabrir(db: Database, species_id: str) -> dict[str, Any]:
    """Desfaz o `concluir`: a espécie volta para a fila."""
    db[CONCLUIDAS].delete_one({"_id": species_id})
    return {"species_id": species_id, "concluida": False}
