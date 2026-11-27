"""Relatório das sessões moderadas (`moderar`, plano_assistant2.md §14).

Lê os JSON de `reports/sessoes/` com uma ave definida (as conversas livres não
têm espécie esperada) e diz, por sessão: se a pessoa confirmou a espécie certa,
se ela chegou a ser apresentada, a posição dela nas 10 primeiras da primeira e
da última busca e quantas perguntas foram feitas.

`gravar_tsv` junta todas as sessões moderadas num arquivo só
(`reports/sessoes_moderadas.tsv`, uma linha por sessão, com a conversa), que a
página regrava depois de cada mensagem e o comando `sessoes` também grava.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from avesrag_assistant.conversa.estado import EstadoConversa

FORA = "fora das 10"


@dataclass(frozen=True)
class ResumoSessao:
    id: str
    ave: str
    esperada: str
    etapa: str
    pessoa: str
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
        pessoa=e.pessoa or "",
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
            f"{r.id}  pessoa {r.pessoa or '-':<5} ave {r.ave:<3} {r.esperada:<32} {resultado:<12} "
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


def especies_de_calibracao(casos_yaml: Path) -> set[str]:
    """As espécies com algum caso de calibração: aves que não servem para o teste, porque os
    pesos e as perguntas foram ajustados com elas."""
    if not casos_yaml.exists():
        return set()
    dados = yaml.safe_load(casos_yaml.read_text(encoding="utf-8")) or {}
    return {c["esperada"] for c in dados.get("casos", []) if c.get("uso") == "calibracao"}


def linhas_do_teste(
    resumos: Sequence[ResumoSessao],
    calibracao: set[str],
    previstas: Sequence[str] = (),
) -> list[str]:
    """O que vale como teste: sessões concluídas, com ave fora da calibração.

    As sessões com ave da calibração (a foto delas entrega a resposta) e as em andamento ficam
    de fora da contagem. Mostra o acerto por pessoa e as aves previstas que ainda não têm
    nenhuma sessão válida.
    """
    validas = [r for r in resumos if r.etapa == "fim" and r.esperada not in calibracao]
    fora = len(resumos) - len(validas)
    saida = [
        f"teste: {len(validas)} sessão(ões) válida(s); {fora} fora da contagem "
        "(em andamento ou ave da calibração)"
    ]
    if validas:
        acertos = sum(r.acertou for r in validas)
        saida.append(
            f"  acerto: {acertos}/{len(validas)} ({acertos / len(validas):.0%}); "
            f"espécie apresentada em {sum(r.apresentada for r in validas)}"
        )
        pessoas: dict[str, list[ResumoSessao]] = {}
        for r in validas:
            pessoas.setdefault(r.pessoa or "(sem código)", []).append(r)
        saida.append(f"  pessoas: {len(pessoas)}")
        for nome in sorted(pessoas):
            rs = pessoas[nome]
            saida.append(
                f"    {nome:<12} {len(rs)} sessão(ões), {sum(r.acertou for r in rs)} acerto(s)"
            )
        if "(sem código)" in pessoas:
            saida.append("  atenção: há sessões sem código de pessoa")
    if previstas:
        feitas = {r.ave for r in validas}
        faltam = [a for a in previstas if a not in feitas]
        saida.append(f"  aves ainda sem sessão válida ({len(faltam)}): {', '.join(faltam) or '-'}")
    return saida


ARQUIVO_TSV = "sessoes_moderadas.tsv"
COLUNAS_TSV = [
    "sessao",
    "criada_em",
    "atualizada_em",
    "pessoa",
    "ave",
    "esperada",
    "etapa",
    "resultado",
    "confirmada",
    "apresentada",
    "rodadas",
    "perguntas",
    "posicao_primeira_busca",
    "posicao_ultima_busca",
    "perguntas_feitas",
    "descricao",
    "conversa",
]


def _uma_linha(texto: str) -> str:
    return " ".join(texto.split())


def linha_tsv(e: EstadoConversa) -> dict[str, str]:
    r = resumir(e)
    resultado = "acertou" if r.acertou else ("em andamento" if r.etapa != "fim" else "errou")
    conversa = " | ".join(
        f"{'pessoa' if m.papel == 'usuario' else 'assistente'}: {_uma_linha(m.texto)}"
        for m in e.mensagens
    )
    return {
        "sessao": e.id,
        "criada_em": e.criada_em.isoformat(timespec="seconds"),
        "atualizada_em": e.atualizada_em.isoformat(timespec="seconds"),
        "pessoa": r.pessoa,
        "ave": r.ave,
        "esperada": r.esperada,
        "etapa": r.etapa,
        "resultado": resultado,
        "confirmada": e.confirmada or "",
        "apresentada": "sim" if r.apresentada else "não",
        "rodadas": str(r.rodadas),
        "perguntas": str(r.perguntas),
        "posicao_primeira_busca": "" if r.primeira is None else str(r.primeira),
        "posicao_ultima_busca": "" if r.ultima is None else str(r.ultima),
        "perguntas_feitas": ", ".join(e.perguntas_feitas),
        "descricao": _uma_linha(" ".join(e.descricoes)),
        "conversa": conversa,
    }


def gravar_tsv(estados: Sequence[EstadoConversa], path: Path) -> Path:
    """Todas as sessões moderadas num TSV, gravado de forma atômica."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUNAS_TSV, delimiter="\t", lineterminator="\n")
            w.writeheader()
            w.writerows(linha_tsv(e) for e in estados)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
