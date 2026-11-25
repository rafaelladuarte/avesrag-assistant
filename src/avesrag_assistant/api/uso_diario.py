"""Resumo diário de uso (plano_assistant4.md W9): `reports/uso.tsv`, uma linha por dia.

Guarda quantas sessões e mensagens houve e quantas chamadas ao LLM e ao embedding
elas causaram. O arquivo é relido ao subir, então reiniciar o servidor não zera o dia.
"""

from __future__ import annotations

import csv
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from avesrag_assistant.api.limites import _dia_utc

COLUNAS = ["dia", "sessoes", "mensagens", "chamadas_llm", "chamadas_embedding"]


class UsoDiario:
    def __init__(self, path: Path, dia: Callable[[], str] = _dia_utc):
        self.path = Path(path)
        self.dia = dia
        self._trava = threading.Lock()
        self._linhas: dict[str, dict[str, int]] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8", newline="") as fh:
                for linha in csv.DictReader(fh, delimiter="\t"):
                    self._linhas[linha["dia"]] = {c: int(linha[c]) for c in COLUNAS[1:]}

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
        with self._trava:
            hoje = self._linhas.setdefault(self.dia(), dict.fromkeys(COLUNAS[1:], 0))
            for nome, valor in novos.items():
                hoje[nome] += valor
            self._gravar()

    def _gravar(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".uso.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, delimiter="\t")
                w.writerow(COLUNAS)
                for dia in sorted(self._linhas):
                    w.writerow([dia, *(self._linhas[dia][c] for c in COLUNAS[1:])])
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def hoje(self) -> dict[str, int]:
        with self._trava:
            return dict(self._linhas.get(self.dia(), dict.fromkeys(COLUNAS[1:], 0)))
