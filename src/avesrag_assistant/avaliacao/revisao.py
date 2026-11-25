"""Fichas para revisão manual: onde os dados da ficha provavelmente estão errados.

Cruza quatro fontes de sinal e gera uma linha por (espécie, problema):

- **conflito observado**: o que a pessoa (ou o LLM) disse numa descrição de avaliação, ou
  numa sessão de conversa, conflita com a ficha da espécie certa. É o sinal mais forte: foi
  o que derrubou a espécie no ranking. Vindo de pessoa, a prioridade é alta; só de LLM, média.
- **coerência da ficha**: perfil de bico de tucano em ave de bico curto, cor composta
  ("cinza-azulado") reduzida à cor de base, ave aquática sem ambiente de água, comprimento e
  massa que discordam no tamanho, cores demais ou de menos, ficha incompleta.
- **backlog**: as fichas já anotadas no BACKLOGS.md (B7, B12, B13, B14), com a correção prevista.

Funções puras sobre dados já lidos; o banco só entra em `reunir_fichas`.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avesrag_builder.core.models import Atributos
from avesrag_builder.core.regras_fusao import RegrasFusao
from avesrag_builder.core.vocabulario import RegrasBico

from avesrag_assistant.busca.modelos import AtributosObservados
from avesrag_assistant.busca.pontuacao import Ranking, pontuar

ALTA, MEDIA, BAIXA = "alta", "media", "baixa"
ORDEM_PRIORIDADE = {ALTA: 0, MEDIA: 1, BAIXA: 2}

COLUNAS = [
    "prioridade",
    "species_id",
    "nome",
    "nome_cientifico",
    "familia",
    "sinal",
    "tipo",
    "campo",
    "valor_na_ficha",
    "detalhe",
    "evidencia",
    "sugestao",
    "no_backlog",
    "status_ficha",
    "revisado",
    "observacao",
]

# O que o BACKLOGS.md já registra (por nome científico): item e correção prevista.
BACKLOG: dict[str, tuple[str, str, str, str]] = {
    # nome científico: (item, campo, tipo, sugestão)
    "Megascops choliba": ("B12", "atributos.marcas", "lacuna", "acrescentar `topete` (os tufos)"),
    "Jacana jacana": (
        "B12",
        "atributos.ambiente",
        "lacuna",
        "acrescentar `beira_de_agua` no ambiente e `na_agua` em onde estava",
    ),
    "Colaptes campestris": (
        "B12",
        "atributos.cores",
        "lacuna",
        "acrescentar a cor `bege` e a marca `pintado`; o bico `insetos_em_troncos` é largo demais (B13)",
    ),
    "Sicalis flaveola": ("B12", "atributos.cores", "lacuna", "acrescentar `laranja`"),
    "Colaptes melanochloros": ("B12", "atributos.marcas", "lacuna", "acrescentar `listrado`"),
    "Pyrocephalus rubinus": ("B12", "atributos.marcas", "lacuna", "acrescentar `mascara`"),
    "Tyrannus savana": ("B12", "atributos.cauda", "lacuna", "acrescentar `bifurcada`"),
    "Dacnis cayana": (
        "B13",
        "atributos.bico_tipo",
        "bico",
        "bico curto e fino: o perfil `frugivoro` é o de um tucano",
    ),
    "Chloroceryle amazona": (
        "B13",
        "atributos.bico_tipo",
        "bico",
        "o perfil `pescador` é largo demais para este martim-pescador",
    ),
    "Accipiter striatus": (
        "B7/B14",
        "atributos.cores",
        "cor_composta",
        "o texto diz cinza-azulado e a regra do vocabulário guardou `azul`: provavelmente `cinza`",
    ),
}

FAMILIAS_AQUATICAS = {
    "Anatidae",
    "Anhimidae",
    "Anhingidae",
    "Aramidae",
    "Alcedinidae",
    "Ardeidae",
    "Charadriidae",
    "Ciconiidae",
    "Eurypygidae",
    "Heliornithidae",
    "Jacanidae",
    "Laridae",
    "Pandionidae",
    "Phalacrocoracidae",
    "Podicipedidae",
    "Rallidae",
    "Scolopacidae",
    "Threskiornithidae",
}
AMBIENTES_DE_AGUA = {"beira_de_agua", "brejo"}
# Cor composta com hífen no texto ("cinza-azulado"): a primeira palavra é a cor-base e o sufixo
# é o tom. Se a ficha guardou o tom em vez da base, a cor provavelmente está errada. Um tom
# sozinho ("barriga amarelada" → amarelo) é correto e não entra.
TONS = {
    "azulad": "azul",
    "esverdead": "verde",
    "esverdeado": "verde",
    "acinzentad": "cinza",
    "amarelad": "amarelo",
    "avermelhad": "vermelho",
    "arroxead": "roxo",
    "amarronzad": "marrom",
    "esbranquiçad": "branco",
    "enegrecid": "preto",
    "rosad": "rosa",
    "alaranjad": "laranja",
}
COR_COMPOSTA = re.compile(r"\b([a-zçãé]+)-(" + "|".join(TONS) + r")\w*", re.IGNORECASE)
# As cores do vocabulário: só um composto de cor-base conhecida entra ("bronze-esverdeado" não).
CORES_BASE = {"preto", "branco", "cinza", "marrom", "bege", "amarelo", "laranja", "vermelho",
              "verde", "azul", "roxo", "rosa"}  # fmt: skip
CORES_DEMAIS = 7


@dataclass
class Ficha:
    species_id: str
    nome: str
    nome_cientifico: str
    familia: str
    status: str
    atributos: dict[str, Any]
    comprimento_cm: float | None
    massa_g: float | None
    evidencias: list[tuple[str, str, str]] = field(default_factory=list)  # (campo, valor, texto)
    faltas: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Observacao:
    """O que uma pessoa disse, para comparar com a ficha da espécie que ela viu."""

    origem: str  # id do caso ou da sessão
    species_id: str
    atributos: AtributosObservados
    de_pessoa: bool
    trecho: str


@dataclass(frozen=True)
class Linha:
    prioridade: str
    species_id: str
    sinal: str
    tipo: str
    campo: str
    valor_na_ficha: str = ""
    detalhe: str = ""
    evidencia: str = ""
    sugestao: str = ""


# -- formatação -------------------------------------------------------------------


def _texto(valor: Any) -> str:
    if valor is None or valor == []:
        return ""
    if isinstance(valor, list):
        return ", ".join(
            f"{v['parte']}:{v['cor']}" if isinstance(v, Mapping) and "parte" in v else str(v)
            for v in valor
        )
    return str(valor)


def _campo_da_ficha(ficha: Ficha, nome: str) -> str:
    if nome == "bico":
        return _texto(ficha.atributos.get("bico_tipo"))
    return _texto(ficha.atributos.get(nome))


def _recortar(texto: str, tamanho: int = 140) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= tamanho else texto[: tamanho - 1] + "…"


# -- conflitos observados ---------------------------------------------------------


def conflitos_observados(
    fichas: Mapping[str, Ficha],
    observacoes: Iterable[Observacao],
    ranking: Ranking,
    regras_bico: RegrasBico,
) -> list[Linha]:
    """Uma linha por (espécie, atributo) que conflitou com a ficha em alguma observação."""
    por_chave: dict[tuple[str, str], list[tuple[Observacao, list[str]]]] = defaultdict(list)
    for obs in observacoes:
        ficha = fichas.get(obs.species_id)
        if ficha is None:
            continue
        atributos = Atributos.model_validate(ficha.atributos)
        for d in pontuar(obs.atributos, atributos, ranking, regras_bico):
            if d.resultado == "conflita":
                por_chave[(obs.species_id, d.atributo)].append((obs, d.conflitaram))
    linhas = []
    for (sid, atributo), itens in por_chave.items():
        ficha = fichas[sid]
        pessoa = any(o.de_pessoa for o, _ in itens)
        ditos = sorted({c for _, conf in itens for c in conf})
        evidencia = " | ".join(f"{o.origem}: {_recortar(o.trecho, 90)}" for o, _ in itens[:3])
        if len(itens) > 3:
            evidencia += f" | (+{len(itens) - 3})"
        linhas.append(
            Linha(
                prioridade=ALTA if pessoa else MEDIA,
                species_id=sid,
                sinal="avaliacao" if all(o.origem[0] in "cgfp" for o, _ in itens) else "sessao",
                tipo="conflito_observado",
                campo=atributo,
                valor_na_ficha=_campo_da_ficha(ficha, atributo),
                detalhe=(
                    f"{len(itens)} descrição(ões) conflitam; a pessoa disse: {', '.join(ditos)}"
                    + ("" if pessoa else " (só descrições geradas por LLM)")
                ),
                evidencia=evidencia,
                sugestao="ver se a ficha está errada ou se a descrição é que errou",
            )
        )
    return linhas


# -- coerência da ficha -------------------------------------------------------------


def coerencia(fichas: Iterable[Ficha], regras: RegrasFusao) -> list[Linha]:
    linhas: list[Linha] = []
    for f in fichas:
        a = f.atributos
        if a.get("bico_tipo") == "frugivoro" and f.familia != "Ramphastidae":
            linhas.append(
                Linha(
                    MEDIA,
                    f.species_id,
                    "coerencia",
                    "bico_suspeito",
                    "atributos.bico_tipo",
                    "frugivoro",
                    f"o perfil `frugivoro` é o de um tucano (médio a longo, grosso, largo) e a "
                    f"família é {f.familia}",
                    sugestao="conferir o bico da ave; se for curto ou fino, trocar o tipo (B13)",
                )
            )
        if f.familia in FAMILIAS_AQUATICAS:
            agua = set(a.get("ambiente") or []) & AMBIENTES_DE_AGUA or (
                "na_agua" in (a.get("onde_estava") or [])
            )
            if not agua:
                linhas.append(
                    Linha(
                        MEDIA,
                        f.species_id,
                        "coerencia",
                        "aquatica_sem_agua",
                        "atributos.ambiente",
                        _texto(a.get("ambiente")),
                        f"família aquática ({f.familia}) sem `beira_de_agua`, `brejo` nem `na_agua`",
                        sugestao="acrescentar o ambiente de água, se a ave vive nele",
                    )
                )
        for campo, valor, texto in f.evidencias:
            for achada in COR_COMPOSTA.finditer(texto):
                base, tom = achada.group(1).lower(), TONS[achada.group(2).lower()]
                if valor == tom and base != tom and base in CORES_BASE:
                    linhas.append(
                        Linha(
                            MEDIA,
                            f.species_id,
                            "coerencia",
                            "cor_composta",
                            campo,
                            valor,
                            f"o texto diz «{achada.group(0)}» (base: {base}, tom: {tom}) e a "
                            f"ficha guardou o tom `{valor}`",
                            evidencia=_recortar(texto, 120),
                            sugestao=f"a cor provavelmente é `{base}`; conferir",
                        )
                    )
                    break
        cores = a.get("cores") or []
        if not cores:
            linhas.append(
                Linha(
                    MEDIA,
                    f.species_id,
                    "coerencia",
                    "sem_cores",
                    "atributos.cores",
                    "",
                    "a ficha não tem nenhuma cor, e a cor é o atributo que mais separa as aves",
                    sugestao="preencher as cores",
                )  # fmt: skip
            )
        elif len(cores) >= CORES_DEMAIS:
            linhas.append(
                Linha(
                    BAIXA,
                    f.species_id,
                    "coerencia",
                    "cores_demais",
                    "atributos.cores",
                    _texto(cores),
                    f"{len(cores)} cores: pode ter cor que não é da ave",
                    sugestao="tirar as cores que só aparecem no ambiente ou em filhotes",
                )  # fmt: skip
            )
        if cores and not a.get("cores_por_parte"):
            linhas.append(
                Linha(
                    BAIXA,
                    f.species_id,
                    "coerencia",
                    "sem_cor_por_parte",
                    "atributos.cores_por_parte",
                    "",
                    "sem a cor de cada parte do corpo",
                    sugestao="preencher cabeça, barriga, dorso, asas e cauda, se for importante",
                )  # fmt: skip
            )
        if f.comprimento_cm and f.massa_g:
            por_comprimento = set(regras.tamanho_por_comprimento(f.comprimento_cm))
            por_massa = set(regras.tamanho_por_massa(f.massa_g))
            if por_comprimento.isdisjoint(por_massa):
                linhas.append(
                    Linha(
                        BAIXA,
                        f.species_id,
                        "coerencia",
                        "tamanho_discordante",
                        "atributos.tamanho",
                        _texto(a.get("tamanho")),
                        f"{f.comprimento_cm:g} cm sugere {sorted(por_comprimento)} e "
                        f"{f.massa_g:g} g sugere {sorted(por_massa)}",
                        sugestao="conferir o tamanho",
                    )  # fmt: skip
                )
        if f.status != "apta":
            linhas.append(
                Linha(
                    BAIXA,
                    f.species_id,
                    "coerencia",
                    "ficha_incompleta",
                    "_meta.dq_status",
                    f.status,
                    "fora da busca por falta de dados: " + "; ".join(f.faltas),
                    sugestao="completar o que falta para a espécie entrar na busca",
                )  # fmt: skip
            )
    return linhas


# -- backlog ---------------------------------------------------------------------------


def do_backlog(fichas: Iterable[Ficha]) -> list[Linha]:
    linhas = []
    for f in fichas:
        item = BACKLOG.get(f.nome_cientifico)
        if item:
            ref, campo, tipo, sugestao = item
            linhas.append(
                Linha(
                    ALTA,
                    f.species_id,
                    "backlog",
                    tipo,
                    campo,
                    _campo_da_ficha(f, campo.split(".")[-1]),
                    f"já registrada no BACKLOGS.md ({ref})",
                    sugestao=sugestao,
                )  # fmt: skip
            )
    return linhas


# -- montagem do CSV ----------------------------------------------------------------------


def montar_linhas(
    fichas: Mapping[str, Ficha],
    observacoes: Sequence[Observacao],
    ranking: Ranking,
    regras_bico: RegrasBico,
    regras: RegrasFusao,
) -> list[dict[str, str]]:
    todas = [
        *do_backlog(fichas.values()),
        *conflitos_observados(fichas, observacoes, ranking, regras_bico),
        *coerencia(fichas.values(), regras),
    ]
    # O mesmo (espécie, campo, tipo) vindo do backlog e da coerência não aparece duas vezes.
    vistas: set[tuple[str, str, str]] = set()
    unicas = []
    for linha in sorted(todas, key=lambda x: ORDEM_PRIORIDADE[x.prioridade]):
        chave = (linha.species_id, linha.campo, linha.tipo)
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(linha)
    n_sinais = defaultdict(int)
    for linha in unicas:
        n_sinais[linha.species_id] += 1
    unicas.sort(
        key=lambda x: (
            ORDEM_PRIORIDADE[x.prioridade],
            -n_sinais[x.species_id],
            fichas[x.species_id].nome.casefold(),
            x.campo,
        )
    )
    saida = []
    for x in unicas:
        f = fichas[x.species_id]
        saida.append(
            {
                "prioridade": x.prioridade,
                "species_id": x.species_id,
                "nome": f.nome,
                "nome_cientifico": f.nome_cientifico,
                "familia": f.familia,
                "sinal": x.sinal,
                "tipo": x.tipo,
                "campo": x.campo,
                "valor_na_ficha": x.valor_na_ficha,
                "detalhe": x.detalhe,
                "evidencia": x.evidencia,
                "sugestao": x.sugestao,
                "no_backlog": BACKLOG[f.nome_cientifico][0] if f.nome_cientifico in BACKLOG else "",
                "status_ficha": f.status,
                "revisado": "",
                "observacao": "",
            }
        )
    return saida


def gravar_csv(linhas: Sequence[Mapping[str, str]], destino: Path) -> Path:
    """UTF-8 com BOM e vírgula, para abrir direto no Excel e no LibreOffice."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUNAS)
        w.writeheader()
        w.writerows(linhas)
    return destino


