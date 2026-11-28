"""Relatório do beta (AS6): resumo sem texto livre, pseudônimo, arquivo e critério do marco 2."""

import csv
import io
import json
import stat
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from avesrag_assistant.api.convites import Convites
from avesrag_assistant.avaliacao import beta
from avesrag_assistant.cli import app
from avesrag_assistant.conversa import estado as arquivos
from avesrag_assistant.conversa.estado import CandidataApresentada, EstadoConversa, Mensagem

runner = CliRunner()

TEXTO_LIVRE = "ave pequena, vi no sítio do seu Zé na Rua das Flores, meu telefone é 34 99999-0000"


def _cand(sid, bateram=(), conflitaram=()):
    return CandidataApresentada(
        species_id=sid,
        nome=sid,
        nome_cientifico=sid,
        nota_final=1.0,
        bateram=list(bateram),
        conflitaram=list(conflitaram),
    )


def _estado(sessao="s1", convite="ana", confirmada="sp_b", voto="ajudou", **extra):
    base = dict(
        id=sessao,
        convite=convite,
        etapa="fim",
        motivo_fim="confirmada" if confirmada else "sem_confirmacao",
        confirmada=confirmada,
        voto=voto,
        descricoes=[TEXTO_LIVRE],
        local="Rua das Flores, Uberlândia",
        atributos={"cores": ["amarelo"]},
        candidatas=[_cand("sp_a"), _cand("sp_b", ["cores"], ["tamanho"]), _cand("sp_c")],
        buscas=[["sp_c", "sp_a", "sp_b"], ["sp_b", "sp_a"]],
        perguntas_feitas=["cor_barriga", "bico"],
        mensagens=[
            Mensagem(papel="usuario", texto=TEXTO_LIVRE),
            Mensagem(papel="assistente", texto="Qual a cor da barriga?"),
            Mensagem(papel="usuario", texto="amarela"),
        ],
        chamadas_llm=5,
        chamadas_embedding=2,
        criada_em=datetime(2026, 12, 1, 15, 30, tzinfo=UTC),
    )
    base.update(extra)
    return EstadoConversa(**base)


# -- resumo ----------------------------------------------------------------------


def test_resumo_traz_os_numeros_e_a_confirmada():
    r = beta.resumir(_estado())
    assert r.sessao == "s1" and r.convite == "ana" and r.criada_em == "2026-12-01"
    assert r.confirmada == "sp_b" and r.voto == "ajudou" and r.etapa == "fim"
    assert (r.mensagens, r.perguntas, r.apresentacoes) == (2, 2, 1)
    assert r.posicao_primeira == 3 and r.posicao_ultima == 1
    assert r.bateram == ["cores"] and r.conflitaram == ["tamanho"]
    assert r.atributos == {"cores": ["amarelo"]}
    assert (r.chamadas_llm, r.chamadas_embedding) == (5, 2)


def test_resumo_nao_leva_nenhum_texto_livre():
    r = beta.resumir(_estado())
    serializado = r.model_dump_json()
    for proibido in ("sítio", "Zé", "Rua das Flores", "99999", "amarela", "Qual a cor"):
        assert proibido not in serializado


def test_resumo_de_conversa_abandonada_e_sem_confirmacao():
    e = _estado(confirmada=None, voto=None, etapa="coleta", candidatas=[], buscas=[])
    r = beta.resumir(e)
    assert r.confirmada is None and r.apresentacoes == 0
    assert r.posicao_primeira is None and r.bateram == []


def test_apresentacoes_contam_as_rodadas_recusadas():
    e = _estado(recusadas=["sp_x", "sp_y", "sp_z"], rodada_confirmacao=1)
    r = beta.resumir(e)
    assert r.apresentacoes == 2
    assert r.apresentadas == ["sp_x", "sp_y", "sp_z", "sp_a", "sp_b", "sp_c"]


# -- pseudônimo ------------------------------------------------------------------


def test_sal_e_criado_uma_vez_e_so_para_o_dono(tmp_path):
    sal = beta.carregar_sal(tmp_path / "beta")
    assert len(sal) == 32 and beta.carregar_sal(tmp_path / "beta") == sal
    modo = stat.S_IMODE((tmp_path / "beta" / beta.ARQUIVO_SAL).stat().st_mode)
    assert modo == 0o600


def test_pseudonimo_e_estavel_e_depende_do_sal():
    assert beta.pseudonimo("ana", "sal1") == beta.pseudonimo("ana", "sal1")
    assert beta.pseudonimo("ana", "sal1") != beta.pseudonimo("ana", "sal2")
    assert beta.pseudonimo("ana", "sal1") != beta.pseudonimo("bia", "sal1")
    assert beta.pseudonimo("ana", "sal1").startswith("c") and len(beta.pseudonimo("ana", "x")) == 9


