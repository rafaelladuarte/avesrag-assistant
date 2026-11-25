"""Onde as sessões da API ficam (plano_assistant4.md §6).

Duas implementações da mesma interface: arquivo (o que o `conversar` e o
`moderar` usam) e MongoDB (`conversations`, com TTL no campo `expira_em`). O
formato é o do `EstadoConversa` nas duas.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Protocol

from pymongo.database import Database

from avesrag_assistant.conversa import estado as arquivos
from avesrag_assistant.conversa.estado import EstadoConversa, agora

CONVERSATIONS = "conversations"


class RepositorioSessoes(Protocol):
    def carregar(self, sessao: str) -> EstadoConversa:
        """Levanta FileNotFoundError se não existe (ou já expirou)."""
        ...

    def salvar(self, estado: EstadoConversa) -> None: ...

    def ok(self) -> bool: ...


class RepositorioArquivo:
    def __init__(self, diretorio: Path):
        self.diretorio = Path(diretorio)

    def carregar(self, sessao: str) -> EstadoConversa:
        return arquivos.carregar(self.diretorio, sessao)

    def salvar(self, estado: EstadoConversa) -> None:
        arquivos.salvar(estado, self.diretorio)

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

    def ok(self) -> bool:
        try:
            self.colecao.database.client.admin.command("ping")
            return True
        except Exception:
            return False
