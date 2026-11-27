"""Guardrails da API (plano_assistant4.md §4.3).

Entrada: tamanho, caracteres de controle, padrões de injeção (P3a) e a checagem
de moderação, antes de o texto chegar ao orquestrador. A moderação falha
fechada (P3d): se o endpoint não responde, a mensagem não passa. Saída: só os campos da lista abaixo saem da API;
um campo novo na resposta falha no teste e, em produção, vira erro (A11: o
texto do WikiAves e o `_meta` nunca saem).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Protocol

log = logging.getLogger(__name__)

_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INVISIVEIS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")


class EntradaRecusada(ValueError):
    """A mensagem não passa; o texto é o que a pessoa vê."""

    def __init__(self, motivo: str, texto: str):
        super().__init__(texto)
        self.motivo = motivo
        self.texto = texto
        self.detalhe = ""  # para o log, nunca para a pessoa


class ModeracaoIndisponivel(RuntimeError):
    """O endpoint de moderação não respondeu: a mensagem não pode ser liberada (P3d)."""


class Moderador(Protocol):
    def sinaliza(self, texto: str) -> bool:
        """True se a mensagem deve ser barrada; `ModeracaoIndisponivel` se não deu para checar."""
        ...


class ModeradorOpenAI:
    """Endpoint de moderação da OpenAI (gratuito). Falha fechada: se não responder, levanta
    `ModeracaoIndisponivel` e a mensagem não passa. O LLM é do mesmo fornecedor, então com a
    moderação fora do ar a conversa já estaria quase toda fora também."""

    def __init__(self, api_key: str, modelo: str = "omni-moderation-latest"):
        from openai import OpenAI

        self.cliente = OpenAI(api_key=api_key, timeout=10, max_retries=1)
        self.modelo = modelo

    def sinaliza(self, texto: str) -> bool:
        try:
            r = self.cliente.moderations.create(model=self.modelo, input=texto)
            return bool(r.results[0].flagged)
        except Exception as e:
            log.warning("moderação indisponível, a mensagem não segue: %s: %s", type(e).__name__, e)
            raise ModeracaoIndisponivel(str(e)) from e


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


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento nem caracteres invisíveis, espaços colapsados: o que os padrões leem."""
    sem_invisiveis = _INVISIVEIS.sub("", texto)
    decomposto = unicodedata.normalize("NFKD", sem_invisiveis)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def compilar_padroes(padroes: Sequence[str]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in padroes]


def detectar_injecao(texto: str, padroes: Sequence[re.Pattern[str]]) -> str | None:
    """O padrão que casou com o texto (para o log), ou `None`. Não chama ninguém."""
    normal = normalizar(texto)
    for padrao in padroes:
        if padrao.search(normal):
            return padrao.pattern
    return None


def conferir_injecao(texto: str, padroes: Sequence[re.Pattern[str]], recusada: str) -> None:
    """`EntradaRecusada("injecao", ...)` se um padrão casar; o `detalhe` leva o padrão ao log."""
    padrao = detectar_injecao(texto, padroes)
    if padrao is not None:
        recusa = EntradaRecusada("injecao", recusada)
        recusa.detalhe = padrao
        raise recusa


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
    "nome_en",
    "nome_cientifico",
    "familia",
    "foto",
    "fotos",
    "ilustracao",
    "textos",
    "como_reconhecer",
    "ficha",
    "url_wikiaves",
}
CAMPOS_CATALOGO = {"species_id", "nome", "nome_cientifico", "familia", "familia_popular", "foto"}
CAMPOS_TEXTO = {"chave", "titulo", "texto", "origem"}
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
    for foto in e.get("fotos") or []:
        _conferir(foto, CAMPOS_FOTO, "fotos")
    for texto in e.get("textos") or []:
        _conferir(texto, CAMPOS_TEXTO, "texto")
    if e.get("ilustracao"):
        _conferir(e["ilustracao"], CAMPOS_ILUSTRACAO, "ilustração")
    for lista in ("como_reconhecer", "ficha"):
        for item in e.get(lista) or []:
            _conferir(item, CAMPOS_ITEM, lista)
    return e


def conferir_catalogo(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in itens:
        _conferir(item, CAMPOS_CATALOGO, "catálogo")
        if item.get("foto"):
            _conferir(item["foto"], CAMPOS_FOTO, "foto do catálogo")
    return itens


CAMPOS_EDICAO = {"species_id", "campos"}
CAMPOS_DO_CAMPO = {
    "caminho",
    "rotulo",
    "tipo",
    "valor",
    "opcoes",
    "manual",
    "automatico",
    "sugestao",
}
CAMPOS_MANUAL = {"por", "em", "motivo"}


def conferir_edicao(e: dict[str, Any]) -> dict[str, Any]:
    _conferir(e, CAMPOS_EDICAO, "edição")
    for c in e["campos"]:
        _conferir(c, CAMPOS_DO_CAMPO, "campo de edição")
        if c.get("manual"):
            _conferir(c["manual"], CAMPOS_MANUAL, "correção manual")
    return e
