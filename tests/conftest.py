import pytest

from avesrag_assistant.settings import get_settings


@pytest.fixture(autouse=True)
def _settings_limpos():
    """Garante que cada teste leia os settings do zero."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
