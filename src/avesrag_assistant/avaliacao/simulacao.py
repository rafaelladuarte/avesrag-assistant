"""Usuário simulado: mede as perguntas sem pessoas (plano_assistant2.md §4.3, E4).

Para cada caso, a busca roda com a descrição e os atributos anotados; o código
escolhe a pergunta (`conversa.perguntas.escolher`), e o "usuário" responde com
o que está na ficha da espécie esperada, sem passar pelo LLM: o atributo entra
direto no estado. Nos atributos de `simulacao.um_valor` (onde estava, ambiente,
bico, tamanho), a resposta é um valor sorteado entre os da ficha; nos outros,
todos. Se a ficha não tem o dado, a resposta é "não sei". Depois de
cada resposta, a busca roda de novo. O resultado é a posição da esperada depois
de 0, 1, ..., `perguntas.maximo` perguntas; quando não há pergunta útil, a
posição fica a mesma até o fim.

Ruído: com probabilidade `ruido`, uma resposta de cor troca uma das cores por
uma vizinha (`simulacao.vizinhas`), e uma resposta de outro atributo vira "não
sei". O sorteio é determinístico: a semente e o id do caso.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from avesrag_builder.core.models import Atributos
from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from pymongo.database import Database

from avesrag_assistant.avaliacao.casos import Caso
from avesrag_assistant.avaliacao.rodar import posicao
from avesrag_assistant.busca.cache import CacheEmbeddings
from avesrag_assistant.busca.modelos import Consulta
from avesrag_assistant.busca.pontuacao import Ranking
from avesrag_assistant.busca.retriever import buscar, carregar_fichas
from avesrag_assistant.conversa.config import ConfigConversa, ConfigSimulacao
from avesrag_assistant.conversa.perguntas import (
    Pergunta,
    aplicar_resposta,
    escolher,
    valores_da_ficha,
)

NAO_SEI = "não sei"


@dataclass
class Passo:
    pergunta: str  # a chave da pergunta (cores_por_parte:barriga, bico:comprimento, ...)
    ganho: float
    resposta: list[str]  # vazia = "não sei"
    com_ruido: bool = False


@dataclass
class Simulado:
    caso: Caso
    posicoes: list[int | None] = field(default_factory=list)  # depois de 0, 1, ... perguntas
    passos: list[Passo] = field(default_factory=list)


def responder(
    pergunta: Pergunta,
    ficha: Atributos,
    regras_bico: RegrasBico,
    config: ConfigSimulacao,
    rng: random.Random,
    ruido: float = 0.0,
) -> tuple[list[str], bool]:
    """A resposta pela ficha (vazia = "não sei") e se houve ruído."""
    valores = valores_da_ficha(pergunta, ficha, regras_bico)
    if valores and pergunta.atributo in config.um_valor:
        valores = [rng.choice(valores)]
    if not valores or not ruido or rng.random() >= ruido:
        return valores, False
    if pergunta.atributo == "cores_por_parte":
        i = rng.randrange(len(valores))
        vizinhas = config.vizinhas_de(valores[i])
        if vizinhas:
            trocada = [*valores[:i], rng.choice(vizinhas), *valores[i + 1 :]]
            return list(dict.fromkeys(trocada)), True
    return [], True


def simular_caso(
    db: Database,
    caso: Caso,
    vetor: list[float],
    ranking: Ranking,
    vocabulario: Vocabulario,
    regras_bico: RegrasBico,
    conversa: ConfigConversa,
    ate: int,
    sugeridas: Sequence[str] = (),
    ruido: float = 0.0,
    semente: int = 0,
) -> Simulado:
    perguntas = conversa.perguntas
    rng = random.Random(f"{semente}:{caso.id}")
    ficha_certa = carregar_fichas(db, [caso.esperada])[caso.esperada].atributos
    atributos = caso.atributos
    feitas: list[str] = []
    res = Simulado(caso=caso)
    while True:
        consulta = Consulta.model_construct(
            descricao=caso.descricao, atributos=atributos, recusadas=[]
        )
        candidatas = buscar(
            db,
            consulta,
            vetor,
            ranking,
            regras_bico,
            conversa.busca.modo,
            k=ate,
            sugeridas=sugeridas,
        )
        res.posicoes.append(posicao(candidatas, caso.esperada))
        if len(feitas) == perguntas.maximo:
            break
        primeiras = candidatas[: perguntas.candidatas]
        fichas = carregar_fichas(db, [c.species_id for c in primeiras])
        escolha = escolher(
            [(c.nota_final, fichas[c.species_id].atributos) for c in primeiras],
            atributos,
            perguntas,
            regras_bico,
            feitas,
        )
        if escolha is None:  # nenhuma pergunta útil: a posição fica a mesma
            res.posicoes += [res.posicoes[-1]] * (perguntas.maximo - len(feitas))
            break
        resposta, com_ruido = responder(
            escolha.pergunta, ficha_certa, regras_bico, conversa.simulacao, rng, ruido
        )
        feitas.append(escolha.pergunta.chave)
        res.passos.append(Passo(escolha.pergunta.chave, escolha.ganho, resposta, com_ruido))
        atributos = aplicar_resposta(
            atributos, escolha.pergunta, resposta, vocabulario, regras_bico
        )
    return res


def simular(
    db: Database,
    casos: Sequence[Caso],
    cache: CacheEmbeddings,
    ranking: Ranking,
    vocabulario: Vocabulario,
    regras_bico: RegrasBico,
    conversa: ConfigConversa,
    sugeridas: Mapping[str, Sequence[str]] | None = None,
    ruido: float = 0.0,
    semente: int = 0,
) -> list[Simulado]:
    """Os casos alcançáveis já filtrados pelo chamador; posições até `vetor.especies`."""
    vetores = cache.vetores([c.descricao for c in casos])
    return [
        simular_caso(
            db,
            caso,
            vetor,
            ranking,
            vocabulario,
            regras_bico,
            conversa,
            ranking.vetor.especies,
            (sugeridas or {}).get(caso.id, ()),
            ruido,
            semente,
        )
        for caso, vetor in zip(casos, vetores, strict=True)
    ]


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

GRUPOS = {
    "pessoas": lambda s: s.caso.origem == "foto",
    "outros": lambda s: s.caso.origem != "foto",
    "todos": lambda s: True,
}
TOPS = (1, 3, 10)


def tabela(simulados: Sequence[Simulado]) -> list[str]:
    """Top 1, 3 e 10 por grupo, depois de 0, 1, ... perguntas."""
    n_passos = max((len(s.posicoes) for s in simulados), default=0)
    linhas = [
        f"{'grupo':<8} {'n':>3} {'perguntas':>9}" + "".join(f" {'top' + str(k):>6}" for k in TOPS)
    ]
    for grupo, filtro in GRUPOS.items():
        sims = [s for s in simulados if filtro(s)]
        if not sims:
            continue
        for i in range(n_passos):
            pos = [s.posicoes[i] for s in sims]
            tops = "".join(
                f" {sum(1 for p in pos if p is not None and p <= k) / len(pos):>6.0%}" for k in TOPS
            )
            linhas.append(f"{grupo:<8} {len(sims):>3} {i:>9}{tops}")
    return linhas


def resumo_perguntas(simulados: Sequence[Simulado]) -> list[str]:
    """Quantas vezes cada atributo foi perguntado e quantas respostas foram "não sei"."""
    contagem: dict[str, list[int]] = {}
    for s in simulados:
        for p in s.passos:
            atributo = p.pergunta.split(":")[0]
            c = contagem.setdefault(atributo, [0, 0])
            c[0] += 1
            c[1] += not p.resposta
    return [
        f"{atributo}: {n} pergunta(s), {nao_sei} {NAO_SEI}"
        for atributo, (n, nao_sei) in sorted(contagem.items(), key=lambda x: -x[1][0])
    ]


def linhas_tsv(simulados: Sequence[Simulado]) -> list[list[str]]:
    n_passos = max((len(s.posicoes) for s in simulados), default=0)
    cab = ["caso", "esperada", "origem", *(f"pos_{i}" for i in range(n_passos)), "perguntas"]
    linhas = [cab]
    for s in simulados:
        passos = " | ".join(
            f"{p.pergunta}={','.join(p.resposta) or NAO_SEI}{' (ruído)' if p.com_ruido else ''}"
            for p in s.passos
        )
        pos = ["" if p is None else str(p) for p in s.posicoes]
        linhas.append([s.caso.id, s.caso.esperada, s.caso.origem, *pos, passos])
    return linhas
