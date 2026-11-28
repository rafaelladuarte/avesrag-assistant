"""Leitura e validação do `avaliacao/escopo.yaml` (planos/plano_guardrails.md §4.2, GR3).

O conjunto mede o `fora_de_escopo` do `c01`: o que ele barra por engano e o que deixa
passar. As descrições do `casos.yaml` não são copiadas para cá: entram por referência.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from avesrag_builder.core.vocabulario import VocabularioInvalido, _erros_pydantic, _ler_yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Grupo = Literal["resposta_curta", "descricao_atipica", "fora", "fronteira"]
Esperado = Literal["escopo", "fora"]
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
IdMensagem = Annotated[str, StringConstraints(pattern=r"^[rafb]\d{3}$")]


class EscopoInvalido(ValueError):
    """O arquivo de escopo não passou na validação."""

    def __init__(self, arquivo: Path | str, erros: list[str]):
        self.arquivo = str(arquivo)
        self.erros = erros
        lista = "\n".join(f"  - {e}" for e in erros)
        super().__init__(f"{self.arquivo}: {len(erros)} erro(s)\n{lista}")


class MensagemEscopo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: IdMensagem
    grupo: Grupo
    esperado: Esperado
    texto: Texto
    pergunta: Texto | None = None  # a última pergunta do assistente (só nas respostas curtas)
    revisar: bool = False  # fronteira: o esperado é proposta, e a decisão é da usuária
    nota: str | None = None


class EscopoArquivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: str
    mensagens: list[MensagemEscopo] = Field(min_length=1)


def _erros_cruzados(mensagens: list[MensagemEscopo]) -> list[str]:
    erros = []
    for id_, n in Counter(m.id for m in mensagens).items():
        if n > 1:
            erros.append(f"mensagens: id {id_!r} repetido")
    for m in mensagens:
        prefixo = {"resposta_curta": "r", "descricao_atipica": "a", "fora": "f", "fronteira": "b"}
        if not m.id.startswith(prefixo[m.grupo]):
            erros.append(f"{m.id}: o id de {m.grupo} começa com {prefixo[m.grupo]!r}")
        if (m.grupo == "resposta_curta") != (m.pergunta is not None):
            erros.append(f"{m.id}: só a resposta_curta tem pergunta, e ela precisa ter")
        if m.grupo == "fronteira" and not m.revisar:
            erros.append(f"{m.id}: a fronteira é para a usuária revisar (revisar: true)")
        if m.grupo in ("resposta_curta", "descricao_atipica") and m.esperado != "escopo":
            erros.append(f"{m.id}: {m.grupo} é sempre do escopo")
        if m.grupo == "fora" and m.esperado != "fora":
            erros.append(f"{m.id}: o grupo fora é sempre fora do escopo")
    return erros


def carregar_escopo(path: Path | str) -> list[MensagemEscopo]:
    path = Path(path)
    try:
        dados = _ler_yaml(path)
    except VocabularioInvalido as e:
        raise EscopoInvalido(path, e.erros) from e
    try:
        arquivo = EscopoArquivo.model_validate(dados)
    except ValidationError as e:
        raise EscopoInvalido(path, _erros_pydantic(e)) from e
    erros = _erros_cruzados(arquivo.mensagens)
    if erros:
        raise EscopoInvalido(path, erros)
    return arquivo.mensagens