def test_anonimizar_tira_o_nome_do_convite():
    r = beta.anonimizar(beta.resumir(_estado(convite="Maria Souza")), "sal")
    assert "Maria" not in r.model_dump_json() and r.convite == beta.pseudonimo("Maria Souza", "sal")


# -- arquivo ---------------------------------------------------------------------


def test_arquivo_guarda_e_le_de_volta(tmp_path):
    resumos = {r.sessao: r for r in (beta.resumir(_estado("s1")), beta.resumir(_estado("s2")))}
    caminho = beta.gravar_arquivo(tmp_path / "conversas.jsonl", resumos)
    assert beta.ler_arquivo(caminho) == resumos
    assert beta.ler_arquivo(tmp_path / "nao_existe.jsonl") == {}


def test_mesclar_mantem_o_que_expirou_e_atualiza_o_que_mudou():
    antigo = beta.resumir(_estado("s1", voto=None))
    expirada = beta.resumir(_estado("s0"))
    atualizado = beta.resumir(_estado("s1", voto="nao_ajudou"))
    saida = beta.mesclar({"s0": expirada, "s1": antigo}, [atualizado])
    assert set(saida) == {"s0", "s1"}
    assert saida["s1"].voto == "nao_ajudou" and saida["s0"] is expirada


def test_tsv_sem_texto_livre(tmp_path):
    r = beta.anonimizar(beta.resumir(_estado()), "sal")
    caminho = beta.gravar_tsv(tmp_path / "c.tsv", {r.sessao: r})
    conteudo = caminho.read_text(encoding="utf-8")
    linhas_tsv = list(csv.reader(io.StringIO(conteudo), delimiter="\t"))
    assert linhas_tsv[0] == beta.COLUNAS_TSV and len(linhas_tsv) == 2
    linha = dict(zip(beta.COLUNAS_TSV, linhas_tsv[1], strict=True))
    assert linha["confirmada"] == "sp_b" and linha["convite"] == r.convite
    assert "ana" not in conteudo and "99999" not in conteudo
    assert json.loads(linha["atributos"]) == {"cores": ["amarelo"]}


# -- relatório -------------------------------------------------------------------


def test_intervalo_de_wilson():
    baixo, alto = beta.intervalo_wilson(4, 18)
    assert round(baixo, 2) == 0.09 and round(alto, 2) == 0.45  # o intervalo das 18 sessões
    assert beta.intervalo_wilson(0, 0) == (0.0, 1.0)
    assert beta.intervalo_wilson(10, 10)[1] == 1.0


def _resumos(n_confirmadas, pessoas, ajudou=0, nao=0):
    saida = []
    for i in range(n_confirmadas):
        voto = "ajudou" if i < ajudou else ("nao_ajudou" if i < ajudou + nao else None)
        saida.append(
            beta.resumir(_estado(f"s{i}", convite=f"p{i % pessoas}", voto=voto, confirmada="sp_b"))
        )
    return saida


def test_relatorio_vazio():
    assert "nenhuma conversa" in beta.linhas([])[0]


def test_relatorio_conta_o_que_falta_para_o_criterio():
    texto = "\n".join(beta.linhas(_resumos(12, pessoas=3, ajudou=8, nao=2)))
    assert "conversas com confirmação 12/30, pessoas 3/5" in texto
    assert "ainda falta: 18 conversa(s) com confirmação e 2 pessoa(s) a mais" in texto
    assert "8/10 (80%" in texto  # "ajudou" entre as que votaram
    assert "concluídas sem voto: 2" in texto
    assert "satisfação, não acerto" in texto


def test_relatorio_criterio_atingido():
    texto = "\n".join(beta.linhas(_resumos(30, pessoas=6, ajudou=20, nao=5)))
    assert "30/30, pessoas 6/5" in texto and "atingido" in texto and "ainda falta" not in texto


def test_relatorio_separa_concluida_sem_confirmar_e_abandonada():
    resumos = [
        beta.resumir(_estado("s1")),
        beta.resumir(_estado("s2", confirmada=None, voto="nao_ajudou")),
        beta.resumir(_estado("s3", confirmada=None, voto=None, etapa="coleta")),
    ]
    texto = "\n".join(beta.linhas(resumos))
    assert "concluídas: 2; em andamento ou abandonadas: 1" in texto
    assert "com espécie confirmada: 1" in texto and "sem confirmar" in texto


def test_conversa_encerrada_por_insistir_fora_do_tema_fica_fora_das_taxas():
    resumos = [
        beta.resumir(_estado("s1")),
        beta.resumir(_estado("s2", confirmada=None, voto=None, motivo_fim="fora_de_escopo")),
        beta.resumir(_estado("s3", confirmada=None, voto=None, etapa="coleta")),
    ]
    texto = "\n".join(beta.linhas(resumos))
    assert "3 conversa(s)" in texto  # a conversa conta no total...
    assert "concluídas: 1; em andamento ou abandonadas: 1" in texto  # ...mas não nas taxas
    assert 'concluídas sem confirmar ("nenhuma delas" ou sem mais rodadas): 0' in texto
    assert "encerradas por mensagens seguidas fora do tema (fora das taxas): 1" in texto


