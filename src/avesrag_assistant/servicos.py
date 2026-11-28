"""Monta os serviços da conversa (Mongo, ranking, regras e os prompts do chat).

Usado pelo CLI (`conversar`, `moderar`, `servir`) e pela fábrica do app da Vercel. Os caches de
LLM e de embeddings vão para o arquivo em `reports/` ou para o MongoDB, conforme
`settings.armazenamento`.
"""

from __future__ import annotations

from avesrag_builder.core.vocabulario import RegrasBico, Vocabulario

from avesrag_assistant.armazem import armazem_do_ambiente
from avesrag_assistant.busca.cache import COLECAO_PADRAO as COLECAO_EMBEDDINGS
from avesrag_assistant.busca.cache import criar_cache
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.conversa.config import carregar_conversa
from avesrag_assistant.conversa.escopo import carregar_escopo_config, criar_detector
from avesrag_assistant.conversa.orquestrador import Servicos
from avesrag_assistant.db import get_db
from avesrag_assistant.llm.cache import COLECAO_PADRAO as COLECAO_LLM
from avesrag_assistant.llm.extrator import criar_extrator
from avesrag_assistant.llm.pergunta import criar_redator
from avesrag_assistant.llm.sugestor import criar_sugestor, indice_da_base
from avesrag_assistant.settings import Settings


def construir_servicos(
    settings: Settings,
    voc: Vocabulario,
    regras: RegrasBico,
    *,
    db=None,
    extrator=criar_extrator,
    redator=criar_redator,
    sugestor=criar_sugestor,
    cache_embeddings=criar_cache,
) -> Servicos:
    """Os criadores podem ser trocados (o CLI e os testes passam os seus)."""
    ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
    conversa = carregar_conversa(settings.CONFIG_DIR / "conversa.yaml", voc)
    db = db or get_db()
    # Em arquivo, cada criador usa o seu arquivo em reports/; em mongo, a collection compartilhada.
    extra_llm: tuple = ()
    extra_emb: tuple = ()
    if settings.armazenamento == "mongo":
        extra_llm = (armazem_do_ambiente(settings, db, COLECAO_LLM, "cache_llm.json"),)
        extra_emb = (
            armazem_do_ambiente(settings, db, COLECAO_EMBEDDINGS, "cache_embeddings.json"),
        )
    cache = cache_embeddings(settings, *extra_emb)
    indice = indice_da_base(db)
    escopo = carregar_escopo_config(settings.CONFIG_DIR / "escopo.yaml")
    return Servicos(
        db=db,
        ranking=ranking,
        conversa=conversa,
        vocabulario=voc,
        regras_bico=regras,
        extrator=extrator(settings, voc, regras, *extra_llm),
        redator=redator(settings, voc, regras, *extra_llm),
        vetor=cache.vetor,
        sugestor=sugestor(settings, voc, regras, *extra_llm),
        indice=indice,
        fala_de_ave=criar_detector(escopo.palavras_de_ave, indice),
    )
