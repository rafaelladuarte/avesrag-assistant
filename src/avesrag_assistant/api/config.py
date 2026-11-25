"""Configuração da API: `config/api.yaml`, validada ao carregar."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ConfigInvalida(ValueError):
    """O api.yaml não existe ou não é válido."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limites(_Base):
    caracteres_por_mensagem: int = Field(gt=0)
    mensagens_por_sessao: int = Field(gt=0)
    sessoes_por_convite_por_dia: int = Field(gt=0)
    mensagens_por_convite_por_dia: int = Field(gt=0)
    requisicoes_por_ip_por_minuto: int = Field(gt=0)
    chamadas_llm_por_dia: int = Field(gt=0)


class ConfigSessoes(_Base):
    ttl_dias: int = Field(gt=0)


class Textos(_Base):
    sem_convite: str
    limite_ip: str
    limite_sessao: str
    limite_sessoes_dia: str
    limite_mensagens_dia: str
    limite_diario: str
    mensagem_vazia: str
    mensagem_grande: str
    moderacao: str
    demorou: str
    erro: str
    sessao_nao_encontrada: str
    especie_nao_encontrada: str


class ConfigApi(_Base):
    versao: str
    historico: list[str] = Field(default_factory=list)
    limites: Limites
    sessoes: ConfigSessoes
    tempo_limite_segundos: float = Field(gt=0)
    textos: Textos


def carregar_api(path: Path) -> ConfigApi:
    try:
        bruto = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return ConfigApi.model_validate(bruto)
    except FileNotFoundError as e:
        raise ConfigInvalida(f"{path} não encontrado") from e
    except (yaml.YAMLError, ValidationError) as e:
        raise ConfigInvalida(f"{path}: {e}") from e


def carregar_familias(path: Path) -> dict[str, str]:
    """`config/familias.yaml`: família científica → nome popular, para o filtro do catálogo."""
    try:
        bruto = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        familias = bruto["familias"]
        if not isinstance(familias, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in familias.items()
        ):
            raise ValueError("familias deve mapear texto para texto não vazio")
        return familias
    except FileNotFoundError as e:
        raise ConfigInvalida(f"{path} não encontrado") from e
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as e:
        raise ConfigInvalida(f"{path}: {e}") from e
