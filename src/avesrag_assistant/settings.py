"""Configuração do assistant, lida do ambiente e do arquivo `.env` na raiz.

Nada é lido no import: use `get_settings()`. As chaves de API são opcionais no
carregamento e cobradas por `Settings.require()` no comando que as usa, para
que `--help` e os testes funcionem sem `.env`.

O `.env` do builder não é lido: o assistant tem o seu próprio.
"""

from functools import lru_cache
from pathlib import Path

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

    # Caminhos
    BUILDER_DIR: Path = ROOT_DIR.parent / "avesrag-dataset-builder"
    CONFIG_DIR: Path = ROOT_DIR / "config"
    AVALIACAO_DIR: Path = ROOT_DIR / "avaliacao"
    REPORTS_DIR: Path = ROOT_DIR / "reports"

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
