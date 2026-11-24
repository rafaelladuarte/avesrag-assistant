"""Escolha da pergunta: o atributo que mais separa as candidatas (plano_assistant2.md §4.1).

Funções puras: nada de Mongo nem de rede. Cada pergunta possível (um atributo,
uma parte do corpo para a cor ou uma dimensão do bico) tem, em cada ficha, um
conjunto de respostas, ou nenhum (`sem_dado`). Uma resposta elimina as
candidatas com o dado e sem nenhum valor em comum com ela, como na pontuação,
em que o atributo só conflita se nenhum valor bateu.

O ganho de uma pergunta é a massa eliminada esperada: supondo que a espécie
certa seja a candidata j (com o peso dela) e que a pessoa responda o que está
na ficha de j, quanto do peso das outras sai. Uma candidata sem o dado não é
eliminada, e se a certa não tem o dado, a resposta é "não sei" e nada sai. Os
pesos são o softmax das notas finais. A entropia das respostas, prevista no
plano, foi trocada por essa medida porque as fichas têm vários valores por
atributo (um peito "amarelo e branco" não é eliminado por "amarelo").
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from avesrag_builder.core.models import Atributos
from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario

from avesrag_assistant.busca.modelos import DIMENSOES_BICO, AtributosObservados, validar_atributos
from avesrag_assistant.conversa.config import ConfigPerguntas


@dataclass(frozen=True)
class Pergunta:
    atributo: str
    parte: str | None = None  # a parte do corpo (cores_por_parte) ou a dimensão (bico)

    @property
    def chave(self) -> str:
        return f"{self.atributo}:{self.parte}" if self.parte else self.atributo


@dataclass(frozen=True)
class Escolha:
    pergunta: Pergunta
    ganho: float


def opcoes(
    config: ConfigPerguntas, atributos: AtributosObservados, feitas: Sequence[str] = ()
) -> Iterator[Pergunta]:
    """As perguntas ainda possíveis, na ordem de `perguntaveis` (que desempata)."""
    feitas = set(feitas)
    for nome in config.perguntaveis:
        if nome == "cores_por_parte":
            ditas = {p.parte for p in atributos.cores_por_parte or []}
            candidatas = [Pergunta(nome, parte) for parte in config.partes if parte not in ditas]
        elif nome == "bico":
            bico = atributos.bico
            candidatas = [
                Pergunta(nome, dim)
                for dim in DIMENSOES_BICO
                if bico is None or getattr(bico, dim) is None
            ]
        else:
            candidatas = [Pergunta(nome)] if getattr(atributos, nome) is None else []
        yield from (p for p in candidatas if p.chave not in feitas)


def valores_da_ficha(pergunta: Pergunta, ficha: Atributos, regras_bico: RegrasBico) -> list[str]:
    """As respostas que a ficha dá para a pergunta, na ordem da ficha; vazia = sem dado."""
    if pergunta.atributo == "cores_por_parte":
        valores = [c.cor for c in ficha.cores_por_parte or [] if c.parte == pergunta.parte]
    elif pergunta.atributo == "bico":
        if ficha.bico_tipo is None:
            return []
        valores = list(getattr(regras_bico.perfil(ficha.bico_tipo), pergunta.parte))
    else:
        valores = list(getattr(ficha, pergunta.atributo) or [])
    return list(dict.fromkeys(valores))


def pesos(notas: Sequence[float], temperatura: float) -> list[float]:
    """Softmax de `notas / temperatura`."""
    if not notas:
        return []
    maior = max(notas)
    exps = [math.exp((n - maior) / temperatura) for n in notas]
    total = sum(exps)
    return [e / total for e in exps]


def ganho(
    pergunta: Pergunta,
    fichas: Sequence[Atributos],
    pesos_: Sequence[float],
    regras_bico: RegrasBico,
) -> float:
    """Massa eliminada esperada (ver o docstring do módulo)."""
    conjuntos = [frozenset(valores_da_ficha(pergunta, f, regras_bico)) for f in fichas]
    total = 0.0
    for wj, sj in zip(pesos_, conjuntos, strict=True):
        if not sj:
            continue
        total += wj * sum(
            wi for wi, si in zip(pesos_, conjuntos, strict=True) if si and not si & sj
        )
    return total


def escolher(
    candidatas: Sequence[tuple[float, Atributos]],
    atributos: AtributosObservados,
    config: ConfigPerguntas,
    regras_bico: RegrasBico,
    feitas: Sequence[str] = (),
) -> Escolha | None:
    """A pergunta de maior ganho sobre as primeiras candidatas (nota final, ficha).

    None se nenhuma chega ao `ganho_minimo`. No empate, vale a ordem de `opcoes`.
    """
    primeiras = list(candidatas)[: config.candidatas]
    if len(primeiras) < 2:
        return None
    w = pesos([nota for nota, _ in primeiras], config.temperatura)
    fichas = [ficha for _, ficha in primeiras]
    melhor: Escolha | None = None
    for p in opcoes(config, atributos, feitas):
        g = ganho(p, fichas, w, regras_bico)
        if melhor is None or g > melhor.ganho + 1e-12:
            melhor = Escolha(p, g)
    if melhor is None or melhor.ganho < config.ganho_minimo:
        return None
    return melhor


def aplicar_resposta(
    atributos: AtributosObservados,
    pergunta: Pergunta,
    valores: Sequence[str],
    vocabulario: Vocabulario,
    regras_bico: RegrasBico,
) -> AtributosObservados:
    """Os atributos com a resposta somada; no bico, vale o primeiro valor."""
    if not valores:
        return atributos
    dados = atributos.model_dump(exclude_none=True)
    nome = pergunta.atributo
    if nome == "cores_por_parte":
        novos = [{"parte": pergunta.parte, "cor": v} for v in valores]
        dados[nome] = [*dados.get(nome, []), *novos]
    elif nome == "bico":
        dados[nome] = {**dados.get(nome, {}), pergunta.parte: valores[0]}
    else:
        dados[nome] = [*dados.get(nome, []), *valores]
    return validar_atributos(dados, vocabulario, regras_bico)
