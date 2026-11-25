"""O que a página vê de uma sessão (plano_assistant4.md §3 e §5.4).

Monta a resposta só com os campos permitidos de `guardrails.py`: nenhum texto do
WikiAves nem do `_meta` da ficha. As respostas prontas da pergunta (D7) saem do
código, não do LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario

from avesrag_assistant.api.exibicao import exibir
from avesrag_assistant.api.guardrails import conferir_visao
from avesrag_assistant.conversa.estado import EstadoConversa
from avesrag_assistant.conversa.orquestrador import ROTULOS

SAUDACAO = "Olá! Me conte como era a ave, do jeito que você contaria para um amigo."
NAO_REPAREI = "Não reparei"


def opcoes_da_pergunta(chave: str, vocabulario: Vocabulario, regras: RegrasBico) -> list[str]:
    """As respostas prontas, tiradas do vocabulário do atributo que o código escolheu (D7)."""
    atributo, _, parte = chave.partition(":")
    if atributo == "cores_por_parte":
        valores = vocabulario.valores("cores")
    elif atributo == "bico":
        valores = regras.arquivo.dimensoes.get(parte, [])
    elif atributo in vocabulario.atributos:
        valores = vocabulario.valores(atributo)
    else:
        valores = []
    return [exibir(v) for v in valores] + [NAO_REPAREI]


def _rotulos(atributos: list[str]) -> list[str]:
    return [ROTULOS.get(a, a) for a in atributos]


def montar_visao(
    e: EstadoConversa,
    fotos: Mapping[str, Mapping[str, str]],
    perguntas_maximo: int,
    mensagens_por_sessao: int,
    vocabulario: Vocabulario,
    regras_bico: RegrasBico,
) -> dict[str, Any]:
    """`fotos`: species_id → {url, credito, link} (o `midia.foto` da ficha)."""
    pergunta = None
    if e.etapa == "coleta" and e.pergunta_texto:
        pergunta = {
            "texto": e.pergunta_texto,
            "opcoes": opcoes_da_pergunta(e.pergunta or "", vocabulario, regras_bico),
            "numero": e.perguntas_na_rodada,
            "de": perguntas_maximo,
        }
    candidatas = []
    if e.etapa == "confirmacao":
        for i, c in enumerate(e.candidatas, start=1):
            foto = fotos.get(c.species_id)
            candidatas.append(
                {
                    "numero": i,
                    "species_id": c.species_id,
                    "nome": c.nome,
                    "nome_cientifico": c.nome_cientifico,
                    "foto": (
                        {"url": foto["url"], "credito": foto["credito"], "link": foto["link"]}
                        if foto
                        else None
                    ),
                    "combina": _rotulos(c.bateram),
                    "nao_combina": _rotulos(c.conflitaram),
                }
            )
    confirmada = None
    if e.confirmada:
        achada = next((c for c in e.candidatas if c.species_id == e.confirmada), None)
        if achada:
            confirmada = {
                "species_id": achada.species_id,
                "nome": achada.nome,
                "nome_cientifico": achada.nome_cientifico,
            }
    enviadas = sum(1 for m in e.mensagens if m.papel == "usuario")
    visao = {
        "id": e.id,
        "etapa": e.etapa,
        "mensagens": [{"papel": "assistente", "texto": SAUDACAO}]
        + [{"papel": m.papel, "texto": m.texto} for m in e.mensagens],
        "pergunta": pergunta,
        "candidatas": candidatas,
        "confirmada": confirmada,
        "voto": e.voto,
        "mensagens_restantes": max(mensagens_por_sessao - enviadas, 0),
    }
    return conferir_visao(visao)
