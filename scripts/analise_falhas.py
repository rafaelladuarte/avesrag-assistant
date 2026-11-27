"""Análise das falhas de ranking (27/11/2026): divisão calibração/teste, baseline com várias
sementes, classificação das falhas, matrizes de conflito e experimentos E1 e E2.

Só LÊ dos caches (`reports/cache_llm.json` e `reports/cache_embeddings.json`): o fornecedor e o
embedder são trocados por peças que levantam erro se alguém tentar uma chamada nova, então esta
análise nunca gera custo de API. Tudo o que grava vai para `reports/analise_falhas/`; as fichas, as
correções manuais e o `reports/` existente não são tocados.

    poetry run python scripts/analise_falhas.py divisao
    poetry run python scripts/analise_falhas.py baseline [--sementes 10]
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import itertools
import json
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from avesrag_builder.llm.base import Embeddings

from avesrag_assistant import vocabulario
from avesrag_assistant.avaliacao.casos import Caso, carregar_casos, conferir_no_banco
from avesrag_assistant.avaliacao.rodar import posicao
from avesrag_assistant.avaliacao.simulacao import Passo, Simulado, responder, simular
from avesrag_assistant.busca import pontuacao as pont
from avesrag_assistant.busca import retriever
from avesrag_assistant.busca.cache import CacheEmbeddings
from avesrag_assistant.busca.modelos import Consulta
from avesrag_assistant.busca.pontuacao import carregar_ranking, pontuar
from avesrag_assistant.busca.retriever import buscar, carregar_fichas
from avesrag_assistant.conversa.config import carregar_conversa
from avesrag_assistant.conversa.perguntas import aplicar_resposta, escolher
from avesrag_assistant.db import get_db
from avesrag_assistant.llm.cache import ARQUIVO_PADRAO, CacheLLM
from avesrag_assistant.llm.prompts import carregar_prompt
from avesrag_assistant.llm.sugestor import Sugestor, indice_da_base
from avesrag_assistant.settings import get_settings

SEMENTE_DIVISAO = 20261127
ALVO_TESTE = 20  # descrições no teste (de 18 a 22 serve)
TOPS = (1, 3, 10)


class CustoBloqueado(RuntimeError):
    """Uma chamada nova à API foi pedida: esta análise só usa o que já está no cache."""


class FornecedorBloqueado:
    nome = "bloqueado"

    def gerar(self, pedido):
        raise CustoBloqueado(f"chamada nova ao LLM bloqueada ({pedido.id})")


class EmbedderBloqueado:
    nome = "bloqueado"

    def embeddings(self, textos, modelo) -> Embeddings:
        raise CustoBloqueado(f"{len(textos)} embedding(s) novo(s) bloqueado(s)")


@dataclass
class Ambiente:
    settings: object
    db: object
    voc: object
    regras: object
    ranking: object
    conversa: object
    casos: list[Caso]  # as 50 descrições de pessoas
    cache: CacheEmbeddings
    sugeridas: dict[str, list[str]]
    saida: Path


def montar() -> Ambiente:
    settings = get_settings()
    voc, regras = vocabulario.carregar()
    ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
    conversa = carregar_conversa(settings.CONFIG_DIR / "conversa.yaml", voc)
    todos = carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras)
    db = get_db()
    inalcancaveis = set(conferir_no_banco(db, todos).inalcancaveis)
    casos = [c for c in todos if c.origem == "foto" and c.esperada not in inalcancaveis]
    cache = CacheEmbeddings(
        settings.REPORTS_DIR / "cache_embeddings.json",
        EmbedderBloqueado(),
        settings.EMBEDDING_MODEL,
    )
    prompt = carregar_prompt(settings.CONFIG_DIR / "prompts" / "c04_candidatas.yaml", voc)
    cache_llm = CacheLLM(settings.REPORTS_DIR / ARQUIVO_PADRAO, FornecedorBloqueado())
    sugestor = Sugestor(prompt, cache_llm, voc, regras, settings.LLM_MODEL)
    indice = indice_da_base(db)
    sugeridas = {c.id: sugestor.especies(c.descricao, indice) for c in casos}
    saida = settings.REPORTS_DIR / "analise_falhas"
    saida.mkdir(parents=True, exist_ok=True)
    return Ambiente(settings, db, voc, regras, ranking, conversa, casos, cache, sugeridas, saida)


# -- divisão -----------------------------------------------------------------------------------


def familias_das_especies(db, especies: Sequence[str]) -> dict[str, str]:
    docs = db["species"].find(
        {"species_id": {"$in": list(especies)}}, {"species_id": 1, "taxonomia.familia": 1}
    )
    return {d["species_id"]: d["taxonomia"]["familia"] for d in docs}


def dividir(casos: Sequence[Caso], familia_de: dict[str, str]) -> dict:
    """Calibração e teste por FAMÍLIA inteira (nenhuma família dos dois lados).

    A ficha do bico vem de uma regra por família e as descrições da mesma espécie compartilham a
    ficha: dividir por descrição deixaria o ajuste "ver" a mesma ficha nos dois lados. Entre as
    divisões com 18 a 22 descrições no teste, em pelo menos 4 famílias, a semente fixa sorteia uma.
    """
    por_familia: dict[str, list[Caso]] = defaultdict(list)
    for c in casos:
        por_familia[familia_de[c.esperada]].append(c)
    familias = sorted(por_familia)
    validas = []
    for r in range(4, len(familias)):
        for combinacao in itertools.combinations(familias, r):
            n = sum(len(por_familia[f]) for f in combinacao)
            if abs(n - ALVO_TESTE) <= 2:
                validas.append(combinacao)
    teste = random.Random(SEMENTE_DIVISAO).choice(validas)
    lado = {f: ("teste" if f in teste else "calibracao") for f in familias}
    return {
        "semente": SEMENTE_DIVISAO,
        "criterio": (
            "por família inteira; entre as divisões com 18 a 22 descrições no teste e pelo menos "
            f"4 famílias ({len(validas)} possíveis), a semente sorteia uma"
        ),
        "calibracao": sorted(c.id for c in casos if lado[familia_de[c.esperada]] == "calibracao"),
        "teste": sorted(c.id for c in casos if lado[familia_de[c.esperada]] == "teste"),
        "familias": {
            "calibracao": sorted(f for f in familias if lado[f] == "calibracao"),
            "teste": sorted(f for f in familias if lado[f] == "teste"),
        },
        "especies": {
            "calibracao": sorted(
                {c.esperada for c in casos if lado[familia_de[c.esperada]] == "calibracao"}
            ),
            "teste": sorted({c.esperada for c in casos if lado[familia_de[c.esperada]] == "teste"}),
        },
    }


def ler_divisao(saida: Path) -> dict[str, str]:
    """caso -> `calibracao` | `teste`."""
    dados = yaml.safe_load((saida / "divisao.yaml").read_text(encoding="utf-8"))
    return {
        **{c: "calibracao" for c in dados["calibracao"]},
        **{c: "teste" for c in dados["teste"]},
    }


def cmd_divisao(amb: Ambiente) -> None:
    destino = amb.saida / "divisao.yaml"
    if destino.exists():
        raise SystemExit(f"{destino} já existe: a divisão é fixa, não é refeita")
    familia_de = familias_das_especies(amb.db, {c.esperada for c in amb.casos})
    d = dividir(amb.casos, familia_de)
    destino.write_text(
        "# Divisão das 50 descrições de pessoas (27/11/2026). Fixa: os ajustes usam só a calibração.\n"
        + yaml.safe_dump(d, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"calibração: {len(d['calibracao'])} descrições, famílias {d['familias']['calibracao']}")
    print(f"teste:      {len(d['teste'])} descrições, famílias {d['familias']['teste']}")
    print(f"gravado em {destino}")


# -- baseline ----------------------------------------------------------------------------------


def topo(pos: Sequence[int | None], k: int) -> float:
    return sum(1 for p in pos if p is not None and p <= k) / len(pos) if pos else float("nan")


def rodar_sementes(
    amb: Ambiente, sementes: Sequence[int], ranking=None, calibrado: bool = True, ruido: float = 0.0
) -> dict[int, list[Simulado]]:
    return {
        s: simular(
            amb.db,
            amb.casos,
            amb.cache,
            ranking or amb.ranking,
            amb.voc,
            amb.regras,
            amb.conversa,
            amb.sugeridas,
            ruido,
            s,
            calibrado,
        )
        for s in sementes
    }


def metricas(
    por_semente: dict[int, list[Simulado]], lado_de: dict[str, str], passo: int
) -> dict[str, dict[int, list[float]]]:
    """grupo -> k -> uma taxa por semente, depois de `passo` perguntas."""
    saida: dict[str, dict[int, list[float]]] = {}
    for grupo in ("calibracao", "teste", "todas"):
        saida[grupo] = {k: [] for k in TOPS}
        for sims in por_semente.values():
            pos = [
                s.posicoes[passo] for s in sims if grupo == "todas" or lado_de[s.caso.id] == grupo
            ]
            for k in TOPS:
                saida[grupo][k].append(topo(pos, k))
    return saida


def faixa(valores: Sequence[float]) -> str:
    return (
        f"{statistics.mean(valores):.0%} ({min(valores):.0%}–{max(valores):.0%})"
        if valores
        else "—"
    )


def tabela_metricas(titulo: str, m_antes: dict, m_depois: dict) -> list[str]:
    linhas = [
        titulo,
        "| Grupo | n | Perguntas | Top 1 | Top 3 | Top 10 |",
        "|---|---|---|---|---|---|",
    ]
    for grupo, n in m_antes["_n"].items():
        for passo, m in ((0, m_antes), (3, m_depois)):
            linhas.append(
                f"| {grupo} | {n} | {passo} | "
                + " | ".join(faixa(m[grupo][k]) for k in TOPS)
                + " |"
            )
    return linhas


def gravar_por_caso(destino: Path, por_semente: dict[int, list[Simulado]], lado_de: dict) -> None:
    with destino.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(
            ["semente", "caso", "esperada", "lado", "pos_0", "pos_1", "pos_2", "pos_3", "perguntas"]
        )
        for semente, sims in por_semente.items():
            for s in sims:
                passos = " | ".join(
                    f"{p.pergunta}={','.join(p.resposta) or 'não sei'}"
                    f"{f' ({p.ruido})' if p.ruido else ''}"
                    for p in s.passos
                )
                w.writerow(
                    [semente, s.caso.id, s.caso.esperada, lado_de[s.caso.id]]
                    + ["" if p is None else p for p in s.posicoes]
                    + [passos]
                )


def cmd_baseline(amb: Ambiente, n_sementes: int) -> None:
    lado_de = ler_divisao(amb.saida)
    sementes = list(range(n_sementes))
    t0 = time.time()
    por_semente = rodar_sementes(amb, sementes)
    print(f"{n_sementes} sementes em {time.time() - t0:.0f}s")
    teto = rodar_sementes(amb, [0], calibrado=False, ruido=0.0)  # respostas sempre pela ficha
    n = {
        "calibracao": sum(v == "calibracao" for v in lado_de.values()),
        "teste": sum(v == "teste" for v in lado_de.values()),
        "todas": len(lado_de),
    }
    m_antes = {**metricas(por_semente, lado_de, 0), "_n": n}
    m_depois = {**metricas(por_semente, lado_de, 3), "_n": n}
    t_antes = {**metricas(teto, lado_de, 0), "_n": n}
    t_depois = {**metricas(teto, lado_de, 3), "_n": n}
    saida = [
        f"# Baseline (ranking {amb.ranking.versao}, conversa {amb.conversa.versao}, sugestões ligadas)",
        "",
        *tabela_metricas(
            f"Ruído calibrado, {n_sementes} sementes (média e faixa mínimo–máximo):",
            m_antes,
            m_depois,
        ),
        "",
        *tabela_metricas(
            "Sem ruído (respostas sempre pela ficha; o teto da ficha):", t_antes, t_depois
        ),
    ]
    destino = amb.saida / "baseline.md"
    destino.write_text("\n".join(saida) + "\n", encoding="utf-8")
    gravar_por_caso(amb.saida / "baseline_por_caso.tsv", por_semente, lado_de)
    gravar_por_caso(amb.saida / "baseline_sem_ruido_por_caso.tsv", teto, lado_de)
    print("\n".join(saida))


# -- evidências de cada falha ------------------------------------------------------------------

K_GRANDE = 600  # todas as candidatas que a busca junta (o piso real é menor: ~100 a 150)


def ranking_completo(ranking):
    """O mesmo ranking com o vetor devolvendo todas as espécies (para a posição pelo vetor só)."""
    v = ranking.vetor.model_copy(update={"candidatos": 2000, "documentos": 1500, "especies": 450})
    return ranking.model_copy(update={"vetor": v})


@dataclass
class Execucao:
    """Uma simulação de um caso, com o que a análise precisa ver."""

    caso: Caso
    posicoes: list[int | None]  # depois de 0, 1, ... perguntas (posição exata; None = não achada)
    passos: list[Passo]
    perguntas: list  # os objetos Pergunta de cada passo
    atributos_finais: object
    candidatas: list  # as candidatas da última busca, todas
    candidatas_0: list  # as da primeira busca (antes das perguntas)


def executar(
    amb: Ambiente, caso: Caso, vetor, semente: int, calibrado=True, ranking=None
) -> Execucao:
    ranking = ranking or amb.ranking
    cfg = amb.conversa.perguntas
    rng = random.Random(f"{semente}:{caso.id}")
    ficha = carregar_fichas(amb.db, [caso.esperada])[caso.esperada].atributos
    sug = amb.sugeridas[caso.id]
    atributos, feitas, passos, escolhidas, posicoes = caso.atributos, [], [], [], []
    candidatas_0 = None
    while True:
        consulta = Consulta.model_construct(
            descricao=caso.descricao, atributos=atributos, recusadas=[]
        )
        candidatas = buscar(
            amb.db, consulta, vetor, ranking, amb.regras, amb.conversa.busca.modo,
            k=K_GRANDE, sugeridas=sug,
        )  # fmt: skip
        candidatas_0 = candidatas_0 or candidatas
        posicoes.append(posicao(candidatas, caso.esperada))
        if len(feitas) == cfg.maximo:
            break
        primeiras = candidatas[: cfg.candidatas]
        fichas = carregar_fichas(amb.db, [c.species_id for c in primeiras])
        escolha = escolher(
            [(c.nota_final, fichas[c.species_id].atributos) for c in primeiras],
            atributos, cfg, amb.regras, feitas,
        )  # fmt: skip
        if escolha is None:
            posicoes += [posicoes[-1]] * (cfg.maximo - len(feitas))
            break
        resposta, tipo = responder(
            escolha.pergunta,
            ficha,
            amb.regras,
            amb.conversa.simulacao,
            rng,
            0.0,
            calibrado,
            amb.voc,
        )
        feitas.append(escolha.pergunta.chave)
        passos.append(Passo(escolha.pergunta.chave, escolha.ganho, resposta, tipo))
        escolhidas.append(escolha.pergunta)
        atributos = aplicar_resposta(atributos, escolha.pergunta, resposta, amb.voc, amb.regras)
    return Execucao(caso, posicoes, passos, escolhidas, atributos, candidatas, candidatas_0)


def bucket(p: int | None) -> str:
    if p is None:
        return "não achada"
    return "1–3" if p <= 3 else "4–10" if p <= 10 else "11–40" if p <= 40 else "41+"


def descrever(c) -> dict:
    return {
        "species_id": c.species_id,
        "nome": c.nome,
        "origem": c.origem,
        "nota_final": round(c.nota_final, 3),
        "vetor_norm": round(c.nota_vetor_normalizada, 3),
        "pontos": round(c.pontos, 3),
        "bonus": round(c.bonus, 3),
        "detalhes": {
            d.atributo: f"{d.resultado} ({', '.join(d.bateram) or '-'} | {', '.join(d.conflitaram) or '-'})"
            for d in c.detalhes
        },
    }


def evidencia(
    amb: Ambiente, ex: Execucao, sem_ruido: Execucao, posicao_vetor: int | None, taxonomia: dict
) -> dict:
    caso = ex.caso
    esperada = caso.esperada
    ficha = carregar_fichas(amb.db, [esperada])[esperada].atributos
    por_id = {c.species_id: c for c in ex.candidatas}
    certa = por_id.get(esperada)
    topo3 = ex.candidatas[:3]
    terceira = topo3[2] if len(topo3) > 2 else None
    inicial = [
        {
            "atributo": d.atributo,
            "resultado": d.resultado,
            "bateram": d.bateram,
            "conflitaram": d.conflitaram,
        }
        for d in pontuar(caso.atributos, ficha, amb.ranking, amb.regras)
    ]
    passos = []
    for passo in ex.passos:
        passos.append(
            {
                "pergunta": passo.pergunta,
                "resposta": ", ".join(passo.resposta) or "não sei",
                "ruido": passo.ruido or "",
                "conflita_com_a_ficha": passo.ruido == "conflito",
            }
        )
    sug = amb.sugeridas[caso.id]
    ev = {
        "caso": caso.id,
        "esperada": esperada,
        "familia": taxonomia[esperada]["familia"],
        "descricao": caso.descricao,
        "posicoes": ex.posicoes,
        "pos_0": ex.posicoes[0],
        "pos_final": ex.posicoes[-1],
        "faixa_final": bucket(ex.posicoes[-1]),
        "faixa_0": bucket(ex.posicoes[0]),
        "pos_sem_ruido": sem_ruido.posicoes[-1],
        "posicao_so_vetor": posicao_vetor,
        "origem_0": next((c.origem for c in ex.candidatas_0 if c.species_id == esperada), None),
        "sugerida_pos": sug.index(esperada) + 1 if esperada in sug else None,
        "sugestoes_do_llm": sug[:5],
        "inicial": inicial,
        "passos": passos,
        "certa": descrever(certa) if certa else None,
        "topo3": [descrever(c) for c in topo3],
    }
    if certa and terceira:
        ev["dif_para_3a"] = {
            "nota_final": round(terceira.nota_final - certa.nota_final, 3),
            "vetor": round(
                amb.ranking.vetor.peso
                * (terceira.nota_vetor_normalizada - certa.nota_vetor_normalizada),
                3,
            ),
            "pontos": round(terceira.pontos - certa.pontos, 3),
            "bonus": round(terceira.bonus - certa.bonus, 3),
        }
        perdas = []
        por_atr_t = {d.atributo: d for d in terceira.detalhes}
        for d in certa.detalhes:
            outro = por_atr_t.get(d.atributo)
            if outro is not None and outro.pontos - d.pontos > 1e-9:
                perdas.append((d.atributo, round(outro.pontos - d.pontos, 3)))
        ev["perde_por_atributo"] = sorted(perdas, key=lambda x: -x[1])
        sem_bonus = sorted(ex.candidatas, key=lambda c: (-(c.nota_final - c.bonus), c.species_id))
        ev["pos_sem_bonus_das_sugestoes"] = next(
            (i for i, c in enumerate(sem_bonus, start=1) if c.species_id == esperada), None
        )
    ev["parecida_no_topo3"] = [
        c.species_id
        for c in topo3
        if c.species_id != esperada
        and taxonomia.get(c.species_id, {}).get("genero") == taxonomia[esperada]["genero"]
    ] or [
        c.species_id
        for c in topo3
        if c.species_id != esperada
        and taxonomia.get(c.species_id, {}).get("familia") == taxonomia[esperada]["familia"]
    ]
    return ev


def taxonomia_de(db, ids) -> dict:
    return {
        d["species_id"]: d["taxonomia"]
        for d in db["species"].find(
            {"species_id": {"$in": list(ids)}}, {"species_id": 1, "taxonomia": 1}
        )
    }


def cmd_evidencias(amb: Ambiente) -> None:
    lado_de = ler_divisao(amb.saida)
    vetores = amb.cache.vetores([c.descricao for c in amb.casos])
    completo = ranking_completo(amb.ranking)
    execs, sem_ruido, so_vetor = {}, {}, {}
    for caso, vetor in zip(amb.casos, vetores, strict=True):
        execs[caso.id] = executar(amb, caso, vetor, 0, True)
        sem_ruido[caso.id] = executar(amb, caso, vetor, 0, False)
        consulta = Consulta.model_construct(
            descricao=caso.descricao, atributos=caso.atributos, recusadas=[]
        )
        achadas = buscar(amb.db, consulta, vetor, completo, amb.regras, "vetor", k=K_GRANDE)
        so_vetor[caso.id] = posicao(achadas, caso.esperada)
    ids = {c.species_id for e in execs.values() for c in e.candidatas[:3]} | {
        c.esperada for c in amb.casos
    }
    taxo = taxonomia_de(amb.db, ids)
    todas = {
        c.id: evidencia(amb, execs[c.id], sem_ruido[c.id], so_vetor[c.id], taxo) for c in amb.casos
    }
    (amb.saida / "evidencias_todas.json").write_text(
        json.dumps({"lado": lado_de, "casos": todas}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    falhas = {k: v for k, v in todas.items() if v["pos_final"] is None or v["pos_final"] > 3}
    print(
        f"{len(falhas)} falhas em {len(todas)} (semente 0); "
        f"calibração {sum(lado_de[k] == 'calibracao' for k in falhas)}, "
        f"teste {sum(lado_de[k] == 'teste' for k in falhas)}"
    )


# -- vizinhança dos valores (E2) e conflitos (tarefa 3) --------------------------------------

CADEIA_ONDE = ("chao", "vegetacao_baixa", "meio_da_arvore", "copa")  # no_ar e na_agua: sem vizinho
DIMENSOES_ORDENADAS = ("comprimento", "espessura", "curvatura")  # `extra` é categórico


def vizinhos(atributo: str, valor: str, regras) -> set[str]:
    """Os valores ao lado de `valor` numa escala ordenada (vazio se o atributo não é ordenado)."""
    if atributo == "onde_estava":
        ordem = list(CADEIA_ONDE)
    elif atributo.startswith("bico:") and atributo.split(":")[1] in DIMENSOES_ORDENADAS:
        ordem = list(regras.arquivo.dimensoes[atributo.split(":")[1]])
    else:
        return set()
    if valor not in ordem:
        return set()
    i = ordem.index(valor)
    return {ordem[j] for j in (i - 1, i + 1) if 0 <= j < len(ordem)}


def conjunto_da_ficha(atributo: str, ficha, regras) -> list[str] | None:
    """Os valores que a ficha aceita para o atributo; None se a ficha não tem o dado."""
    if atributo == "onde_estava":
        return list(ficha.onde_estava) if ficha.onde_estava else None
    dim = atributo.split(":")[1]
    if ficha.bico_tipo is None:
        return None
    return list(getattr(regras.perfil(ficha.bico_tipo), dim) or []) or None


def classificar(atributo: str, informado: str, aceitos: list[str] | None, regras) -> str:
    if aceitos is None:
        return "sem_dado"
    if informado in aceitos:
        return "bate"
    return "vizinho" if vizinhos(atributo, informado, regras) & set(aceitos) else "distante"


def itens_informados(atributos) -> list[tuple[str, str]]:
    """(atributo, valor) do bico e do onde_estava que a descrição traz."""
    saida = [("onde_estava", v) for v in atributos.onde_estava or []]
    if atributos.bico is not None:
        for dim in ("comprimento", "espessura", "curvatura"):
            if v := getattr(atributos.bico, dim):
                saida.append((f"bico:{dim}", v))
        saida += [("bico:extra", v) for v in atributos.bico.extra or []]
    return saida


PASSO_RE = re.compile(r"^(?P<chave>[^=]+)=(?P<valor>.*?)(?: \((?P<tag>[a-z_]+)\))?$")


def registros_de_conflito(amb: Ambiente) -> list[dict]:
    """Cada valor de bico ou onde_estava, visto na anotação real e nas respostas simuladas."""
    fichas = carregar_fichas(amb.db, sorted({c.esperada for c in amb.casos}))
    lado_de = ler_divisao(amb.saida)
    saida = []
    for caso in amb.casos:
        ficha = fichas[caso.esperada].atributos
        for atributo, valor in itens_informados(caso.atributos):
            aceitos = conjunto_da_ficha(atributo, ficha, amb.regras)
            saida.append(
                dict(fonte="anotacao", semente="", caso=caso.id, esperada=caso.esperada,
                     lado=lado_de[caso.id], atributo=atributo, informado=valor,
                     ficha=",".join(aceitos or []) or "(sem dado)",
                     resultado=classificar(atributo, valor, aceitos, amb.regras), tag="")
            )  # fmt: skip
    caminho = amb.saida / "baseline_por_caso.tsv"
    for linha in csv.DictReader(caminho.open(encoding="utf-8"), delimiter="\t"):
        ficha = fichas[linha["esperada"]].atributos
        for passo in filter(None, linha["perguntas"].split(" | ")):
            m = PASSO_RE.match(passo)
            chave, valores, tag = m["chave"], m["valor"], m["tag"] or ""
            if not (chave == "onde_estava" or chave.startswith("bico:")) or valores == "não sei":
                continue
            aceitos = conjunto_da_ficha(chave, ficha, amb.regras)
            for valor in valores.split(","):
                saida.append(
                    dict(fonte="simulada", semente=linha["semente"], caso=linha["caso"],
                         esperada=linha["esperada"], lado=linha["lado"], atributo=chave,
                         informado=valor, ficha=",".join(aceitos or []) or "(sem dado)",
                         resultado=classificar(chave, valor, aceitos, amb.regras), tag=tag)
                )  # fmt: skip
    return saida


def matriz_md(registros: list[dict], atributo: str, n_sementes: int) -> list[str]:
    """Linhas = o que a ficha aceita; colunas = o valor informado; célula = real / simulado."""
    sub = [r for r in registros if r["atributo"] == atributo]
    fichas = sorted({r["ficha"] for r in sub})
    colunas = sorted({r["informado"] for r in sub})
    marca = {"bate": "✓", "vizinho": "~", "distante": "✗", "sem_dado": "?"}
    linhas = [
        f"**{atributo}** (célula: anotações reais / respostas simuladas por semente; "
        "✓ bate, ~ vizinho, ✗ distante, ? ficha sem dado)",
        "",
        "| ficha aceita ↓ · informado → | " + " | ".join(colunas) + " |",
        "|---|" + "---|" * len(colunas),
    ]
    for f in fichas:
        celulas = []
        for c in colunas:
            reais = [
                r
                for r in sub
                if r["ficha"] == f and r["informado"] == c and r["fonte"] == "anotacao"
            ]
            sims = [
                r
                for r in sub
                if r["ficha"] == f and r["informado"] == c and r["fonte"] == "simulada"
            ]
            if not reais and not sims:
                celulas.append("")
                continue
            res = (reais or sims)[0]["resultado"]
            celulas.append(f"{marca[res]} {len(reais)} / {len(sims) / n_sementes:.1f}")
        linhas.append(f"| {f} | " + " | ".join(celulas) + " |")
    return linhas


def cmd_conflitos(amb: Ambiente, n_sementes: int) -> None:
    registros = registros_de_conflito(amb)
    destino = amb.saida / "conflitos_bico_onde.tsv"
    with destino.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(registros[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(registros)
    saida = ["# Conflitos de bico e onde_estava", ""]
    for fonte in ("anotacao", "simulada"):
        sub = [r for r in registros if r["fonte"] == fonte]
        por = Counter((r["atributo"].split(":")[0], r["resultado"]) for r in sub)
        div = n_sementes if fonte == "simulada" else 1
        saida.append(f"{fonte}: " + "; ".join(
            f"{a} {res}={n / div:.1f}" for (a, res), n in sorted(por.items())))  # fmt: skip
    saida.append("")
    for atributo in (
        "onde_estava",
        "bico:comprimento",
        "bico:espessura",
        "bico:curvatura",
        "bico:extra",
    ):
        saida += [*matriz_md(registros, atributo, n_sementes), ""]
    (amb.saida / "conflitos.md").write_text("\n".join(saida), encoding="utf-8")
    print("\n".join(saida))


# -- experimentos E1 e E2 ----------------------------------------------------------------------


@contextlib.contextmanager
def tolerancia_de_vizinhos(regras, credito_bico: float, credito_onde: float):
    """E2: o valor vizinho (escala ordenada) vale `credito` em vez de conflito, só em bico e onde_estava."""
    original = pont._itens

    def com_vizinhos(nome, atributos, ficha, regras_bico):
        itens = original(nome, atributos, ficha, regras_bico)
        if nome == "onde_estava" and credito_onde:
            aceitos = set(ficha.onde_estava or [])
            return [
                (
                    r,
                    credito_onde
                    if v == pont.CONFLITO and vizinhos("onde_estava", r, regras) & aceitos
                    else v,
                )
                for r, v in itens
            ]
        if nome == "bico" and credito_bico and ficha.bico_tipo:
            perfil = regras_bico.perfil(ficha.bico_tipo)
            novos = []
            for r, v in itens:
                if v == pont.CONFLITO:
                    dim, valor = r.split(":", 1)
                    aceitos = set(getattr(perfil, dim) or [])
                    if vizinhos(f"bico:{dim}", valor, regras) & aceitos:
                        v = credito_bico
                novos.append((r, v))
            return novos
        return itens

    pont._itens = com_vizinhos
    try:
        yield
    finally:
        pont._itens = original


@contextlib.contextmanager
def bonus_condicionado(fator: float):
    """E4 (extra, proposto pelos dados): o bônus de uma sugerida do LLM é multiplicado por
    `fator ** conflitos`, em que `conflitos` é o número de atributos informados que a ficha dela
    contradiz. Sem conflito, o bônus é o de hoje."""
    original = retriever.reordenar

    def reordenar_condicionado(entradas, atributos, ranking, regras_bico, bonus=None):
        if not bonus:
            return original(entradas, atributos, ranking, regras_bico, bonus)
        sem = original(entradas, atributos, ranking, regras_bico, None)
        conflitos = {p.species_id: sum(d.resultado == "conflita" for d in p.detalhes) for p in sem}
        ajustado = {sid: b * fator ** conflitos.get(sid, 0) for sid, b in bonus.items()}
        return original(entradas, atributos, ranking, regras_bico, ajustado)

    retriever.reordenar = reordenar_condicionado
    try:
        yield
    finally:
        retriever.reordenar = original


def variantes(amb: Ambiente) -> dict[str, tuple]:
    """nome -> (ranking, gerenciador de contexto que liga o patch da variante)."""
    base = amb.ranking
    v = {"base": (base, contextlib.nullcontext)}
    for pen in (0.0, 0.1, 0.25, 0.75, 1.0):
        v[f"e1_pen{pen:g}"] = (
            base.model_copy(update={"penalidade_conflito": pen}),
            contextlib.nullcontext,
        )
    for cred in (0.25, 0.5, 0.75):
        v[f"e2_cred{cred:g}"] = (
            base,
            lambda cred=cred: tolerancia_de_vizinhos(amb.regras, cred, cred),
        )
    for fator in (0.0, 0.5):
        v[f"e4_fator{fator:g}"] = (base, lambda fator=fator: bonus_condicionado(fator))
    return v


def cmd_experimentos(amb: Ambiente, lado: str, nomes: list[str], n_sementes: int) -> None:
    lado_de = ler_divisao(amb.saida)
    casos = [c for c in amb.casos if lado_de[c.id] == lado]
    todas = variantes(amb)
    nomes = nomes or list(todas)
    for nome in nomes:
        ranking, contexto = todas[nome]
        destino = amb.saida / f"exp_{lado}_{nome}.json"
        if destino.exists():
            print(f"{nome}: já existe, pulado")
            continue
        t0 = time.time()
        por_semente = {}
        with contexto():
            for s in range(n_sementes):
                por_semente[s] = simular(
                    amb.db, casos, amb.cache, ranking, amb.voc, amb.regras, amb.conversa,
                    amb.sugeridas, 0.0, s, True,
                )  # fmt: skip
        dados = {
            "variante": nome,
            "lado": lado,
            "ranking": amb.ranking.versao,
            "casos": [c.id for c in casos],
            "pos": {
                str(s): {sim.caso.id: sim.posicoes for sim in sims}
                for s, sims in por_semente.items()
            },
        }
        destino.write_text(json.dumps(dados), encoding="utf-8")
        pos0 = [sim.posicoes[0] for sim in por_semente[0]]
        pos3 = [[sim.posicoes[3] for sim in sims] for sims in por_semente.values()]
        print(
            f"{nome:12s} {lado}: 0 perguntas top3 {topo(pos0, 3):.0%} top10 {topo(pos0, 10):.0%} | "
            f"3 perguntas top3 {statistics.mean(topo(p, 3) for p in pos3):.0%} "
            f"({min(topo(p, 3) for p in pos3):.0%}–{max(topo(p, 3) for p in pos3):.0%}) "
            f"top10 {statistics.mean(topo(p, 10) for p in pos3):.0%} [{time.time() - t0:.0f}s]",
            flush=True,
        )


# -- E3: estimativa de custo (NÃO chama a API) -----------------------------------------------

SISTEMA_E3 = (
    "Você reordena candidatas de ave para a identificação de uma ave descrita por uma pessoa. "
    "Receba a descrição, os atributos que a pessoa informou ou respondeu e 10 candidatas, cada "
    "uma com os atributos da ficha. Ordene da mais para a menos provável. Use só as candidatas e só "
    "os atributos fornecidos. A pessoa pode ter errado a percepção por pouco (uma cor vizinha, "
    "um tamanho ou um bico ao lado): uma divergência pequena pesa menos que uma grande. "
    'Responda um JSON {"ordem": [10 species_id, sem repetir]} com exatamente as 10 candidatas.'
)


def ficha_para_prompt(ficha, regras) -> dict:
    perfil = regras.perfil(ficha.bico_tipo) if ficha.bico_tipo else None
    bico = (
        {d: list(getattr(perfil, d) or []) for d in ("comprimento", "espessura", "curvatura")}
        if perfil
        else None
    )
    cores_parte = [f"{c.parte}:{c.cor}" for c in ficha.cores_por_parte or []]
    campos = {
        "tamanho": ficha.tamanho, "cores": ficha.cores, "cores_por_parte": cores_parte,
        "marcas": ficha.marcas, "bico": bico, "cauda": ficha.cauda,
        "onde_estava": ficha.onde_estava, "ambiente": ficha.ambiente,
        "o_que_fazia": ficha.o_que_fazia, "grupo": ficha.grupo,
    }  # fmt: skip
    return {k: v for k, v in campos.items() if v}


def prompt_e3(amb: Ambiente, ex: Execucao, fichas: dict) -> str:
    candidatas = ex.candidatas[:10]
    corpo = {
        "descricao": ex.caso.descricao,
        "atributos_informados": ex.atributos_finais.model_dump(exclude_none=True, mode="json"),
        "candidatas": [
            {
                "species_id": c.species_id,
                "nome": c.nome,
                "ficha": ficha_para_prompt(fichas[c.species_id].atributos, amb.regras),
            }
            for c in candidatas
        ],
    }
    return json.dumps(corpo, ensure_ascii=False)


def cmd_e3_estimativa(amb: Ambiente, n_sementes: int) -> None:
    vetores = dict(
        zip(
            (c.id for c in amb.casos),
            amb.cache.vetores([c.descricao for c in amb.casos]),
            strict=True,
        )
    )
    unicos: dict[str, int] = {}
    por_caso_semente = 0
    exemplo = None
    for s in range(n_sementes):
        for caso in amb.casos:
            ex = executar(amb, caso, vetores[caso.id], s, True)
            fichas = carregar_fichas(amb.db, [c.species_id for c in ex.candidatas[:10]])
            texto = prompt_e3(amb, ex, fichas)
            chave = hashlib.sha256(texto.encode()).hexdigest()
            unicos.setdefault(chave, len(texto))
            por_caso_semente += 1
            exemplo = exemplo or texto
    n = len(unicos)
    chars_usuario = statistics.mean(unicos.values())
    # ~3,3 caracteres por token em português com JSON (sem tiktoken instalado: é uma aproximação)
    tokens_entrada = (chars_usuario + len(SISTEMA_E3)) / 3.3
    tokens_saida = 140  # 10 species_id em JSON
    resumo = {
        "sementes": n_sementes,
        "execucoes_caso_semente": por_caso_semente,
        "chamadas_unicas": n,
        "tokens_entrada_por_chamada": round(tokens_entrada),
        "tokens_saida_por_chamada": tokens_saida,
        "tokens_entrada_total": round(n * tokens_entrada),
        "tokens_saida_total": n * tokens_saida,
    }
    (amb.saida / "e3_estimativa.json").write_text(json.dumps(resumo, indent=1), encoding="utf-8")
    (amb.saida / "e3_prompt_exemplo.md").write_text(
        "## system\n\n"
        + SISTEMA_E3
        + "\n\n## usuário (exemplo)\n\n```json\n"
        + (exemplo or "")
        + "\n```\n",
        encoding="utf-8",
    )
    print(json.dumps(resumo, indent=1))


# -- resultados dos experimentos ---------------------------------------------------------------


def carregar_exp(saida: Path, lado: str, nome: str) -> dict:
    return json.loads((saida / f"exp_{lado}_{nome}.json").read_text(encoding="utf-8"))


def taxas_por_semente(exp: dict, passo: int, k: int) -> list[float]:
    return [topo([pos[passo] for pos in por_caso.values()], k) for por_caso in exp["pos"].values()]


def fracao_por_caso(exp: dict, passo: int, k: int) -> dict[str, float]:
    """Por caso: a fração das sementes em que a espécie certa ficou no top k."""
    casos = exp["casos"]
    n = len(exp["pos"])
    return {
        c: sum(
            1
            for por_caso in exp["pos"].values()
            if por_caso[c][passo] is not None and por_caso[c][passo] <= k
        )
        / n
        for c in casos
    }


def tabela_experimentos(saida: Path, lado: str, nomes: list[str]) -> list[str]:
    base = carregar_exp(saida, lado, "base")
    t3_base = taxas_por_semente(base, 3, 3)
    faixa_base = max(t3_base) - min(t3_base)
    f3_base = fracao_por_caso(base, 3, 3)
    linhas = [
        f"Lado: **{lado}** ({len(base['casos'])} descrições, {len(base['pos'])} sementes). "
        f"Faixa do baseline em top 3 com 3 perguntas: {min(t3_base):.0%}–{max(t3_base):.0%} "
        f"(largura {faixa_base:.0%}): só vale como real uma melhora maior que isso.",
        "",
        "| Variante | Top 3 (0 perg.) | Top 10 (0 perg.) | Top 3 (3 perg.) média (faixa) | Δ top 3 | Δ top 10 | Real? | Casos +/− (top 3) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    t10_base = statistics.mean(taxas_por_semente(base, 3, 10))
    for nome in nomes:
        exp = carregar_exp(saida, lado, nome)
        t3 = taxas_por_semente(exp, 3, 3)
        t10 = statistics.mean(taxas_por_semente(exp, 3, 10))
        d3 = statistics.mean(t3) - statistics.mean(t3_base)
        d10 = t10 - t10_base
        f3 = fracao_por_caso(exp, 3, 3)
        ganha = sum(1 for c in f3 if f3[c] - f3_base[c] >= 0.3)
        perde = sum(1 for c in f3 if f3_base[c] - f3[c] >= 0.3)
        zero3 = topo([pos[0] for pos in exp["pos"]["0"].values()], 3)
        zero10 = topo([pos[0] for pos in exp["pos"]["0"].values()], 10)
        real = "sim" if d3 > faixa_base else ("não" if nome != "base" else "—")
        linhas.append(
            f"| {nome} | {zero3:.0%} | {zero10:.0%} | {faixa(t3)} | {d3 * 100:+.1f} p.p. | "
            f"{d10 * 100:+.1f} p.p. | {real} | +{ganha} / −{perde} |"
        )
    return linhas


def cmd_resultados(amb: Ambiente, lado: str, nomes: list[str]) -> None:
    saida = tabela_experimentos(amb.saida, lado, nomes or ["base"])
    print("\n".join(saida))


# -- tabela das falhas -------------------------------------------------------------------------

CAUSAS = (
    "ficha_incompleta", "ficha_errada", "conflito_percepcao", "vocabulario", "texto_busca",
    "bonus_ebird", "especie_parecida",
)  # fmt: skip


def ler_causas(saida: Path) -> dict[str, dict]:
    with (saida / "causas_revisadas.tsv").open(encoding="utf-8") as fh:
        return {r["caso"]: r for r in csv.DictReader(fh, delimiter="\t")}


def passos_curtos(passos: list[dict]) -> str:
    return " | ".join(
        f"{p['pergunta']}={p['resposta']}"
        + (
            " (conflita)"
            if p["conflita_com_a_ficha"]
            else " (não sei simulado)"
            if p["ruido"] == "nao_sei"
            else ""
        )
        for p in passos
    )


def cmd_falhas(amb: Ambiente) -> None:
    dados = json.loads((amb.saida / "evidencias_todas.json").read_text(encoding="utf-8"))
    causas = ler_causas(amb.saida)
    falhas = {
        c: e for c, e in dados["casos"].items() if e["pos_final"] is None or e["pos_final"] > 3
    }
    faltam = sorted(set(falhas) - set(causas))
    if faltam:
        raise SystemExit(f"sem causa revisada: {faltam}")
    colunas = ["caso", "lado", "esperada", "familia", "pos_0", "posicoes", "pos_final", "faixa", "sem_ruido_passa",
               "llm_sugeriu_a_certa", "posicao_so_vetor", "perguntas_e_respostas", "causa_principal",
               "causas_secundarias", "justificativa"]  # fmt: skip
    linhas = []
    for c, e in falhas.items():
        faixa_ = "4–10" if e["pos_final"] is not None and e["pos_final"] <= 10 else "fora do top 10"
        linhas.append(
            [c, dados["lado"][c], e["esperada"], e["familia"], e["pos_0"] or "—",
             "→".join(str(x or "—") for x in e["posicoes"]), e["pos_final"] or "não achada", faixa_,
             "sim" if e["pos_sem_ruido"] is not None and e["pos_sem_ruido"] <= 3 else "não",
             "sim" if e["sugerida_pos"] else "não", e["posicao_so_vetor"],
             passos_curtos(e["passos"]), causas[c]["causa_principal"], causas[c]["causas_secundarias"],
             causas[c]["justificativa"]]
        )  # fmt: skip
    linhas.sort(key=lambda r: (r[1], r[0]))
    with (amb.saida / "falhas.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(colunas)
        w.writerows(linhas)

    def contar(lado: str | None) -> Counter:
        c = Counter()
        for r in linhas:
            if lado is None or r[1] == lado:
                c[(r[12], r[7])] += 1
        return c

    todas = sorted({r[12] for r in linhas}, key=lambda x: (x.startswith("outro"), x))
    ordem = [c for c in CAUSAS] + [c for c in todas if c not in CAUSAS]
    saida = [
        "| Causa principal | 4–10 | fora do top 10 | total | calibração | teste |",
        "|---|---|---|---|---|---|",
    ]
    tot = contar(None)
    for causa in ordem:
        a, b = tot[(causa, "4–10")], tot[(causa, "fora do top 10")]
        cal = sum(contar("calibracao")[(causa, f)] for f in ("4–10", "fora do top 10"))
        tes = sum(contar("teste")[(causa, f)] for f in ("4–10", "fora do top 10"))
        nota = " (não existe no código)" if causa == "bonus_ebird" else ""
        saida.append(f"| `{causa}`{nota} | {a} | {b} | {a + b} | {cal} | {tes} |")
    n410 = sum(1 for r in linhas if r[7] == "4–10")
    saida.append(f"| **total** | {n410} | {len(linhas) - n410} | {len(linhas)} | "
                 f"{sum(1 for r in linhas if r[1] == 'calibracao')} | {sum(1 for r in linhas if r[1] == 'teste')} |")  # fmt: skip
    sec = Counter(s.strip().split("(")[0] for r in linhas for s in r[13].split(";") if s.strip())
    saida += [
        "",
        "Causas secundárias citadas: " + ", ".join(f"`{k}` {v}" for k, v in sec.most_common()),
    ]
    (amb.saida / "falhas_resumo.md").write_text("\n".join(saida) + "\n", encoding="utf-8")
    print("\n".join(saida))


def principal(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("divisao")
    b = sub.add_parser("baseline")
    b.add_argument("--sementes", type=int, default=10)
    sub.add_parser("evidencias")
    e = sub.add_parser("experimentos")
    e.add_argument("--lado", choices=("calibracao", "teste"), required=True)
    e.add_argument("--variantes", nargs="*", default=[])
    e.add_argument("--sementes", type=int, default=10)
    sub.add_parser("falhas")
    r = sub.add_parser("resultados")
    r.add_argument("--lado", choices=("calibracao", "teste"), required=True)
    r.add_argument("--variantes", nargs="*", default=[])
    e3 = sub.add_parser("e3-estimativa")
    e3.add_argument("--sementes", type=int, default=5)
    c = sub.add_parser("conflitos")
    c.add_argument("--sementes", type=int, default=10)
    args = ap.parse_args(argv)
    amb = montar()
    if args.cmd == "divisao":
        cmd_divisao(amb)
    elif args.cmd == "baseline":
        cmd_baseline(amb, args.sementes)
    elif args.cmd == "evidencias":
        cmd_evidencias(amb)
    elif args.cmd == "conflitos":
        cmd_conflitos(amb, args.sementes)
    elif args.cmd == "falhas":
        cmd_falhas(amb)
    elif args.cmd == "resultados":
        cmd_resultados(amb, args.lado, args.variantes)
    elif args.cmd == "e3-estimativa":
        cmd_e3_estimativa(amb, args.sementes)
    elif args.cmd == "experimentos":
        cmd_experimentos(amb, args.lado, args.variantes, args.sementes)


if __name__ == "__main__":
    try:
        principal()
    except CustoBloqueado as e:
        sys.exit(f"custo novo bloqueado: {e}\nEstime o custo e peça aprovação antes de liberar.")
