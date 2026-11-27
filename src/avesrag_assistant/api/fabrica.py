"""Monta o app da API a partir do ambiente (`servir` e ponto de entrada da Vercel).

O que muda com `settings.armazenamento`:
- `arquivo` (padrão local): limites em memória, uso em `reports/uso.tsv`, caches em `reports/`;
- `mongo` (Vercel): limites, uso e caches no MongoDB, que valem para todas as instâncias.

Os convites vêm de `CONVITES_JSON` (variável de ambiente, só leitura) ou do arquivo.
A curadoria das fichas só entra se `settings.curadoria_habilitada` (traz o `fuse` do builder).
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import FastAPI

from avesrag_assistant import vocabulario
from avesrag_assistant.api.app import criar_app
from avesrag_assistant.api.config import carregar_api, carregar_familias
from avesrag_assistant.api.convites import Convites
from avesrag_assistant.api.guardrails import ModeradorOpenAI
from avesrag_assistant.api.limites import Limitador
from avesrag_assistant.api.limites_mongo import LimitadorMongo, UsoDiarioMongo
from avesrag_assistant.api.sessoes import RepositorioArquivo, RepositorioMongo
from avesrag_assistant.api.uso_diario import UsoDiario
from avesrag_assistant.servicos import construir_servicos
from avesrag_assistant.settings import Settings, get_settings


class SemConvites(RuntimeError):
    """Nenhum convite configurado: o beta não teria como autorizar ninguém."""


def convites_do_ambiente(settings: Settings) -> Convites:
    if settings.CONVITES_JSON is not None:
        convites = Convites(
            settings.CONVITES_PATH, json.loads(settings.CONVITES_JSON.get_secret_value())
        )
    else:
        convites = Convites(settings.CONVITES_PATH)
    if not convites.listar():
        raise SemConvites("Não há convites. Crie um: avesrag-assistant convite criar <nome>")
    return convites


def criar_app_do_ambiente(
    settings: Settings | None = None,
    sessoes_em: Literal["mongo", "arquivo"] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    voc, regras = vocabulario.carregar()
    config = carregar_api(settings.CONFIG_DIR / "api.yaml")
    familias = carregar_familias(settings.CONFIG_DIR / "familias.yaml")
    servicos = construir_servicos(settings, voc, regras)
    compartilhado = settings.armazenamento == "mongo"
    sessoes_em = sessoes_em or ("mongo" if compartilhado else "arquivo")
    if sessoes_em == "mongo":
        repositorio = RepositorioMongo(servicos.db, config.sessoes.ttl_dias)
        repositorio.garantir_indices()
    else:
        repositorio = RepositorioArquivo(settings.REPORTS_DIR / "sessoes")
    if compartilhado:
        limitador = LimitadorMongo(servicos.db, config.limites.requisicoes_por_ip_por_minuto)
        limitador.garantir_indices()
        uso_diario = UsoDiarioMongo(servicos.db)
    else:
        limitador = Limitador(config.limites.requisicoes_por_ip_por_minuto)
        uso_diario = UsoDiario(settings.REPORTS_DIR / "uso.tsv")
    curadoria = None
    if settings.curadoria_habilitada:
        from avesrag_assistant.api.curadoria import Curadoria

        curadoria = Curadoria(servicos.db, voc, regras, config_dir=settings.builder_config_dir)
        curadoria.preparar()
    settings.require("OPENAI_API_KEY")
    return criar_app(
        servicos,
        config,
        repositorio,
        convites_do_ambiente(settings),
        limitador,
        ModeradorOpenAI(settings.OPENAI_API_KEY.get_secret_value()),
        uso_diario=uso_diario,
        familias=familias,
        curadoria=curadoria,
    )
