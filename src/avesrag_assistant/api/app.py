"""A API do beta fechado (plano_assistant4.md §3).

`criar_app` recebe tudo pronto (serviços, repositório, convites, limitador,
moderador), como a `Moderacao`, para os testes usarem peças falsas. Os
endpoints são funções comuns: o FastAPI as roda num pool de threads, e cada
sessão tem a sua trava, então duas mensagens da mesma sessão não correm juntas.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as TempoEsgotado
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from avesrag_assistant.api import especies, fila
from avesrag_assistant.api.config import ConfigApi
from avesrag_assistant.api.convites import Convites
from avesrag_assistant.api.curadoria_erro import ErroCuradoria
from avesrag_assistant.api.guardrails import (
    EntradaRecusada,
    ModeracaoIndisponivel,
    Moderador,
    compilar_padroes,
    conferir_injecao,
    limpar_entrada,
)
from avesrag_assistant.api.limites import Limitador
from avesrag_assistant.api.sessoes import RepositorioSessoes
from avesrag_assistant.api.uso_diario import UsoDiario
from avesrag_assistant.api.visao import montar_visao
from avesrag_assistant.conversa import estado as estados
from avesrag_assistant.conversa.orquestrador import Orquestrador, Servicos
from avesrag_assistant.uso import CONTADOR, Contador

if TYPE_CHECKING:  # a curadoria traz o `fuse` do builder; só entra quando está habilitada
    from avesrag_assistant.api.curadoria import Curadoria

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parents[1] / "web"
COOKIE = "avesrag_convite"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class ErroApi(Exception):
    def __init__(self, status: int, texto: str):
        super().__init__(texto)
        self.status = status
        self.texto = texto


class CorpoMensagem(BaseModel):
    texto: Any = None


class CorpoCorrecao(BaseModel):
    valor: Any = Field(...)  # obrigatório: null remove o campo da ficha
    motivo: str | None = None


class CorpoTexto(BaseModel):
    texto: str


class CorpoVoto(BaseModel):
    voto: str


def _ip(request: Request) -> str:
    """O IP de quem chama. Atrás do túnel local tudo chega de 127.0.0.1: aí vale o X-Forwarded-For."""
    host = request.client.host if request.client else "desconhecido"
    if host in LOOPBACK:
        encaminhado = request.headers.get("x-forwarded-for", "")
        if encaminhado.strip():
            return encaminhado.split(",")[0].strip()
    return host


def criar_app(
    servicos: Servicos,
    config: ConfigApi,
    repositorio: RepositorioSessoes,
    convites: Convites,
    limitador: Limitador,
    moderador: Moderador | None = None,
    contador: Contador = CONTADOR,
    dia: Callable[[], str] | None = None,
    uso_diario: UsoDiario | None = None,
    familias: Mapping[str, str] | None = None,
    curadoria: Curadoria | None = None,
) -> FastAPI:
    app = FastAPI(title="AvesRAG", docs_url=None, redoc_url=None, openapi_url=None)
    textos = config.textos
    lim = config.limites
    padroes_injecao = compilar_padroes(config.guardrails.padroes_injecao)
    maximo_perguntas = servicos.conversa.perguntas.maximo
    executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mensagem")
    travas: dict[str, threading.Lock] = {}
    travas_trava = threading.Lock()
    base_dia: dict[str, Any] = {"dia": None, "llm": 0, "avisado": False}
    base_trava = threading.Lock()
    dia_atual = dia or limitador.dia

    def trava_da(sessao: str) -> threading.Lock:
        with travas_trava:
            return travas.setdefault(sessao, threading.Lock())

    # -- erros ---------------------------------------------------------------

    @app.exception_handler(ErroApi)
    async def _erro_api(_: Request, e: ErroApi) -> JSONResponse:
        return JSONResponse({"erro": e.texto}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _erro_corpo(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"erro": textos.mensagem_vazia}, status_code=400)

    @app.middleware("http")
    async def _sem_cache(request: Request, call_next):
        resposta = await call_next(request)
        if request.url.path.startswith("/api/"):
            resposta.headers["Cache-Control"] = "no-store"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        return resposta

    # -- dependências ----------------------------------------------------------

    def limite_ip(request: Request) -> None:
        if not limitador.permitir_ip(_ip(request)):
            raise ErroApi(429, textos.limite_ip)

    def convite_atual(request: Request, _: None = Depends(limite_ip)) -> str:
        nome = convites.validar(request.cookies.get(COOKIE))
        if nome is None:
            raise ErroApi(401, textos.sem_convite)
        return nome

    def curador_atual(convite: str = Depends(convite_atual)) -> str:
        if curadoria is None or convites.papel_de(convite) != "curadoria":
            raise ErroApi(
                403, textos.sem_permissao
            )  # o servidor confere: esconder o botão não basta
        return convite

    def _sessao_do_convite(sessao: str, convite: str) -> estados.EstadoConversa:
        try:
            e = repositorio.carregar(sessao)
        except (FileNotFoundError, ValueError):
            raise ErroApi(404, textos.sessao_nao_encontrada) from None
        if e.convite != convite:  # sessão de outra pessoa: igual a não existir
            raise ErroApi(404, textos.sessao_nao_encontrada)
        return e

    def _visao(e: estados.EstadoConversa) -> dict[str, Any]:
        ids = [c.species_id for c in e.candidatas] if e.etapa == "confirmacao" else []
        return montar_visao(
            e,
            especies.fotos(servicos.db, ids) if ids else {},
            maximo_perguntas,
            lim.mensagens_por_sessao,
            servicos.vocabulario,
            servicos.regras_bico,
        )

    def _teto_diario() -> None:
        hoje = dia_atual()
        with base_trava:
            if base_dia["dia"] != hoje:
                base_dia.update(dia=hoje, llm=contador.llm, avisado=False)
            usadas = contador.llm - base_dia["llm"]
            if usadas >= lim.chamadas_llm_por_dia:
                raise ErroApi(429, textos.limite_diario)
            if usadas >= 0.8 * lim.chamadas_llm_por_dia and not base_dia["avisado"]:
                base_dia["avisado"] = True
                log.warning("chamadas ao LLM hoje: %d de %d", usadas, lim.chamadas_llm_por_dia)

    # -- páginas ---------------------------------------------------------------

    @app.get("/entrar")
    def entrar(request: Request, c: str = "", _: None = Depends(limite_ip)) -> Response:
        if convites.validar(c) is None:
            return FileResponse(WEB / "paginas" / "sem_convite.html", status_code=401)
        resposta = RedirectResponse("/", status_code=303)
        seguro = (
            request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        )
        resposta.set_cookie(
            COOKIE, c, httponly=True, samesite="lax", secure=seguro, max_age=30 * 24 * 3600
        )
        return resposta

    @app.get("/")
    def inicio(request: Request) -> Response:
        if convites.validar(request.cookies.get(COOKIE)) is None:
            return FileResponse(WEB / "paginas" / "sem_convite.html", status_code=401)
        return FileResponse(WEB / "static" / "index.html")

    @app.get("/privacidade")
    def privacidade() -> Response:
        return FileResponse(WEB / "paginas" / "privacidade.html")

    @app.get("/creditos")
    def creditos() -> Response:
        return FileResponse(WEB / "paginas" / "creditos.html")

    # -- API -------------------------------------------------------------------

    @app.get("/api/saude")
    def saude() -> dict[str, bool]:
        return {"ok": True, "sessoes": repositorio.ok()}

    @app.post("/api/sessoes", status_code=201)
    def abrir(convite: str = Depends(convite_atual)) -> dict[str, Any]:
        if not limitador.consumir_dia(f"sessoes:{convite}", lim.sessoes_por_convite_por_dia):
            raise ErroApi(429, textos.limite_sessoes_dia)
        e = estados.EstadoConversa(id=secrets.token_hex(12), convite=convite)
        repositorio.salvar(e)
        if uso_diario:
            uso_diario.registrar(sessoes=1)
        return _visao(e)

    @app.get("/api/sessoes/{sessao}")
    def ver(sessao: str, convite: str = Depends(convite_atual)) -> dict[str, Any]:
        return _visao(_sessao_do_convite(sessao, convite))

    @app.post("/api/sessoes/{sessao}/mensagens")
    def mensagem(
        sessao: str, corpo: CorpoMensagem, convite: str = Depends(convite_atual)
    ) -> dict[str, Any]:
        e = _sessao_do_convite(sessao, convite)
        try:
            texto = limpar_entrada(
                corpo.texto,
                lim.caracteres_por_mensagem,
                textos.mensagem_vazia,
                textos.mensagem_grande,
                None,  # a moderação vem depois dos limites, que são grátis
                textos.moderacao,
            )
            conferir_injecao(texto, padroes_injecao, textos.injecao)
        except EntradaRecusada as r:
            if r.motivo == "injecao":  # só o convite e o padrão: o texto pode ter dado pessoal
                log.warning("injeção barrada (convite %s, padrão %s)", convite, r.detalhe)
            raise ErroApi(400, r.texto) from None
        if sum(1 for m in e.mensagens if m.papel == "usuario") >= lim.mensagens_por_sessao:
            raise ErroApi(429, textos.limite_sessao)
        _teto_diario()
        if not limitador.consumir_dia(f"mensagens:{convite}", lim.mensagens_por_convite_por_dia):
            raise ErroApi(429, textos.limite_mensagens_dia)
        try:
            if moderador is not None and moderador.sinaliza(texto):
                raise ErroApi(400, textos.moderacao)
        except ModeracaoIndisponivel:  # falha fechada (P3d): sem checagem, a mensagem não passa
            raise ErroApi(503, textos.moderacao_indisponivel) from None
        return _processar(sessao, convite, texto)

    def _processar(sessao: str, convite: str, texto: str) -> dict[str, Any]:
        def rodar() -> dict[str, Any]:
            with trava_da(sessao):
                e = _sessao_do_convite(sessao, convite)  # relê: a trava garante a ordem
                antes = (contador.llm, contador.embeddings)
                orquestrador = Orquestrador(servicos, e)
                orquestrador.processar(texto)
                e = orquestrador.estado
                e.chamadas_llm += max(contador.llm - antes[0], 0)
                e.chamadas_embedding += max(contador.embeddings - antes[1], 0)
                repositorio.salvar(e)
                if uso_diario:
                    uso_diario.registrar(
                        mensagens=1,
                        chamadas_llm=max(contador.llm - antes[0], 0),
                        chamadas_embedding=max(contador.embeddings - antes[1], 0),
                    )
                return _visao(e)

        futuro = executor.submit(rodar)
        try:
            return futuro.result(timeout=config.tempo_limite_segundos)
        except TempoEsgotado:
            raise ErroApi(504, textos.demorou) from None
        except ErroApi:
            raise
        except Exception:
            log.exception("falha ao processar a mensagem da sessão %s", sessao)
            raise ErroApi(500, textos.erro) from None

    @app.post("/api/sessoes/{sessao}/voto")
    def votar(
        sessao: str, corpo: CorpoVoto, convite: str = Depends(convite_atual)
    ) -> dict[str, Any]:
        if corpo.voto not in ("ajudou", "nao_ajudou"):
            raise ErroApi(400, textos.erro)
        with trava_da(sessao):
            e = _sessao_do_convite(sessao, convite)
            if e.etapa != "fim":
                raise ErroApi(400, textos.erro)
            e.voto = corpo.voto  # type: ignore[assignment]
            repositorio.salvar(e)
            return _visao(e)

    catalogo_pronto: dict[str, Any] = {}
    catalogo_trava = threading.Lock()

    @app.get("/api/especies")
    def listar_especies(_: str = Depends(convite_atual)) -> list[dict[str, Any]]:
        """O catálogo do "Explorar aves": montado uma vez por processo (a base não muda no ar)."""
        with catalogo_trava:
            if "itens" not in catalogo_pronto:
                catalogo_pronto["itens"] = especies.catalogo(servicos.db, familias or {})
            return catalogo_pronto["itens"]

    @app.get("/api/curadoria/fila")
    def fila_de_revisao(_: str = Depends(curador_atual)) -> list[dict[str, Any]]:
        return fila.pendentes(servicos.db)

    @app.post("/api/curadoria/fila/{species_id}/concluir")
    def concluir_revisao(species_id: str, quem: str = Depends(curador_atual)) -> dict[str, Any]:
        try:
            return fila.concluir(servicos.db, species_id, quem)
        except ValueError as e:
            raise ErroApi(404, str(e)) from None

    @app.delete("/api/curadoria/fila/{species_id}/concluir")
    def reabrir_revisao(species_id: str, _: str = Depends(curador_atual)) -> dict[str, Any]:
        return fila.reabrir(servicos.db, species_id)

    @app.get("/api/curadoria/fila/{species_id}/texto")
    def texto_anexado(species_id: str, _: str = Depends(curador_atual)) -> dict[str, Any]:
        return fila.ler_texto(servicos.db, species_id)

    @app.put("/api/curadoria/fila/{species_id}/texto")
    def anexar_texto(
        species_id: str, corpo: CorpoTexto, quem: str = Depends(curador_atual)
    ) -> dict[str, Any]:
        try:
            return fila.gravar_texto(servicos.db, species_id, corpo.texto, quem)
        except ValueError as e:
            raise ErroApi(400, str(e)) from None

    @app.get("/api/especies/{species_id}")
    def especie(species_id: str, _: str = Depends(convite_atual)) -> dict[str, Any]:
        ficha = especies.ficha_publica(servicos.db, species_id, config.exibir_textos_gerados)
        if ficha is None:
            raise ErroApi(404, textos.especie_nao_encontrada)
        return ficha

    @app.get("/api/eu")
    def quem_sou(convite: str = Depends(convite_atual)) -> dict[str, str]:
        return {"papel": convites.papel_de(convite)}

    @app.get("/api/especies/{species_id}/edicao")
    def campos_da_ficha(species_id: str, _: str = Depends(curador_atual)) -> dict[str, Any]:
        try:
            return curadoria.campos(species_id)  # type: ignore[union-attr]
        except ErroCuradoria as e:
            raise ErroApi(404, str(e)) from None

    @app.put("/api/especies/{species_id}/campos/{caminho}")
    def corrigir_campo(
        species_id: str, caminho: str, corpo: CorpoCorrecao, quem: str = Depends(curador_atual)
    ) -> dict[str, Any]:
        with trava_da(f"especie:{species_id}"):
            try:
                resultado = curadoria.definir(  # type: ignore[union-attr]
                    species_id, caminho, corpo.valor, quem, corpo.motivo
                )
                catalogo_pronto.clear()  # a foto principal pode ter mudado
                return resultado
            except ErroCuradoria as e:
                raise ErroApi(400, str(e)) from None
            except Exception:
                log.exception("falha ao corrigir %s de %s", caminho, species_id)
                raise ErroApi(500, textos.correcao_falhou) from None

    @app.delete("/api/especies/{species_id}/campos/{caminho}")
    def reverter_campo(
        species_id: str, caminho: str, quem: str = Depends(curador_atual)
    ) -> dict[str, Any]:
        with trava_da(f"especie:{species_id}"):
            try:
                resultado = curadoria.reverter(species_id, caminho, quem)  # type: ignore[union-attr]
                catalogo_pronto.clear()
                return resultado
            except ErroCuradoria as e:
                raise ErroApi(400, str(e)) from None
            except Exception:
                log.exception("falha ao reverter %s de %s", caminho, species_id)
                raise ErroApi(500, textos.correcao_falhou) from None

    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
    return app
