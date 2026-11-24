"""Página das sessões moderadas e o relatório, com LLM e banco falsos (servidor numa porta livre)."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from avesrag_assistant.avaliacao.sessoes import carregar_moderadas, linhas, resumir
from avesrag_assistant.busca.pontuacao import carregar_ranking
from avesrag_assistant.conversa import estado as sessoes
from avesrag_assistant.conversa.config import carregar_conversa
from avesrag_assistant.conversa.orquestrador import Servicos
from avesrag_assistant.settings import ROOT_DIR
from avesrag_assistant.web.servidor import (
    LIMITE_MENSAGEM,
    SAUDACAO,
    Moderacao,
    carregar_aves,
    criar_servidor,
)
from test_orquestrador import ExtratorFalso, RedatorFalso, SugestorFalso, _banco

CSV = (
    "formulario,ordem,species_id,nome,nome_cientifico,foto_url,foto_link,foto_credito\n"
    "A,1,sp_a,ave-a,Ave a,https://exemplo/a.jpg,https://exemplo/a,Foto: Fulano · CC BY 4.0\n"
)


@pytest.fixture
def servidor(tmp_path, vocab_regras):
    (tmp_path / "aves.csv").write_text(CSV, encoding="utf-8")
    aves = carregar_aves(tmp_path / "aves.csv")
    servicos = Servicos(
        db=_banco(),
        ranking=carregar_ranking(ROOT_DIR / "config" / "ranking.yaml"),
        conversa=carregar_conversa(ROOT_DIR / "config" / "conversa.yaml", vocab_regras[0]),
        vocabulario=vocab_regras[0],
        regras_bico=vocab_regras[1],
        extrator=ExtratorFalso(vocab_regras),
        redator=RedatorFalso(),
        vetor=lambda texto: [0.1],
        sugestor=SugestorFalso(),
        indice={},
    )
    moderacao = Moderacao(servicos, aves, tmp_path / "sessoes")
    srv = criar_servidor(moderacao, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / "sessoes"
    srv.shutdown()
    srv.server_close()


def _chamar(url, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    pedido = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"} if dados else {}
    )
    try:
        with urllib.request.urlopen(pedido, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_pagina_e_lista_de_aves_sem_nome(servidor):
    base, _ = servidor
    status, html = _chamar(base + "/")
    assert status == 200 and "sessão moderada" in html
    status, corpo = _chamar(base + "/api/aves")
    aves = json.loads(corpo)
    assert aves == [
        {
            "codigo": "A1",
            "foto_url": "https://exemplo/a.jpg",
            "foto_credito": "Foto: Fulano · CC BY 4.0",
        }
    ]
    assert "ave-a" not in corpo and "sp_a" not in corpo  # o nome não vai para a página


def test_sessao_moderada_ate_revelar(servidor):
    base, diretorio = servidor
    status, corpo = _chamar(base + "/api/sessoes", {"ave": "A1"})
    assert status == 201
    v = json.loads(corpo)
    assert (v["ave"], v["foto_url"], v["etapa"]) == ("A1", "https://exemplo/a.jpg", "coleta")
    assert v["mensagens"] == [{"papel": "assistente", "texto": SAUDACAO}]
    assert "sp_a" not in corpo

    url = f"{base}/api/sessoes/{v['id']}/mensagens"
    v = json.loads(_chamar(url, {"texto": "uma ave pequena"})[1])
    assert v["mensagens"][-1]["texto"] == "Pergunta sobre a cor da barriga?"
    v = json.loads(_chamar(url, {"texto": "amarela"})[1])
    assert v["etapa"] == "confirmacao"
    assert [c["numero"] for c in v["candidatas"]] == [1, 2, 3]
    assert v["candidatas"][0]["nome"] == "ave-sp_a"
    v = json.loads(_chamar(url, {"texto": "1"})[1])
    assert v["etapa"] == "fim"
    assert v["revelada"] == {"nome": "ave-a", "nome_cientifico": "Ave a", "acertou": True}

    # Retomar pela página e o relatório.
    assert json.loads(_chamar(f"{base}/api/sessoes/{v['id']}")[1])["etapa"] == "fim"
    estados = carregar_moderadas(diretorio)
    r = resumir(estados[0])
    assert (r.ave, r.esperada, r.acertou, r.apresentada, r.rodadas, r.perguntas) == (
        "A1",
        "sp_a",
        True,
        True,
        1,
        1,
    )
    assert (r.primeira, r.ultima) == (1, 1)
    saida = linhas([r])
    assert "acertou" in saida[0]
    assert saida[-1].startswith("1 sessão(ões) concluída(s): 1 acerto(s) (100%)")


def test_sessao_livre_sem_foto(servidor):
    base, diretorio = servidor
    v = json.loads(_chamar(base + "/api/sessoes", {"ave": None})[1])
    assert v["ave"] is None and v["foto_url"] is None
    assert carregar_moderadas(diretorio) == []  # sem ave, fora do relatório


@pytest.mark.parametrize(
    ("caminho", "corpo", "status"),
    [
        ("/api/sessoes", {"ave": "Z9"}, 400),
        ("/api/sessoes/naoexiste/mensagens", {"texto": "oi"}, 404),
        ("/api/sessoes/naoexiste", None, 404),
        ("/api/outra", {}, 404),
    ],
)
def test_erros(servidor, caminho, corpo, status):
    base, _ = servidor
    assert _chamar(base + caminho, corpo)[0] == status


def test_mensagem_vazia_ou_longa(servidor):
    base, _ = servidor
    v = json.loads(_chamar(base + "/api/sessoes", {"ave": "A1"})[1])
    url = f"{base}/api/sessoes/{v['id']}/mensagens"
    assert _chamar(url, {"texto": "  "})[0] == 400
    assert _chamar(url, {"texto": "a" * (LIMITE_MENSAGEM + 1)})[0] == 400
    assert _chamar(url, {"outro": 1})[0] == 400


def test_relatorio_sem_sessoes(tmp_path):
    assert linhas([]) == ["nenhuma sessão moderada em reports/sessoes/"]
    e = sessoes.EstadoConversa(id="x1", ave="A1", esperada="sp_a")
    r = resumir(e)
    assert (r.acertou, r.apresentada, r.rodadas, r.primeira) == (False, False, 0, None)
    assert "em andamento" in linhas([r])[0]
