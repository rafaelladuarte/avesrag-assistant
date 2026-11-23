"""Vocabulário e regras de bico, lidos do `config/` do builder.

Os arquivos são carregados pelos leitores do próprio builder, que validam o
conteúdo, mas pelo caminho do `BUILDER_DIR` do assistant: assim o `Settings`
do builder (e o `.env` dele) não entra em jogo.
"""

from pathlib import Path

from avesrag_builder.core.vocabulario import (
    RegrasBico,
    Vocabulario,
    carregar_regras_bico,
    carregar_vocabulario,
)

from avesrag_assistant.settings import get_settings


def carregar(config_dir: Path | None = None) -> tuple[Vocabulario, RegrasBico]:
    """Lê o `vocabulario.yaml` e o `regras_bico.yaml` da pasta `config/` do builder."""
    pasta = config_dir or get_settings().builder_config_dir
    vocabulario = carregar_vocabulario(pasta / "vocabulario.yaml")
    regras_bico = carregar_regras_bico(pasta / "regras_bico.yaml", vocabulario)
    return vocabulario, regras_bico
