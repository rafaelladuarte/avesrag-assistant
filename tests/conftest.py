import pytest

from avesrag_assistant.settings import get_settings


@pytest.fixture(autouse=True)
def _settings_limpos():
    """Garante que cada teste leia os settings do zero."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def vocab_regras():
    """Vocabulário e regras de bico reais, lidos do `config/` do builder (sem rede nem Mongo)."""
    from avesrag_assistant import vocabulario

    return vocabulario.carregar()


@pytest.fixture
def obs(vocab_regras):
    """Monta AtributosObservados validados: `obs(cores=["amarelo"])`."""
    from avesrag_assistant.busca.modelos import validar_atributos

    def _obs(**dados):
        return validar_atributos(dados, *vocab_regras)

    return _obs