def test_relatorio_mostra_o_nome_do_convite_so_na_tela():
    r = beta.anonimizar(beta.resumir(_estado(convite="ana")), "sal")
    pseudonimo = beta.pseudonimo("ana", "sal")
    assert "ana" in "\n".join(beta.linhas([r], {pseudonimo: "ana"}))
    assert pseudonimo in "\n".join(beta.linhas([r]))  # sem a tradução, fica o pseudônimo


def test_posicao_na_1a_busca_conta_o_top_3():
    fora = _estado("s2", buscas=[["a", "b", "c", "d", "sp_b"]])  # 5º
    texto = "\n".join(beta.linhas([beta.resumir(_estado("s1")), beta.resumir(fora)]))
    assert "1ª busca (antes das perguntas): 1/2" in texto  # só a 1ª está no top 3
    assert "entre as 10: 2/2" in texto


# -- comando ---------------------------------------------------------------------


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("CONVITES_PATH", str(tmp_path / "convites.json"))
    monkeypatch.delenv("CONVITES_JSON", raising=False)
    Convites(tmp_path / "convites.json").criar("ana")
    Convites(tmp_path / "convites.json").criar("teste-interno")
    return tmp_path


def _gravar(ambiente, *estados):
    for e in estados:
        arquivos.salvar(e, ambiente / "reports" / "sessoes")


def test_comando_relata_e_arquiva_sem_texto_livre(ambiente):
    _gravar(
        ambiente,
        _estado("a1", convite="ana"),
        _estado("a2", convite="teste-interno"),
        _estado("m1", convite="ana", ave="S1", esperada="sp_b"),  # moderada: fica de fora
    )
    r = runner.invoke(app, ["beta", "--origem", "arquivo", "--excluir", "teste-interno"])
    assert r.exit_code == 0, r.output
    assert "1 da moderação ignorada(s)" in r.output and "1 excluída(s) por convite" in r.output
    assert "ana" in r.output and "teste-interno" not in r.output.split("por convite")[1]
    assert "conversas com confirmação 1/30" in r.output
    jsonl = (ambiente / "reports" / "beta" / "conversas.jsonl").read_text(encoding="utf-8")
    assert jsonl.count("\n") == 2  # a1 e a2: a moderada não entra no arquivo
    assert "ana" not in jsonl and "99999" not in jsonl
    assert (ambiente / "reports" / "beta" / "conversas.tsv").exists()


def test_comando_guarda_o_que_sumiu_do_banco(ambiente):
    _gravar(ambiente, _estado("a1"), _estado("a2", voto="nao_ajudou"))
    assert runner.invoke(app, ["beta", "--origem", "arquivo"]).exit_code == 0
    (ambiente / "reports" / "sessoes" / "a1.json").unlink()  # o TTL apagou a conversa
    r = runner.invoke(app, ["beta", "--origem", "arquivo"])
    assert "conversas com confirmação 2/30" in r.output  # a1 continua no arquivo
    assert "1 só no arquivo" in r.output


def test_comando_sem_arquivar_nao_grava(ambiente):
    _gravar(ambiente, _estado("a1"))
    r = runner.invoke(app, ["beta", "--origem", "arquivo", "--sem-arquivar"])
    assert r.exit_code == 0 and "conversas com confirmação 1/30" in r.output
    assert not (ambiente / "reports" / "beta" / "conversas.jsonl").exists()


def test_comando_origem_invalida(ambiente):
    r = runner.invoke(app, ["beta", "--origem", "nuvem"])
    assert r.exit_code == 2


def test_aviso_de_expiracao_do_banco(monkeypatch, ambiente):
    from avesrag_assistant import cli

    velha = _estado("a1", atualizada_em=datetime.now(UTC) - timedelta(days=13, hours=12))

    class BancoComConversas:
        def __getitem__(self, _):
            return self

        def find(self, _):
            doc = velha.model_dump()
            doc["_id"] = velha.id
            doc["expira_em"] = velha.atualizada_em + timedelta(days=14)
            for campo in ("criada_em", "atualizada_em", "expira_em"):
                doc[campo] = doc[campo].replace(tzinfo=None)  # o pymongo devolve datas sem fuso
            return [doc]

    monkeypatch.setattr(cli, "get_db", lambda: BancoComConversas())
    r = runner.invoke(app, ["beta", "--sem-arquivar"])
    assert r.exit_code == 0, r.output
    assert "expira em 0 dia(s) e NÃO foram arquivadas" in r.output
