"""Servidor local das sessões moderadas (plano_assistant2.md §14).

Quem modera abre a página no próprio computador, escolhe uma das aves do
formulário (`avaliacao/especies_formulario.csv`) e passa o teclado para a
pessoa: ela vê a foto, sem o nome, e conversa com o assistente. Quando as 3
candidatas aparecem, cada uma vem com a foto, para a pessoa comparar; no fim, a
página revela a espécie. Também dá para conversar sem foto (modo livre), sobre
uma ave que a pessoa viu.

Só a biblioteca padrão (`http.server`): a API de verdade, com FastAPI, é do
Assistant 4. O servidor escuta só em 127.0.0.1 por padrão, atende uma mensagem
por vez (as chamadas ao LLM levam alguns segundos e há uma pessoa só) e grava o
estado de cada sessão em `reports/sessoes/<id>.json`, como o `conversar`, e
regrava o resumo de todas em `reports/sessoes_moderadas.tsv` (uma linha por
sessão, com a conversa) depois de cada mensagem.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from avesrag_builder.db.collections import SPECIES

from avesrag_assistant.avaliacao.sessoes import ARQUIVO_TSV, carregar_moderadas, gravar_tsv
from avesrag_assistant.conversa import estado as sessoes
from avesrag_assistant.conversa.orquestrador import Orquestrador, Servicos

PAGINA = Path(__file__).with_name("pagina.html")
LIMITE_CORPO = 10_000  # bytes de uma requisição
LIMITE_MENSAGEM = 1_000  # caracteres de uma mensagem
SAUDACAO = "Olá! Me conte como era a ave, do jeito que você contaria para um amigo."
CODIGO_PESSOA = re.compile(r"^[A-Za-z0-9_-]{1,12}$")
ROTA_MENSAGEM = re.compile(r"^/api/sessoes/([A-Za-z0-9]+)/mensagens$")
ROTA_SESSAO = re.compile(r"^/api/sessoes/([A-Za-z0-9]+)$")


@dataclass(frozen=True)
class Ave:
    """Uma ave do formulário: o código mostrado (A1…C5) e a espécie, que a página não mostra."""

    codigo: str
    species_id: str
    nome: str
    nome_cientifico: str
    foto_url: str
    foto_link: str
    foto_credito: str


def carregar_aves(path: Path) -> dict[str, Ave]:
    aves = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for linha in csv.DictReader(fh):
            codigo = f"{linha['formulario']}{linha['ordem']}"
            aves[codigo] = Ave(
                codigo=codigo,
                species_id=linha["species_id"],
                nome=linha["nome"],
                nome_cientifico=linha["nome_cientifico"],
                foto_url=linha["foto_url"],
                foto_link=linha["foto_link"],
                foto_credito=linha["foto_credito"],
            )
    return aves


class Moderacao:
    """As sessões abertas e o que a página precisa saber de cada uma."""

    def __init__(self, servicos: Servicos, aves: Mapping[str, Ave], diretorio: Path):
        self.servicos = servicos
        self.aves = aves
        self.diretorio = diretorio
        self.tsv = diretorio.parent / ARQUIVO_TSV
        self.trava = threading.Lock()

    def _gravar_tsv(self) -> None:
        gravar_tsv(carregar_moderadas(self.diretorio), self.tsv)

    # -- leitura -------------------------------------------------------------

    def lista_de_aves(self) -> list[dict[str, str]]:
        """Só o código e a foto: o nome não vai para a página."""
        return [
            {"codigo": a.codigo, "foto_url": a.foto_url, "foto_credito": a.foto_credito}
            for a in self.aves.values()
        ]

    def _fotos(self, species_ids: list[str]) -> dict[str, dict[str, str]]:
        cursor = self.servicos.db[SPECIES].find(
            {"species_id": {"$in": species_ids}}, {"_id": 0, "species_id": 1, "midia.foto": 1}
        )
        return {d["species_id"]: ((d.get("midia") or {}).get("foto") or {}) for d in cursor}

    def visao(self, e: sessoes.EstadoConversa) -> dict[str, Any]:
        """O que a página mostra da sessão: as mensagens, as candidatas e, no fim, a resposta."""
        ave = self.aves.get(e.ave or "")
        dados: dict[str, Any] = {
            "id": e.id,
            "etapa": e.etapa,
            "ave": e.ave,
            "foto_url": ave.foto_url if ave else None,
            "foto_credito": ave.foto_credito if ave else None,
            "mensagens": [{"papel": "assistente", "texto": SAUDACAO}]
            + [{"papel": m.papel, "texto": m.texto} for m in e.mensagens],
            "candidatas": [],
            "revelada": None,
        }
        if e.etapa == "confirmacao":
            fotos = self._fotos([c.species_id for c in e.candidatas])
            dados["candidatas"] = [
                {
                    "numero": i,
                    "nome": c.nome,
                    "nome_cientifico": c.nome_cientifico,
                    "foto_url": fotos.get(c.species_id, {}).get("url"),
                    "foto_credito": fotos.get(c.species_id, {}).get("credito"),
                }
                for i, c in enumerate(e.candidatas, start=1)
            ]
        if e.etapa == "fim" and ave:
            dados["revelada"] = {
                "nome": ave.nome,
                "nome_cientifico": ave.nome_cientifico,
                "acertou": e.confirmada == ave.species_id,
            }
        return dados

    # -- escrita -------------------------------------------------------------

    def nova(self, codigo: str | None, pessoa: str | None = None) -> dict[str, Any]:
        if codigo and codigo not in self.aves:
            raise ValueError(f"ave desconhecida: {codigo!r}")
        pessoa = (pessoa or "").strip() or None
        if pessoa and not CODIGO_PESSOA.match(pessoa):
            raise ValueError("código da pessoa: use até 12 letras, números, _ ou - (ex.: P03)")
        if codigo and not pessoa:
            raise ValueError("informe o código da pessoa (ex.: P03), sem nome nem e-mail")
        ave = self.aves.get(codigo or "")
        e = sessoes.EstadoConversa(
            id=sessoes.novo_id(),
            ave=ave.codigo if ave else None,
            esperada=ave.species_id if ave else None,
            pessoa=pessoa,
        )
        with self.trava:
            sessoes.salvar(e, self.diretorio)
            self._gravar_tsv()
        return self.visao(e)

    def abrir(self, sessao: str) -> dict[str, Any]:
        return self.visao(sessoes.carregar(self.diretorio, sessao))

    def mensagem(self, sessao: str, texto: str) -> dict[str, Any]:
        texto = texto.strip()
        if not texto:
            raise ValueError("mensagem vazia")
        if len(texto) > LIMITE_MENSAGEM:
            raise ValueError(f"mensagem com mais de {LIMITE_MENSAGEM} caracteres")
        with self.trava:
            e = sessoes.carregar(self.diretorio, sessao)
            orquestrador = Orquestrador(self.servicos, e)
            orquestrador.processar(texto)
            sessoes.salvar(orquestrador.estado, self.diretorio)
            self._gravar_tsv()
            return self.visao(orquestrador.estado)


def criar_handler(moderacao: Moderacao) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "avesrag-moderar"

        def log_message(self, formato: str, *args: Any) -> None:  # sem log por requisição
            pass

        def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, status: int, dados: Any) -> None:
            corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
            self._responder(status, corpo, "application/json; charset=utf-8")

        def _erro(self, status: int, mensagem: str) -> None:
            self._json(status, {"erro": mensagem})

        def _corpo(self) -> dict[str, Any]:
            tamanho = int(self.headers.get("Content-Length") or 0)
            if tamanho > LIMITE_CORPO:
                raise ValueError("requisição grande demais")
            dados = json.loads(self.rfile.read(tamanho) or b"{}")
            if not isinstance(dados, dict):
                raise ValueError("o corpo deve ser um objeto JSON")
            return dados

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                pagina = PAGINA.read_bytes()
                self._responder(HTTPStatus.OK, pagina, "text/html; charset=utf-8")
            elif self.path == "/api/aves":
                self._json(HTTPStatus.OK, moderacao.lista_de_aves())
            elif m := ROTA_SESSAO.match(self.path):
                try:
                    self._json(HTTPStatus.OK, moderacao.abrir(m.group(1)))
                except FileNotFoundError as e:
                    self._erro(HTTPStatus.NOT_FOUND, str(e))
            else:
                self._erro(HTTPStatus.NOT_FOUND, "não encontrado")

        def do_POST(self) -> None:
            try:
                corpo = self._corpo()
                if self.path == "/api/sessoes":
                    self._json(
                        HTTPStatus.CREATED, moderacao.nova(corpo.get("ave"), corpo.get("pessoa"))
                    )
                elif m := ROTA_MENSAGEM.match(self.path):
                    texto = corpo.get("texto")
                    if not isinstance(texto, str):
                        raise ValueError("falta o texto da mensagem")
                    self._json(HTTPStatus.OK, moderacao.mensagem(m.group(1), texto))
                else:
                    self._erro(HTTPStatus.NOT_FOUND, "não encontrado")
            except FileNotFoundError as e:
                self._erro(HTTPStatus.NOT_FOUND, str(e))
            except (ValueError, json.JSONDecodeError) as e:
                self._erro(HTTPStatus.BAD_REQUEST, str(e))
            except Exception as e:  # falha do LLM, do embedding ou do Mongo: a página mostra
                self._erro(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    return Handler


def criar_servidor(moderacao: Moderacao, host: str, porta: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, porta), criar_handler(moderacao))
