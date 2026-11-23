import pytest
from avesrag_builder.db.collections import SPECIES, SPECIES_REGISTRY, SPECIES_RETRIEVAL_DOCS

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


class ColecaoFalsa:
    def __init__(self, docs):
        self.docs = docs
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return iter(self.docs)

    def find(self, filtro, projecao=None):
        ids = set(filtro["species_id"]["$in"])
        return iter(d for d in self.docs if d["species_id"] in ids)

    def distinct(self, campo, filtro):
        ids = set(filtro["species_id"]["$in"])
        return sorted({d[campo] for d in self.docs if d["species_id"] in ids})


class BancoFalso:
    def __init__(self, docs, fichas, registry=None):
        """`registry`: species_id do registry; por padrão, os das fichas e dos documentos."""
        if registry is None:
            registry = {d["species_id"] for d in [*docs, *fichas]}
        self.colecoes = {
            SPECIES_RETRIEVAL_DOCS: ColecaoFalsa(docs),
            SPECIES: ColecaoFalsa(fichas),
            SPECIES_REGISTRY: ColecaoFalsa([{"species_id": s} for s in sorted(registry)]),
        }

    def __getitem__(self, nome):
        return self.colecoes[nome]


# Base de busca e fichas de mentira para o BancoFalso.
DOCS = [
    {"species_id": "sp_verde", "dominio": "aparencia", "nota": 0.80},
    {"species_id": "sp_amarela", "dominio": "aparencia", "nota": 0.72},
    {"species_id": "sp_amarela", "dominio": "voz_habitos", "nota": 0.78},
    {"species_id": "sp_sem_ficha", "dominio": "aparencia", "nota": 0.79},
    {"species_id": "sp_cinza", "dominio": "comportamento_ambiente", "nota": 0.70},
]
FICHAS = [
    {
        "species_id": "sp_verde",
        "nome_cientifico": "Ave verde",
        "nomes": {"pt": ["ave-verde"]},
        "atributos": {"cores": ["verde"]},
    },
    {
        "species_id": "sp_amarela",
        "nome_cientifico": "Ave amarela",
        "nomes": {"pt": ["ave-amarela", "outra"]},
        "atributos": {"cores": ["amarelo"], "marcas": ["sobrancelha"]},
    },
    {"species_id": "sp_cinza", "nome_cientifico": "Ave cinza"},
]
