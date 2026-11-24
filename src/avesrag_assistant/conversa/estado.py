"""Estado de uma conversa, salvo num arquivo JSON por sessão (plano_assistant2.md §5).

O formato segue a collection `conversations` do Modelo de Dados, que só chega no
Assistant 4, com a API. Os atributos ficam como dicionário e são validados
contra o vocabulário pelo orquestrador ao retomar. A gravação é atômica: um
arquivo temporário renomeado por cima do anterior.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Etapa = Literal["coleta", "confirmacao", "fim"]


def agora() -> datetime:
    return datetime.now(UTC)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mensagem(_Base):
    papel: Literal["usuario", "assistente"]
    texto: str
    em: datetime = Field(default_factory=agora)


class CandidataApresentada(_Base):
    species_id: str
    nome: str
    nome_cientifico: str
    nota_final: float
    bateram: list[str] = Field(default_factory=list)  # atributos que bateram
    conflitaram: list[str] = Field(default_factory=list)


class EstadoConversa(_Base):
    id: str
    etapa: Etapa = "coleta"
    descricoes: list[str] = Field(default_factory=list)  # o que vira embedding e sugestões
    atributos: dict[str, Any] = Field(default_factory=dict)  # AtributosObservados
    descricao_para_busca: str | None = None  # do c01; guardada, mas não vai para a busca
    local: str | None = None
    data_observacao: str | None = None
    pergunta: str | None = None  # chave da pergunta à espera de resposta
    pergunta_texto: str | None = None
    perguntas_feitas: list[str] = Field(default_factory=list)  # chaves, na conversa toda
    perguntas_na_rodada: int = 0
    sugeridas: list[str] = Field(default_factory=list)  # species_id do c04
    candidatas: list[CandidataApresentada] = Field(default_factory=list)
    recusadas: list[str] = Field(default_factory=list)
    rodada_confirmacao: int = 0
    modo_busca: str | None = None  # o modo da última apresentação
    confirmada: str | None = None
    motivo_fim: str | None = None
    mensagens: list[Mensagem] = Field(default_factory=list)
    criada_em: datetime = Field(default_factory=agora)
    atualizada_em: datetime = Field(default_factory=agora)


def novo_id() -> str:
    return secrets.token_hex(4)


def caminho(diretorio: Path, sessao: str) -> Path:
    if not sessao.isalnum():
        raise ValueError(f"id de sessão inválido: {sessao!r} (só letras e números)")
    return diretorio / f"{sessao}.json"


def salvar(estado: EstadoConversa, diretorio: Path) -> Path:
    estado.atualizada_em = agora()
    destino = caminho(diretorio, estado.id)
    diretorio.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=diretorio, prefix=f".{estado.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(estado.model_dump_json(indent=2))
        os.replace(tmp, destino)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return destino


def carregar(diretorio: Path, sessao: str) -> EstadoConversa:
    path = caminho(diretorio, sessao)
    if not path.exists():
        raise FileNotFoundError(f"sessão {sessao!r} não encontrada em {diretorio}")
    return EstadoConversa.model_validate_json(path.read_text(encoding="utf-8"))
