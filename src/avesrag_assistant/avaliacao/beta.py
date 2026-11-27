"""Relatório do beta fechado (AS6, plano_assistant2.md §14.2).

Sem as sessões moderadas, o teste com pessoas é o beta por convite. Cada conversa
guarda a espécie que a pessoa confirmou e o voto "ajudou" ou "não ajudou". Este
módulo resume as conversas (da collection `conversations` ou de arquivos), conta
quanto falta para o critério de saída do marco 2 e **guarda um resumo sem texto
livre** antes de o TTL apagar as conversas (P6).

O que o resumo guarda: números, as espécies, os atributos do vocabulário e o
voto. Nunca as mensagens, as descrições nem o local (texto livre pode trazer
dado pessoal). O convite vira um pseudônimo (`c` mais 8 hexadecimais de um hash
com um sal que só existe nesta máquina, em `reports/beta/sal.txt`): quem não tem
o sal não recupera o nome pelo arquivo.

Limite que o relatório repete: sem gabarito, "confirmou" e "ajudou" medem
satisfação, não acerto (em 4 das 18 sessões moderadas a pessoa confirmou a
espécie errada).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import secrets
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from avesrag_assistant.conversa.estado import EstadoConversa

ARQUIVO_RESUMOS = "conversas.jsonl"
ARQUIVO_TSV = "conversas.tsv"
ARQUIVO_SAL = "sal.txt"
SEM_CONVITE = "(sem convite)"

MINIMO_CONVERSAS = 30
MINIMO_PESSOAS = 5


class ResumoBeta(BaseModel):
    """Uma conversa do beta, sem nenhum texto livre."""

    model_config = ConfigDict(extra="forbid")

    sessao: str
    convite: str  # o nome na leitura; o pseudônimo no arquivo (`anonimizar`)
    criada_em: str  # só a data (AAAA-MM-DD)
    etapa: str
    motivo_fim: str | None = None
    confirmada: str | None = None  # species_id
    voto: Literal["ajudou", "nao_ajudou"] | None = None
    mensagens: int = 0  # da pessoa
    perguntas: int = 0
    apresentacoes: int = 0  # quantas vezes as 3 candidatas foram mostradas
    apresentadas: list[str] = Field(default_factory=list)
    posicao_primeira: int | None = None  # da confirmada na 1ª busca (antes das perguntas)
    posicao_ultima: int | None = None
    bateram: list[str] = Field(default_factory=list)  # atributos que bateram com a confirmada
    conflitaram: list[str] = Field(default_factory=list)
    atributos: dict[str, Any] = Field(default_factory=dict)  # valores do vocabulário
    chamadas_llm: int = 0
    chamadas_embedding: int = 0


def como_utc(momento: datetime) -> datetime:
    """O pymongo devolve datas sem fuso (em UTC): põe o fuso para poder comparar com `agora()`."""
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _posicao(busca: Sequence[str], species_id: str | None) -> int | None:
    return busca.index(species_id) + 1 if species_id and species_id in busca else None


def resumir(e: EstadoConversa) -> ResumoBeta:
    confirmada = next((c for c in e.candidatas if c.species_id == e.confirmada), None)
    apresentadas = list(dict.fromkeys([*e.recusadas, *(c.species_id for c in e.candidatas)]))
    return ResumoBeta(
        sessao=e.id,
        convite=e.convite or SEM_CONVITE,
        criada_em=e.criada_em.date().isoformat(),
        etapa=e.etapa,
        motivo_fim=e.motivo_fim,
        confirmada=e.confirmada,
        voto=e.voto,
        mensagens=sum(1 for m in e.mensagens if m.papel == "usuario"),
        perguntas=len(e.perguntas_feitas),
        apresentacoes=e.rodada_confirmacao + (1 if e.candidatas or e.confirmada else 0),
        apresentadas=apresentadas,
        posicao_primeira=_posicao(e.buscas[0], e.confirmada) if e.buscas else None,
        posicao_ultima=_posicao(e.buscas[-1], e.confirmada) if e.buscas else None,
        bateram=list(confirmada.bateram) if confirmada else [],
        conflitaram=list(confirmada.conflitaram) if confirmada else [],
        atributos=dict(e.atributos),
        chamadas_llm=e.chamadas_llm,
        chamadas_embedding=e.chamadas_embedding,
    )


# -- pseudônimo ------------------------------------------------------------------


def carregar_sal(diretorio: Path) -> str:
    """O sal desta máquina; criado na primeira vez e nunca exportado com os resumos."""
    path = diretorio / ARQUIVO_SAL
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    diretorio.mkdir(parents=True, exist_ok=True)
    sal = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(sal + "\n")
    return sal


def pseudonimo(convite: str, sal: str) -> str:
    return "c" + hashlib.sha256(f"{sal}:{convite}".encode()).hexdigest()[:8]


def anonimizar(r: ResumoBeta, sal: str) -> ResumoBeta:
    return r.model_copy(update={"convite": pseudonimo(r.convite, sal)})


# -- arquivo ---------------------------------------------------------------------


def _gravar_atomico(path: Path, escrever) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            escrever(fh)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def ler_arquivo(path: Path) -> dict[str, ResumoBeta]:
    """Os resumos já guardados, por sessão (vazio se o arquivo não existe)."""
    if not path.exists():
        return {}
    resumos = [
        ResumoBeta.model_validate_json(linha)
        for linha in path.read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    return {r.sessao: r for r in resumos}


def mesclar(
    guardados: Mapping[str, ResumoBeta], novos: Iterable[ResumoBeta]
) -> dict[str, ResumoBeta]:
    """Os guardados mais os novos; a versão nova de uma sessão vale mais que a guardada."""
    saida = dict(guardados)
    for r in novos:
        saida[r.sessao] = r
    return saida


def _ordenar(resumos: Iterable[ResumoBeta]) -> list[ResumoBeta]:
    return sorted(resumos, key=lambda r: (r.criada_em, r.sessao))


def gravar_arquivo(path: Path, resumos: Mapping[str, ResumoBeta]) -> Path:
    def escrever(fh) -> None:
        for r in _ordenar(resumos.values()):
            fh.write(r.model_dump_json() + "\n")

    return _gravar_atomico(path, escrever)


COLUNAS_TSV = [
    "sessao",
    "convite",
    "criada_em",
    "etapa",
    "motivo_fim",
    "confirmada",
    "voto",
    "mensagens",
    "perguntas",
    "apresentacoes",
    "posicao_primeira",
    "posicao_ultima",
    "apresentadas",
    "bateram",
    "conflitaram",
    "atributos",
    "chamadas_llm",
    "chamadas_embedding",
]


def gravar_tsv(path: Path, resumos: Mapping[str, ResumoBeta]) -> Path:
    """Os mesmos resumos numa tabela para abrir em planilha (sem texto livre)."""

    def linha(r: ResumoBeta) -> dict[str, str]:
        d = r.model_dump()
        for campo in ("apresentadas", "bateram", "conflitaram"):
            d[campo] = ", ".join(d[campo])
        d["atributos"] = json.dumps(d["atributos"], ensure_ascii=False, sort_keys=True)
        return {c: "" if d[c] is None else str(d[c]) for c in COLUNAS_TSV}

    def escrever(fh) -> None:
        w = csv.DictWriter(fh, fieldnames=COLUNAS_TSV, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(linha(r) for r in _ordenar(resumos.values()))

    return _gravar_atomico(path, escrever)


# -- relatório -------------------------------------------------------------------


def intervalo_wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confiança de 95% para k de n (Wilson): é largo com poucas conversas."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centro - meia), min(1.0, centro + meia))


def _taxa(k: int, n: int) -> str:
    if n == 0:
        return "sem dados"
    baixo, alto = intervalo_wilson(k, n)
    return f"{k}/{n} ({k / n:.0%}; intervalo de 95%: {baixo:.0%} a {alto:.0%})"


def _media(valores: Sequence[int | float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def linhas(
    resumos: Sequence[ResumoBeta],
    nomes: Mapping[str, str] | None = None,
    minimo_conversas: int = MINIMO_CONVERSAS,
    minimo_pessoas: int = MINIMO_PESSOAS,
) -> list[str]:
    """O relatório. `nomes` traduz o pseudônimo de volta para o nome do convite, só na tela."""
    nomes = nomes or {}
    if not resumos:
        return ["beta: nenhuma conversa (sem as da moderação nem as excluídas)"]
    concluidas = [r for r in resumos if r.etapa == "fim"]
    confirmadas = [r for r in resumos if r.confirmada]
    sem_confirmar = [r for r in concluidas if not r.confirmada]
    abertas = len(resumos) - len(concluidas)
    pessoas = {r.convite for r in confirmadas}

    saida = [
        f"beta: {len(resumos)} conversa(s) de {len({r.convite for r in resumos})} convite(s)",
        f"  concluídas: {len(concluidas)}; em andamento ou abandonadas: {abertas}",
        f"  com espécie confirmada: {len(confirmadas)}; "
        f'concluídas sem confirmar ("nenhuma delas" ou sem mais rodadas): {len(sem_confirmar)}',
    ]
    if confirmadas:
        na_primeira = sum(1 for r in confirmadas if r.apresentacoes == 1)
        saida.append(
            f"  confirmou já na 1ª apresentação das candidatas: {_taxa(na_primeira, len(confirmadas))}"
        )
        buscadas = [r for r in confirmadas if r.posicao_primeira is not None]
        top3 = sum(1 for r in buscadas if r.posicao_primeira <= 3)  # type: ignore[operator]
        saida.append(
            f"  confirmada entre as 3 primeiras da 1ª busca (antes das perguntas): "
            f"{_taxa(top3, len(confirmadas))}; entre as 10: {len(buscadas)}/{len(confirmadas)}"
        )

    com_confirmacao_e_voto = [r for r in confirmadas if r.voto]
    ajudou_c = sum(1 for r in com_confirmacao_e_voto if r.voto == "ajudou")
    votaram = [r for r in concluidas if r.voto]
    ajudou = sum(1 for r in votaram if r.voto == "ajudou")
    saida.append("")
    saida.append("votos")
    saida.append(
        f'  "ajudou" nas conversas com confirmação que votaram: '
        f"{_taxa(ajudou_c, len(com_confirmacao_e_voto))}"
    )
    saida.append(f'  "ajudou" em todas as concluídas que votaram: {_taxa(ajudou, len(votaram))}')
    sem_voto = len(concluidas) - len(votaram)
    if sem_voto:
        saida.append(f"  concluídas sem voto: {sem_voto}")

    if concluidas:
        saida.append("")
        saida.append("uso")
        saida.append(
            f"  mensagens da pessoa por conversa concluída: "
            f"{_media([r.mensagens for r in concluidas]):.1f}; "
            f"perguntas: {_media([r.perguntas for r in concluidas]):.1f}; "
            f"chamadas ao LLM: {_media([r.chamadas_llm for r in concluidas]):.1f}"
        )

    saida.append("")
    saida.append("por convite")
    por_convite: dict[str, list[ResumoBeta]] = {}
    for r in resumos:
        por_convite.setdefault(r.convite, []).append(r)
    for chave in sorted(por_convite, key=lambda c: nomes.get(c, c)):
        rs = por_convite[chave]
        saida.append(
            f"  {nomes.get(chave, chave):<16} {len(rs):>3} conversa(s), "
            f"{sum(1 for r in rs if r.etapa == 'fim'):>3} concluída(s), "
            f"{sum(1 for r in rs if r.confirmada):>3} confirmada(s), "
            f"ajudou {sum(1 for r in rs if r.voto == 'ajudou')}, "
            f"não ajudou {sum(1 for r in rs if r.voto == 'nao_ajudou')}"
        )

    falta_conversas = max(minimo_conversas - len(confirmadas), 0)
    falta_pessoas = max(minimo_pessoas - len(pessoas), 0)
    saida.append("")
    saida.append(
        f"critério do marco 2: conversas com confirmação {len(confirmadas)}/{minimo_conversas}, "
        f"pessoas {len(pessoas)}/{minimo_pessoas}"
    )
    if falta_conversas or falta_pessoas:
        faltas = []
        if falta_conversas:
            faltas.append(f"{falta_conversas} conversa(s) com confirmação")
        if falta_pessoas:
            faltas.append(f"{falta_pessoas} pessoa(s) a mais")
        saida.append("  ainda falta: " + " e ".join(faltas))
    else:
        saida.append('  atingido: registre a taxa de "ajudou" no §14.2 do plano_assistant2.md')
    saida.append(
        '  limite: sem gabarito, "confirmou" e "ajudou" medem satisfação, não acerto; '
        "peça aos convidados que testem aves que conhecem"
    )
    return saida
