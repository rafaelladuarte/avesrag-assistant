"""Quando uma mensagem fora do escopo fala de ave (planos/plano_guardrails.md §13).

O `c01` só diz "fora do escopo" (um booleano). Para a recusa saber se a pessoa perguntou algo
**sobre aves** (que a aplicação ainda não responde, mas vai) ou algo sem relação, o código
confere se a mensagem cita ave: uma palavra de `config/escopo.yaml` ou o nome de uma das fichas.
É uma regra de palavras, sem LLM e sem cache; erra nas bordas, e só escolhe entre dois textos.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PALAVRA = re.compile(r"[a-z0-9]+")


class EscopoInvalido(ValueError):
    """`config/escopo.yaml` com erro."""


class ConfigEscopo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versao: str
    palavras_de_ave: list[str] = Field(min_length=1)


def carregar_escopo_config(path: Path | str) -> ConfigEscopo:
    try:
        return ConfigEscopo.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError as e:
        raise EscopoInvalido(f"{path} não encontrado") from e
    except (yaml.YAMLError, ValidationError) as e:
        raise EscopoInvalido(f"{path}: {e}") from e


def palavras(texto: str) -> tuple[str, ...]:
    """Minúsculas, sem acento, só letras e números ("bem-te-vi" vira "bem", "te", "vi")."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return tuple(_PALAVRA.findall(sem_acento.lower()))


def _singular(ps: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(p[:-1] if len(p) > 3 and p.endswith("s") else p for p in ps)


def criar_detector(
    palavras_de_ave: Iterable[str], nomes_das_fichas: Iterable[str] = ()
) -> Callable[[str], bool]:
    """`texto -> bool`: a mensagem cita ave (palavra da lista ou nome de uma ficha)."""
    conhecidos = {palavras(n) for n in [*palavras_de_ave, *nomes_das_fichas]}
    conhecidos.discard(())
    maior = max((len(n) for n in conhecidos), default=0)

    def fala_de_ave(texto: str) -> bool:
        ps = palavras(texto)
        for variante in (ps, _singular(ps)):  # "aves" e "tucanos" também valem
            for tamanho in range(1, maior + 1):
                if any(
                    variante[i : i + tamanho] in conhecidos
                    for i in range(len(variante) - tamanho + 1)
                ):
                    return True
        return False

    return fala_de_ave
