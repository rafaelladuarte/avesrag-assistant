"""Acerto do extrator contra o gabarito do `casos.yaml` (plano_assistant2.md §3, E1).

Cada atributo vira itens comparáveis: um valor de lista, um par parte–cor, uma
dimensão do bico. Precisão é a fração do extraído que está no gabarito;
cobertura, a fração do gabarito que foi extraída.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from avesrag_assistant.avaliacao.casos import Caso
from avesrag_assistant.busca.modelos import ATRIBUTOS_LISTA, DIMENSOES_BICO, AtributosObservados

Item = tuple[str, str]  # (atributo, valor legível)


def itens(atributos: AtributosObservados) -> set[Item]:
    saida: set[Item] = set()
    for nome in ATRIBUTOS_LISTA:
        saida |= {(nome, v) for v in getattr(atributos, nome) or []}
    saida |= {("cores_por_parte", f"{p.parte}:{p.cor}") for p in atributos.cores_por_parte or []}
    if atributos.bico is not None:
        for dim in DIMENSOES_BICO:
            if valor := getattr(atributos.bico, dim):
                saida.add(("bico", f"{dim}:{valor}"))
        saida |= {("bico", f"extra:{v}") for v in atributos.bico.extra or []}
    return saida


@dataclass(frozen=True)
class Acerto:
    extraidos: int
    esperados: int
    certos: int

    @property
    def precisao(self) -> float | None:
        return self.certos / self.extraidos if self.extraidos else None

    @property
    def cobertura(self) -> float | None:
        return self.certos / self.esperados if self.esperados else None


def comparar(
    casos: Sequence[Caso], extraidos: Mapping[str, AtributosObservados]
) -> dict[str, Acerto]:
    """Acerto por atributo e no total (`"total"`), nos casos com extração."""
    ext, esp, ok = Counter(), Counter(), Counter()
    for caso in casos:
        if caso.id not in extraidos:
            continue
        e, g = itens(extraidos[caso.id]), itens(caso.atributos)
        for nome, _ in e:
            ext[nome] += 1
        for nome, _ in g:
            esp[nome] += 1
        for nome, _ in e & g:
            ok[nome] += 1
    nomes = sorted(set(ext) | set(esp))
    saida = {n: Acerto(ext[n], esp[n], ok[n]) for n in nomes}
    saida["total"] = Acerto(sum(ext.values()), sum(esp.values()), sum(ok.values()))
    return saida


def diferencas(caso: Caso, extraido: AtributosObservados) -> tuple[list[Item], list[Item]]:
    """(a mais, a menos) do extraído em relação ao gabarito, para o relatório por caso."""
    e, g = itens(extraido), itens(caso.atributos)
    return sorted(e - g), sorted(g - e)


def linhas_acerto(acertos: Mapping[str, Acerto]) -> Iterable[str]:
    def pct(x: float | None) -> str:
        return "   -" if x is None else f"{x:4.0%}"

    yield f"{'atributo':16} {'extraídos':>9} {'gabarito':>8} {'certos':>6}  precisão  cobertura"
    for nome, a in acertos.items():
        yield (
            f"{nome:16} {a.extraidos:9} {a.esperados:8} {a.certos:6}  "
            f"{pct(a.precisao):>8}  {pct(a.cobertura):>9}"
        )
