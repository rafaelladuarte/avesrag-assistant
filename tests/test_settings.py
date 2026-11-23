import pytest

from avesrag_assistant.settings import ROOT_DIR, MissingSettingError, Settings, get_settings


def test_carrega_sem_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.OPENAI_API_KEY is None
    assert settings.EMBEDDING_MODEL == "text-embedding-3-large"


def test_builder_dir_padrao_e_a_pasta_irma():
    settings = Settings(_env_file=None)
    assert settings.BUILDER_DIR == ROOT_DIR.parent / "avesrag-dataset-builder"
    assert settings.builder_config_dir == settings.BUILDER_DIR / "config"


def test_require_cobra_chave_vazia(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingSettingError, match="OPENAI_API_KEY"):
        Settings(_env_file=None).require("OPENAI_API_KEY")


def test_require_aceita_chave_definida(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-teste")
    Settings(_env_file=None).require("OPENAI_API_KEY")


def test_get_settings_tem_cache():
    assert get_settings() is get_settings()
