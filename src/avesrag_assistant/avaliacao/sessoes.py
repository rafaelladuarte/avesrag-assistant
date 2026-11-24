"""Relatório das sessões moderadas (`moderar`, plano_assistant2.md §14).

Lê os JSON de `reports/sessoes/` com uma ave definida (as conversas livres não
têm espécie esperada) e diz, por sessão: se a pessoa confirmou a espécie certa,
se ela chegou a ser apresentada, a posição dela nas 10 primeiras da primeira e
da última busca e quantas perguntas foram feitas.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from avesrag_assistant.conversa.estado import EstadoConversa

FORA = "fora das 10"


@dataclass(frozen=True)
class ResumoSessao:
    id: str
    ave: str
    esperada: str
    etapa: str
    acertou: bool
    apresentada: bool  # a esperada esteve entre as 3 mostradas em alguma rodada
    rodadas: int  # apresentações feitas
    perguntas: int
    primeira: int | None  # posição da esperada na primeira busca (None: fora das 10)
    ultima: int | None


def _posicao(busca: Sequence[str], species_id: str) -> int | None:
    return busca.index(species_id) + 1 if species_id in busca else None


def resumir(e: EstadoConversa) -> ResumoSessao:
    esperada = e.esperada or ""
    mostradas = set(e.recusadas) | {c.species_id for c in e.candidatas}
    if e.confirmada:
        mostradas.add(e.confirmada)
    rodadas = e.rodada_confirmacao + (1 if e.candidatas or e.confirmada else 0)
    return ResumoSessao(
        id=e.id,
        ave=e.ave or "",
        esperada=esperada,
        etapa=e.etapa,
        acertou=bool(e.confirmada) and e.confirmada == esperada,
        apresentada=esperada in mostradas,
        rodadas=rodadas,
        perguntas=len(e.perguntas_feitas),
        primeira=_posicao(e.buscas[0], esperada) if e.buscas else None,
        ultima=_posicao(e.buscas[-1], esperada) if e.buscas else None,
    )


def carregar_moderadas(diretorio: Path) -> list[EstadoConversa]:
    """As sessões com ave, da mais antiga para a mais nova."""
    if not diretorio.exists():
        return []
    estados = [
        EstadoConversa.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(diretorio.glob("*.json"))
    ]
    return sorted((e for e in estados if e.ave), key=lambda e: e.criada_em)


def _fmt(p: int | None) -> str:
    return FORA if p is None else f"{p}º"


def linhas(resumos: Sequence[ResumoSessao]) -> list[str]:
    if not resumos:
        return ["nenhuma sessão moderada em reports/sessoes/"]
    saida = []
    for r in resumos:
        resultado = "acertou" if r.acertou else ("em andamento" if r.etapa != "fim" else "errou")
        saida.append(
            f"{r.id}  ave {r.ave:<3} {r.esperada:<32} {resultado:<12} "
            f"apresentada: {'sim' if r.apresentada else 'não'}  rodadas {r.rodadas}  "
            f"perguntas {r.perguntas}  1ª busca {_fmt(r.primeira)}  última {_fmt(r.ultima)}"
        )
    fim = [r for r in resumos if r.etapa == "fim"]
    if fim:
        acertos = sum(r.acertou for r in fim)
        apresentadas = sum(r.apresentada for r in fim)
        saida.append(
            f"{len(fim)} sessão(ões) concluída(s): {acertos} acerto(s) "
            f"({acertos / len(fim):.0%}); espécie apresentada em {apresentadas} "
            f"({apresentadas / len(fim):.0%}); "
            f"perguntas por sessão: {sum(r.perguntas for r in fim) / len(fim):.1f}"
        )
    return saida
