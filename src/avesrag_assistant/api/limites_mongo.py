"""Limites e uso diário no MongoDB, para várias instâncias do servidor (Vercel).

Mesma interface do `Limitador` e do `UsoDiario` em memória, mas os contadores ficam numa
collection e valem para todas as instâncias. As janelas expiram sozinhas (índice TTL).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from avesrag_assistant.api.limites import _dia_utc

COLECAO_LIMITES = "limites"
COLECAO_USO = "uso_diario"
COLUNAS_USO = ("sessoes", "mensagens", "chamadas_llm", "chamadas_embedding")


class LimitadorMongo:
    def __init__(
        self,
        db: Database,
        requisicoes_por_minuto: int,
        relogio: Callable[[], float] = time.time,
        dia: Callable[[], str] = _dia_utc,
    ):
        self.col = db[COLECAO_LIMITES]
        self.por_minuto = requisicoes_por_minuto
        self.relogio = relogio
        self.dia = dia

    def garantir_indices(self) -> None:
        self.col.create_index("expira_em", expireAfterSeconds=0, name="ttl_expira_em")

    def _expira(self, segundos: int) -> datetime:
        return datetime.fromtimestamp(self.relogio(), UTC) + timedelta(seconds=segundos)

    def permitir_ip(self, ip: str) -> bool:
        """Conta uma requisição do IP no minuto atual; False se passou do limite."""
        minuto = int(self.relogio() // 60)
        d = self.col.find_one_and_update(
            {"_id": f"ip:{ip}:{minuto}"},
            {"$inc": {"n": 1}, "$setOnInsert": {"expira_em": self._expira(120)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return d["n"] <= self.por_minuto

    def consumir_dia(self, chave: str, limite: int) -> bool:
        """Conta um uso de hoje para `chave`; False (sem contar) se já chegou em `limite`."""
        if limite <= 0:
            return False
        try:
            self.col.update_one(
                {"_id": f"dia:{self.dia()}:{chave}", "n": {"$lt": limite}},
                {"$inc": {"n": 1}, "$setOnInsert": {"expira_em": self._expira(3 * 86400)}},
                upsert=True,
            )
        except DuplicateKeyError:  # o documento existe e já chegou no limite
            return False
        return True

    def usado_hoje(self, chave: str) -> int:
        d = self.col.find_one({"_id": f"dia:{self.dia()}:{chave}"})
        return d["n"] if d else 0


class UsoDiarioMongo:
    def __init__(self, db: Database, dia: Callable[[], str] = _dia_utc):
        self.col = db[COLECAO_USO]
        self.dia = dia

    def registrar(
        self,
        sessoes: int = 0,
        mensagens: int = 0,
        chamadas_llm: int = 0,
        chamadas_embedding: int = 0,
    ) -> None:
        novos = {
            "sessoes": sessoes,
            "mensagens": mensagens,
            "chamadas_llm": chamadas_llm,
            "chamadas_embedding": chamadas_embedding,
        }
        if any(novos.values()):
            self.col.update_one(
                {"_id": self.dia()}, {"$inc": {k: v for k, v in novos.items() if v}}, upsert=True
            )

    def hoje(self) -> dict[str, int]:
        d = self.col.find_one({"_id": self.dia()}) or {}
        return {c: int(d.get(c, 0)) for c in COLUNAS_USO}
