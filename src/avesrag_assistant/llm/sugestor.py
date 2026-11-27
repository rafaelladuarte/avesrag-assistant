"""Espécies sugeridas pelo LLM para uma descrição (`c04_candidatas`, plano_assistant2.md §13.2).

Os nomes devolvidos são casados com a base pelo nome científico e, se não der,
pelo nome popular (sem acento, minúsculo, hífen como espaço). O que não casa
com nenhuma espécie com documento de busca é descartado: o LLM não cria
espécie. As que casam vão para a busca como candidatas, com o bônus de
`ranking.sugestoes`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario
from avesrag_builder.db.collections import SPECIES, SPECIES_RETRIEVAL_DOCS
from avesrag_builder.llm.base import Pedido
from pymongo.database import Database

from avesrag_assistant.armazem import Armazem
from avesrag_assistant.llm.cache import ARQUIVO_PADRAO, CacheLLM
from avesrag_assistant.llm.contratos import C04Candidatas, schema_do_contrato
from avesrag_assistant.llm.prompts import PromptChat, carregar_prompt
from avesrag_assistant.settings import Settings


def normalizar_nome(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().replace("-", " ").split())


def indice_de_nomes(especies: Iterable[tuple[str, str, list[str]]]) -> dict[str, str]:
    """(species_id, nome científico, nomes populares) → nome normalizado → species_id."""
    indice: dict[str, str] = {}
    for sid, cientifico, populares in especies:
        for nome in (cientifico, *populares):
            indice.setdefault(normalizar_nome(nome), sid)
    return indice


def indice_da_base(db: Database) -> dict[str, str]:
    """O índice de nomes das espécies com documento de busca (as que a busca alcança)."""
    ids = db[SPECIES_RETRIEVAL_DOCS].distinct("species_id", {})
    cursor = db[SPECIES].find(
        {"species_id": {"$in": list(ids)}},
        {"_id": 0, "species_id": 1, "nome_cientifico": 1, "nomes.pt": 1},
    )
    return indice_de_nomes(
        (d["species_id"], d["nome_cientifico"], (d.get("nomes") or {}).get("pt") or [])
        for d in sorted(cursor, key=lambda d: d["species_id"])
    )


class Sugestor:
    def __init__(
        self,
        prompt: PromptChat,
        cache: CacheLLM,
        vocabulario: Vocabulario,
        regras_bico: RegrasBico,
        modelo_padrao: str,
    ):
        self.prompt = prompt
        self.cache = cache
        self.modelo = prompt.modelo or modelo_padrao
        self._system = prompt.render_system(vocabulario, regras_bico)
        self._schema = schema_do_contrato("C04Candidatas", vocabulario, regras_bico)

    def sugerir(self, mensagem: str) -> C04Candidatas:
        pedido = Pedido(
            id=f"chat:{self.prompt.id}",
            modelo=self.modelo,
            system=self._system,
            entrada=self.prompt.render_entrada(mensagem=mensagem.strip()),
            schema=self._schema,
            temperatura=self.prompt.temperatura,
        )
        return self.cache.gerar(
            self.prompt.id, self.prompt.versao, pedido, C04Candidatas.model_validate_json
        )

    def especies(self, mensagem: str, indice: Mapping[str, str]) -> list[str]:
        """Os species_id sugeridos, na ordem e sem repetidos; os que não casam saem."""
        ids = casar(self.sugerir(mensagem), indice)
        return list(dict.fromkeys(i for i in ids if i))


def casar(sugestao: C04Candidatas, indice: Mapping[str, str]) -> list[str | None]:
    """O species_id de cada sugestão, na ordem; None quando não casa com a base."""
    saida: list[str | None] = []
    for e in sugestao.especies:
        sid = indice.get(normalizar_nome(e.nome_cientifico)) or indice.get(
            normalizar_nome(e.nome_popular)
        )
        saida.append(sid)
    return saida


def criar_sugestor(
    settings: Settings,
    vocabulario: Vocabulario,
    regras_bico: RegrasBico,
    armazem: Armazem | None = None,
) -> Sugestor:
    """Sugestor com o prompt do `config/prompts/`, o fornecedor da OpenAI e o cache em `reports/`."""
    from avesrag_builder.llm.openai import OpenAIFornecedor

    prompt = carregar_prompt(settings.CONFIG_DIR / "prompts" / "c04_candidatas.yaml", vocabulario)
    settings.require("OPENAI_API_KEY")
    fornecedor = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    cache = CacheLLM(armazem or settings.REPORTS_DIR / ARQUIVO_PADRAO, fornecedor)
    return Sugestor(prompt, cache, vocabulario, regras_bico, settings.LLM_MODEL)
