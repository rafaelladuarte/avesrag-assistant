"""Métricas e relatório da avaliação: top 1, top 3, top 10 e posição mediana.

Os grupos do relatório são os casos alcançáveis de calibração, de teste, todos
os alcançáveis e os alcançáveis sem os gerados por LLM (`origem: llm`). Os
inalcançáveis (espécie sem documento de busca) ficam listados à parte.
"""

from __future__ import annotations

import csv
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from avesrag_assistant.avaliacao.rodar import Avaliacao, ResultadoCaso

TOPS = (1, 3, 10)


@dataclass(frozen=True)
class Metricas:
    n: int
    tops: dict[int, float]  # fração dos casos com a esperada até a posição k
    mediana: float | None  # None: a mediana caiu fora das posições medidas


def metricas(posicoes: Sequence[int | None]) -> Metricas:
    n = len(posicoes)
    if n == 0:
        return Metricas(0, {k: 0.0 for k in TOPS}, None)
    tops = {k: sum(1 for p in posicoes if p is not None and p <= k) / n for k in TOPS}
    mediana = statistics.median([math.inf if p is None else p for p in posicoes])
    return Metricas(n, tops, None if math.isinf(mediana) else float(mediana))


GRUPOS: dict[str, Callable[[ResultadoCaso], bool]] = {
    "calibracao": lambda r: r.alcancavel and r.caso.uso == "calibracao",
    "teste": lambda r: r.alcancavel and r.caso.uso == "teste",
    "alcancaveis": lambda r: r.alcancavel,
    "sem_llm": lambda r: r.alcancavel and r.caso.origem != "llm",
}


def _fmt_mediana(m: Metricas, ate: int) -> str:
    if m.n == 0:
        return "-"
    if m.mediana is None:
        return f">{ate}"
    return f"{m.mediana:g}"


def tabela(av: Avaliacao) -> list[str]:
    cab = f"{'modo':<14} {'grupo':<12} {'n':>3}" + "".join(f" {'top' + str(k):>6}" for k in TOPS)
    linhas = [cab + f" {'mediana':>8}"]
    for modo in av.modos:
        for grupo, filtro in GRUPOS.items():
            m = metricas([r.posicoes[modo] for r in av.resultados if filtro(r)])
            tops = "".join(f" {m.tops[k]:>6.0%}" for k in TOPS)
            linhas.append(f"{modo:<14} {grupo:<12} {m.n:>3}{tops} {_fmt_mediana(m, av.ate):>8}")
    return linhas


def inalcancaveis(av: Avaliacao) -> list[str]:
    return [f"{r.caso.id} {r.caso.esperada}" for r in av.resultados if not r.alcancavel]


def gravar_tsv(av: Avaliacao, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(
            [
                "caso",
                "esperada",
                "uso",
                "origem",
                "alcancavel",
                *(f"pos_{m}" for m in av.modos),
                *(f"primeiras_{m}" for m in av.modos),
            ]
        )
        for r in av.resultados:
            w.writerow(
                [
                    r.caso.id,
                    r.caso.esperada,
                    r.caso.uso,
                    r.caso.origem,
                    "sim" if r.alcancavel else "nao",
                    *(r.posicoes[m] or f">{av.ate}" for m in av.modos),
                    *(" ".join(r.primeiras[m]) for m in av.modos),
                ]
            )
    return path
