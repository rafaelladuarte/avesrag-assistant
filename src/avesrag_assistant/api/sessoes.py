"""Onde as sessões da API ficam (plano_assistant4.md §6).

Duas implementações da mesma interface: arquivo (o que o `conversar` e o
`moderar` usam) e MongoDB (`conversations`, com TTL no campo `expira_em`). O
formato é o do `EstadoConversa` nas duas.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError
from pymongo.database import Database

from avesrag_assistant.conversa import estado as arquivos
from avesrag_assistant.conversa.estado import EstadoConversa, agora

CONVERSATIONS = "conversations"

log = logging.getLogger(__name__)


class RepositorioSessoes(Protocol):
    def carregar(self, sessao: str) -> EstadoConversa:
        """Levanta FileNotFoundError se não existe (ou já expirou)."""
        ...

    def salvar(self, estado: EstadoConversa) -> None: ...

    def todas(self) -> list[EstadoConversa]:
        """Todas as sessões que existem (para o relatório); as ilegíveis ficam de fora."""
        ...

    def ok(self) -> bool: ...


class RepositorioArquivo:
    def __init__(self, diretorio: Path):
        self.diretorio = Path(diretorio)

    def carregar(self, sessao: str) -> EstadoConversa:
        return arquivos.carregar(self.diretorio, sessao)

    def salvar(self, estado: EstadoConversa) -> None:
        arquivos.salvar(estado, self.diretorio)

    def todas(self) -> list[EstadoConversa]:
        estados = []
        for path in sorted(self.diretorio.glob("*.json")) if self.diretorio.exists() else []:
            try:
                estados.append(EstadoConversa.model_validate_json(path.read_text(encoding="utf-8")))
            except ValidationError:
                log.warning("sessão ilegível ignorada: %s", path.name)
        return estados

    def ok(self) -> bool:
        return True


class RepositorioMongo:
    def __init__(self, db: Database, ttl_dias: int):
        self.colecao = db[CONVERSATIONS]
        self.ttl = timedelta(days=ttl_dias)

    def garantir_indices(self) -> None:
        """TTL: o Mongo apaga a sessão quando `expira_em` passa."""
        self.colecao.create_index("expira_em", expireAfterSeconds=0, name="ttl_expira_em")

    def carregar(self, sessao: str) -> EstadoConversa:
        doc = self.colecao.find_one({"_id": sessao})
        if doc is None:
            raise FileNotFoundError(f"sessão {sessao!r} não encontrada")
        doc.pop("_id")
        doc.pop("expira_em", None)
        return EstadoConversa.model_validate(doc)

    def salvar(self, estado: EstadoConversa) -> None:
        estado.atualizada_em = agora()
        doc = estado.model_dump()
        doc["expira_em"] = estado.atualizada_em + self.ttl
        self.colecao.replace_one({"_id": estado.id}, {"_id": estado.id, **doc}, upsert=True)

    def todas(self) -> list[EstadoConversa]:
        estados = []
        for doc in self.colecao.find({}):
            doc = dict(doc)
            sessao = doc.pop("_id", "?")
            doc.pop("expira_em", None)
            try:
                estados.append(EstadoConversa.model_validate(doc))
            except ValidationError:
                log.warning("sessão ilegível ignorada: %s", sessao)
        return estados

    def ok(self) -> bool:
        try:
            self.colecao.database.client.admin.command("ping")
            return True
        except Exception:
            return False
