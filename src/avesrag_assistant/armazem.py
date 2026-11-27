"""Onde os caches (LLM e embeddings) guardam as respostas: arquivo ou MongoDB.

No computador da usuária o arquivo em `reports/` basta (um processo só, gravação atômica).
Num ambiente serverless (Vercel) o disco não persiste e há várias instâncias ao mesmo tempo:
o `ArmazemMongo` guarda cada resposta numa collection, com `_id` = a chave do cache.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Protocol

from pymongo import ReplaceOne
from pymongo.database import Database


class Armazem(Protocol):
    def obter(self, chaves: list[str]) -> dict[str, Any]:
        """Os valores guardados para as chaves que existem (as outras ficam de fora)."""

    def gravar(self, itens: dict[str, Any]) -> None:
        """Guarda os itens (chave -> valor), trocando os que já existem."""


_TRAVAS: dict[Path, threading.Lock] = {}
_TRAVAS_TRAVA = threading.Lock()


def _trava_de(path: Path) -> threading.Lock:
    """Uma trava por arquivo, para todas as instâncias que o usam (extrator, redator, sugestor)."""
    with _TRAVAS_TRAVA:
        return _TRAVAS.setdefault(path, threading.Lock())


class ArmazemArquivo:
    """Um JSON `{chave: valor}`, lido uma vez e regravado de forma atômica a cada gravação."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._trava = _trava_de(self.path)
        self._dados: dict[str, Any] | None = None

    def _carregar(self) -> dict[str, Any]:
        if self._dados is None:
            if self.path.exists():
                self._dados = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self._dados = {}
        return self._dados

    def obter(self, chaves: list[str]) -> dict[str, Any]:
        with self._trava:
            dados = self._carregar()
            return {c: dados[c] for c in chaves if c in dados}

    def gravar(self, itens: dict[str, Any]) -> None:
        with self._trava:
            dados = self._carregar()
            dados.update(itens)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)


class ArmazemMongo:
    """Uma collection com `_id` = chave e `valor`; vale para todas as instâncias do servidor."""

    def __init__(self, db: Database, colecao: str):
        self.col = db[colecao]

    def obter(self, chaves: list[str]) -> dict[str, Any]:
        if not chaves:
            return {}
        return {d["_id"]: d["valor"] for d in self.col.find({"_id": {"$in": chaves}})}

    def gravar(self, itens: dict[str, Any]) -> None:
        if itens:
            self.col.bulk_write(
                [
                    ReplaceOne({"_id": c}, {"_id": c, "valor": v}, upsert=True)
                    for c, v in itens.items()
                ],
                ordered=False,
            )


def armazem_do_ambiente(settings: Any, db: Database | None, colecao: str, arquivo: str) -> Armazem:
    """O armazém que o ambiente pede (`settings.armazenamento`): a collection ou o arquivo."""
    if settings.armazenamento == "mongo":
        if db is None:
            raise ValueError("armazenamento mongo precisa do banco")
        return ArmazemMongo(db, colecao)
    return ArmazemArquivo(settings.REPORTS_DIR / arquivo)
