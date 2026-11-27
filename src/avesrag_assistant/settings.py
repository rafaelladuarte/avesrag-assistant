"""Configuração do assistant, lida do ambiente e do arquivo `.env` na raiz.

Nada é lido no import: use `get_settings()`. As chaves de API são opcionais no
carregamento e cobradas por `Settings.require()` no comando que as usa, para
que `--help` e os testes funcionem sem `.env`.

O `.env` do builder não é lido: o assistant tem o seu próprio.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class MissingSettingError(RuntimeError):
    """Uma variável obrigatória para o comando não está definida."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chaves de API (obrigatórias por comando)
    OPENAI_API_KEY: SecretStr | None = None

    # MongoDB montado pelo builder (o mesmo banco)
    MONGODB_URI: str = "mongodb://localhost:27017/?directConnection=true"
    MONGODB_DATABASE: str = "avesrag"

    # Embeddings: tem de ser o mesmo modelo que o builder usou nos documentos de busca
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    # Modelo dos prompts do chat que não fixam o seu (Modelo de Dados > Modelo de linguagem)
    LLM_MODEL: str = "gpt-6-luna"

    # Caminhos
    # Ao lado deste repositório; no pacote da Vercel, o `builder/` copiado para dentro dele.
    BUILDER_DIR: Path = (
        ROOT_DIR / "builder"
        if (ROOT_DIR / "builder" / "config").is_dir()
        else ROOT_DIR.parent / "avesrag-dataset-builder"
    )
    CONFIG_DIR: Path = ROOT_DIR / "config"
    AVALIACAO_DIR: Path = ROOT_DIR / "avaliacao"
    REPORTS_DIR: Path = ROOT_DIR / "reports"
    # Convites do beta (só o hash do token); fora do git
    CONVITES_PATH: Path = ROOT_DIR / "config" / "convites.json"
    # O mesmo JSON do arquivo de convites, numa variável de ambiente (somente leitura): para
    # onde não há disco persistente, como a Vercel. Vale no lugar do arquivo.
    CONVITES_JSON: SecretStr | None = None

    # Onde ficam os caches, os contadores de uso e os limites: `arquivo` (reports/, um processo)
    # ou `mongo` (várias instâncias, como na Vercel). Vazio: `mongo` se estiver na Vercel.
    ARMAZENAMENTO: Literal["arquivo", "mongo"] | None = None
    # Curadoria das fichas pela página (traz o `fuse` do builder): vazio = só fora da Vercel.
    CURADORIA_HABILITADA: bool | None = None

    @property
    def armazenamento(self) -> Literal["arquivo", "mongo"]:
        return self.ARMAZENAMENTO or ("mongo" if os.environ.get("VERCEL") else "arquivo")

    @property
    def curadoria_habilitada(self) -> bool:
        if self.CURADORIA_HABILITADA is not None:
            return self.CURADORIA_HABILITADA
        return not os.environ.get("VERCEL")

    @property
    def builder_config_dir(self) -> Path:
        """Pasta `config/` do builder, com o `vocabulario.yaml` e o `regras_bico.yaml`."""
        return self.BUILDER_DIR / "config"

    def require(self, *names: str) -> None:
        """Levanta MissingSettingError se alguma das variáveis estiver vazia."""
        faltando = []
        for name in names:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value:
                faltando.append(name)
        if faltando:
            raise MissingSettingError(
                f"Defina {', '.join(faltando)} no ambiente ou no .env (veja .env.example)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
