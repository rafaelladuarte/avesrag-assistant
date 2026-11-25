"""Contador de chamadas externas do processo (plano_assistant4.md §4.2, W9).

Os dois caches (LLM e embeddings) somam aqui cada chamada que chega ao fornecedor.
A API usa o contador para o teto diário e para guardar, em cada sessão, quantas
chamadas ela causou. Com várias mensagens em paralelo a diferença por mensagem é
aproximada, mas o total do processo é exato.
"""

from __future__ import annotations

import threading


class Contador:
    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._llm = 0
        self._embeddings = 0

    def mais_llm(self) -> None:
        with self._trava:
            self._llm += 1

    def mais_embedding(self) -> None:
        with self._trava:
            self._embeddings += 1

    @property
    def llm(self) -> int:
        with self._trava:
            return self._llm

    @property
    def embeddings(self) -> int:
        with self._trava:
            return self._embeddings


CONTADOR = Contador()