def resumo(linhas: Sequence[Mapping[str, str]]) -> list[str]:
    from collections import Counter

    por_prioridade = Counter(x["prioridade"] for x in linhas)
    especies = {x["species_id"] for x in linhas}
    out = [
        f"{len(linhas)} linhas em {len(especies)} espécies "
        f"(alta {por_prioridade[ALTA]}, média {por_prioridade[MEDIA]}, baixa {por_prioridade[BAIXA]})"
    ]
    for tipo, n in Counter(x["tipo"] for x in linhas).most_common():
        out.append(f"  {tipo}: {n}")
    return out


def sessao_como_observacoes(estado_json: str, atributos: AtributosObservados) -> Observacao | None:
    """Uma sessão vira observação da espécie que a pessoa confirmou (ou, na moderada, da ave)."""
    dados = json.loads(estado_json)
    alvo = dados.get("confirmada") or dados.get("esperada")
    if not alvo or not atributos.informados():
        return None
    trecho = (dados.get("descricoes") or [""])[0]
    return Observacao(f"sessão {dados['id']}", alvo, atributos, True, trecho)


# -- leitura do banco ----------------------------------------------------------------------


def reunir_fichas(db: Any, regras: RegrasFusao) -> dict[str, Ficha]:
    """As fichas do banco, com as evidências de cor do `b01` e o que falta a cada incompleta."""
    from avesrag_builder.db.collections import SPECIES, SPECIES_EXTRACTIONS
    from avesrag_builder.validate import regras as validar
    from avesrag_builder.validate.pipeline import textos_b03

    docs = list(db[SPECIES].find({}))
    ids = [d["species_id"] for d in docs]
    textos = textos_b03(db, ids)
    evidencias: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for e in db[SPECIES_EXTRACTIONS].find(
        {"species_id": {"$in": ids}, "prompt": "b01_aparencia", "validacao.ok": True}
    ):
        saida = e.get("saida") or {}
        for item in saida.get("cores") or []:
            if item.get("evidencia"):
                evidencias[e["species_id"]].append(
                    ("atributos.cores", item["valor"], item["evidencia"])
                )
    fichas = {}
    for d in docs:
        atributos = d.get("atributos") or {}
        cores = set(atributos.get("cores") or [])
        status = (d.get("_meta") or {}).get("dq_status", "")
        fichas[d["species_id"]] = Ficha(
            species_id=d["species_id"],
            nome=((d.get("nomes") or {}).get("pt") or [d["nome_cientifico"]])[0],
            nome_cientifico=d["nome_cientifico"],
            familia=(d.get("taxonomia") or {}).get("familia", ""),
            status=status,
            atributos=atributos,
            comprimento_cm=atributos.get("comprimento_cm"),
            massa_g=(d.get("perfil") or {}).get("massa_g"),
            # só a evidência de uma cor que ficou na ficha
            evidencias=[x for x in evidencias[d["species_id"]] if x[1] in cores],
            faltas=[]
            if status == "apta"
            else validar.faltas(d, regras, textos.get(d["species_id"], set())),
        )
    return fichas
