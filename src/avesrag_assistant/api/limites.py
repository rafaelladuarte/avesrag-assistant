"""Limites de uso em memória (plano_assistant4.md §4.2).

Há um processo só, então os contadores ficam na memória e somem ao reiniciar,
o que basta para um beta fechado. O relógio é injetável, para os testes.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime


def _dia_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class Limitador:
    def __init__(
        self,
        requisicoes_por_minuto: int,
        relogio: Callable[[], float] = time.monotonic,
        dia: Callable[[], str] = _dia_utc,
    ):
        self.por_minuto = requisicoes_por_minuto
        self.relogio = relogio
        self.dia = dia
        self._trava = threading.Lock()
        self._janelas: dict[str, deque[float]] = defaultdict(deque)
        self._dias: dict[tuple[str, str], int] = defaultdict(int)

    def permitir_ip(self, ip: str) -> bool:
        """Conta uma requisição do IP na janela de 60 s; False se passou do limite."""
        agora = self.relogio()
        with self._trava:
            janela = self._janelas[ip]
            while janela and agora - janela[0] >= 60:
                janela.popleft()
            if len(janela) >= self.por_minuto:
                return False
            janela.append(agora)
            return True

    def consumir_dia(self, chave: str, limite: int) -> bool:
        """Conta um uso de hoje para `chave`; False (sem contar) se já chegou em `limite`."""
        with self._trava:
            k = (self.dia(), chave)
            if self._dias[k] >= limite:
                return False
            self._dias[k] += 1
            return True

    def usado_hoje(self, chave: str) -> int:
        with self._trava:
            return self._dias[(self.dia(), chave)]
