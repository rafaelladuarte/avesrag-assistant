"""Guardrails da API (plano_assistant4.md §4.3).

Entrada: tamanho, caracteres de controle e a checagem de moderação, antes de o
texto chegar ao orquestrador. Saída: só os campos da lista abaixo saem da API;
um campo novo na resposta falha no teste e, em produção, vira erro (A11: o
texto do WikiAves e o `_meta` nunca saem).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class EntradaRecusada(ValueError):
    """A mensagem não passa; o texto é o que a pessoa vê."""

    def __init__(self, motivo: str, texto: str):
        super().__init__(texto)
        self.motivo = motivo
        self.texto = texto


class Moderador(Protocol):
    def sinaliza(self, texto: str) -> bool: ...


class ModeradorOpenAI:
    """Endpoint de moderação da OpenAI (gratuito). Se falhar, a mensagem passa e o log avisa."""

    def __init__(self, api_key: str, modelo: str = "omni-moderation-latest"):
        from openai import OpenAI

        self.cliente = OpenAI(api_key=api_key, timeout=10, max_retries=1)
        self.modelo = modelo

    def sinaliza(self, texto: str) -> bool:
        try:
            r = self.cliente.moderations.create(model=self.modelo, input=texto)
            return bool(r.results[0].flagged)
        except Exception as e:
            log.warning("moderação indisponível, a mensagem segue: %s: %s", type(e).__name__, e)
            return False


def limpar_entrada(
    texto: object, limite: int, vazio: str, grande: str, moderador: Moderador | None, recusada: str
) -> str:
    """O texto limpo, ou `EntradaRecusada`."""
    if not isinstance(texto, str):
        raise EntradaRecusada("tipo", vazio)
    limpo = _CONTROLE.sub("", texto).strip()
    if not limpo:
        raise EntradaRecusada("vazia", vazio)
    if len(limpo) > limite:
        raise EntradaRecusada("grande", grande.format(limite=limite))
    if moderador is not None and moderador.sinaliza(limpo):
        raise EntradaRecusada("moderacao", recusada)
    return limpo


# -- saída --------------------------------------------------------------------

CAMPOS_VISAO = {
    "id",
    "etapa",
    "mensagens",
    "pergunta",
    "candidatas",
    "confirmada",
    "voto",
    "mensagens_restantes",
}
CAMPOS_MENSAGEM = {"papel", "texto"}
CAMPOS_PERGUNTA = {"texto", "opcoes", "numero", "de"}
CAMPOS_CANDIDATA = {
    "numero",
    "species_id",
    "nome",
    "nome_cientifico",
    "foto",
    "combina",
    "nao_combina",
}
CAMPOS_FOTO = {"url", "credito", "link"}
CAMPOS_CONFIRMADA = {"species_id", "nome", "nome_cientifico"}
CAMPOS_ESPECIE = {
    "species_id",
    "nome",
    "nome_cientifico",
    "familia",
    "foto",
    "ilustracao",
    "como_reconhecer",
    "ficha",
    "url_wikiaves",
}
CAMPOS_ILUSTRACAO = {"url", "credito", "link", "rotulo"}
CAMPOS_ITEM = {"rotulo", "valor"}


class SaidaInvalida(RuntimeError):
    """A resposta tem um campo fora da lista permitida."""


def _conferir(dado: Any, permitidos: set[str], onde: str) -> None:
    extras = set(dado) - permitidos
    if extras:
        raise SaidaInvalida(f"campos fora da lista em {onde}: {sorted(extras)}")


def conferir_visao(v: dict[str, Any]) -> dict[str, Any]:
    _conferir(v, CAMPOS_VISAO, "visão")
    for m in v["mensagens"]:
        _conferir(m, CAMPOS_MENSAGEM, "mensagem")
    if v.get("pergunta"):
        _conferir(v["pergunta"], CAMPOS_PERGUNTA, "pergunta")
    for c in v["candidatas"]:
        _conferir(c, CAMPOS_CANDIDATA, "candidata")
        if c.get("foto"):
            _conferir(c["foto"], CAMPOS_FOTO, "foto")
    if v.get("confirmada"):
        _conferir(v["confirmada"], CAMPOS_CONFIRMADA, "confirmada")
    return v


def conferir_especie(e: dict[str, Any]) -> dict[str, Any]:
    _conferir(e, CAMPOS_ESPECIE, "espécie")
    if e.get("foto"):
        _conferir(e["foto"], CAMPOS_FOTO, "foto")
    if e.get("ilustracao"):
        _conferir(e["ilustracao"], CAMPOS_ILUSTRACAO, "ilustração")
    for lista in ("como_reconhecer", "ficha"):
        for item in e.get(lista) or []:
            _conferir(item, CAMPOS_ITEM, lista)
    return e
