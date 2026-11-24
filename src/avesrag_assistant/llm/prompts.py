"""Leitor validado dos prompts do chat (`config/prompts/*.yaml` do assistant).

Mesmo formato dos prompts do builder (Modelo de Dados > Regras comuns), mas a
entrada é a do chat (estado e mensagem), e não os textos do WikiAves. No
`system`, `{vocabulario}` recebe o bloco de valores permitidos do
`vocabulario.yaml` e `{bico}` as dimensões do `regras_bico.yaml`.
"""

from __future__ import annotations

import string
from pathlib import Path

from avesrag_builder.core.vocabulario import (
    ATRIBUTO_CORES_POR_PARTE,
    RegrasBico,
    Vocabulario,
    VocabularioInvalido,
    _erros_pydantic,
    _ler_yaml,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from avesrag_assistant.llm.contratos import ENTRADAS

PLACEHOLDERS_SYSTEM = {"vocabulario", "bico"}


class PromptInvalido(VocabularioInvalido):
    """Arquivo de prompt com erro; a mensagem aponta o caminho."""


def _placeholders(texto: str) -> set[str]:
    return {nome for _, nome, _, _ in string.Formatter().parse(texto) if nome is not None}


class PromptChat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    versao: str
    modelo: str | None = None  # sem modelo, vale o LLM_MODEL do settings
    temperatura: float | None = None  # null: o parâmetro não é enviado
    schema_: str = Field(alias="schema")
    vocabulario: list[str]
    system: str
    entrada: str

    @property
    def schema_nome(self) -> str:
        return self.schema_

    def render_system(self, vocabulario: Vocabulario, regras: RegrasBico) -> str:
        valores = {
            "vocabulario": vocabulario.para_prompt(self.vocabulario),
            "bico": "\n".join(
                f"- {dim}: {', '.join(vals)}" for dim, vals in regras.arquivo.dimensoes.items()
            ),
        }
        usados = _placeholders(self.system)
        return self.system.format(**{k: v for k, v in valores.items() if k in usados}).strip()

    def render_entrada(self, **campos: str) -> str:
        return self.entrada.format(**campos).strip()


def _erros_de_conteudo(p: PromptChat, vocabulario: Vocabulario) -> list[str]:
    erros = []
    if p.schema_nome not in ENTRADAS:
        erros.append(f"schema: {p.schema_nome!r} não existe ({', '.join(sorted(ENTRADAS))})")
        return erros
    conhecidos = {*vocabulario.atributos, ATRIBUTO_CORES_POR_PARTE}
    for nome in p.vocabulario:
        if nome not in conhecidos:
            erros.append(f"vocabulario: atributo {nome!r} não existe no vocabulario.yaml")
    no_system = _placeholders(p.system)
    if sobrando := no_system - PLACEHOLDERS_SYSTEM:
        erros.append(f"system: placeholders desconhecidos {sorted(sobrando)}")
    if ("vocabulario" in no_system) != bool(p.vocabulario):
        erros.append("system: {vocabulario} e a lista `vocabulario` andam juntos")
    na_entrada = _placeholders(p.entrada)
    if na_entrada != ENTRADAS[p.schema_nome]:
        erros.append(
            f"entrada: placeholders {sorted(na_entrada)}, "
            f"esperado {sorted(ENTRADAS[p.schema_nome])}"
        )
    return erros


def carregar_prompt(path: Path | str, vocabulario: Vocabulario) -> PromptChat:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise PromptInvalido(path, e.erros) from e
    try:
        prompt = PromptChat.model_validate(dados)
    except ValidationError as e:
        raise PromptInvalido(path, _erros_pydantic(e)) from e
    erros = _erros_de_conteudo(prompt, vocabulario)
    if prompt.id != path.stem:
        erros.append(f"id: {prompt.id!r} diferente do nome do arquivo ({path.stem!r})")
    if erros:
        raise PromptInvalido(path, erros)
    return prompt
