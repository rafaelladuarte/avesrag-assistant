"""LLM do chat com um fornecedor falso: contratos, prompts, cache, extrator e sugestor."""

import json

import pytest
import yaml
from avesrag_builder.llm.base import Pedido, Resposta
from pydantic import ValidationError

from avesrag_assistant.llm.cache import CacheLLM, ErroLLM
from avesrag_assistant.llm.contratos import contrato, schema_do_contrato
from avesrag_assistant.llm.extrator import Extrator, estado_para_prompt
from avesrag_assistant.llm.prompts import PromptInvalido, carregar_prompt
from avesrag_assistant.llm.sugestor import (
    Sugestor,
    casar,
    indice_da_base,
    indice_de_nomes,
    normalizar_nome,
)
from avesrag_assistant.settings import ROOT_DIR

PROMPT_REAL = ROOT_DIR / "config" / "prompts" / "c01_extrator.yaml"


def _saida(**atributos):
    """Uma saída válida do c01, com os atributos dados e o resto null."""
    base = {
        nome: None
        for nome in (
            "tamanho",
            "cores",
            "cores_por_parte",
            "marcas",
            "bico",
            "cauda",
            "ambiente",
            "onde_estava",
            "o_que_fazia",
            "grupo",
            "atividade",
        )
    }
    return {
        "fora_de_escopo": False,
        "local": None,
        "data_observacao": None,
        "atributos": {**base, **atributos},
        "descricao_para_busca": "Ave pequena e amarela.",
    }


class Falso:
    """Fornecedor que devolve as respostas da fila, em ordem."""

    nome = "falso"

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.pedidos: list[Pedido] = []

    def gerar(self, pedido):
        self.pedidos.append(pedido)
        conteudo = self.respostas.pop(0)
        if isinstance(conteudo, dict):
            conteudo = json.dumps(conteudo)
        return Resposta(id=pedido.id, conteudo=conteudo, tokens_entrada=10, tokens_saida=5)


@pytest.fixture
def prompt(vocab_regras):
    return carregar_prompt(PROMPT_REAL, vocab_regras[0])


def _extrator(tmp_path, prompt, vocab_regras, *respostas):
    falso = Falso(*respostas)
    cache = CacheLLM(tmp_path / "llm.json", falso)
    return Extrator(prompt, cache, *vocab_regras, "modelo-padrao"), falso


# ---------------------------------------------------------------------------
# Contratos
# ---------------------------------------------------------------------------


def test_schema_do_c01_traz_o_vocabulario_como_enum(vocab_regras):
    schema = json.dumps(schema_do_contrato("C01Extrator", *vocab_regras))
    assert '"amarelo"' in schema and '"sobrancelha"' in schema and '"levemente_curvo"' in schema
    assert '"strict": true' in schema


@pytest.mark.parametrize(
    "atributos",
    [{"cores": ["amarela"]}, {"marcas": ["faixa"]}, {"bico": {"comprimento": "enorme"}}],
)
def test_c01_recusa_valor_fora_do_vocabulario(vocab_regras, atributos):
    modelo = contrato("C01Extrator", *vocab_regras)
    with pytest.raises(ValidationError):
        modelo.model_validate(_saida(**atributos))


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_prompt_real_carrega_e_injeta_vocabulario_e_bico(prompt, vocab_regras):
    system = prompt.render_system(*vocab_regras)
    assert "{vocabulario}" not in system and "{bico}" not in system
    assert "sobrancelha" in system and "levemente_curvo" in system
    entrada = prompt.render_entrada(estado="{}", mensagem="ave amarela")
    assert "<mensagem_usuario>\nave amarela\n</mensagem_usuario>" in entrada


def _gravar(tmp_path, nome, **mudancas):
    dados = yaml.safe_load(PROMPT_REAL.read_text(encoding="utf-8"))
    dados.update(mudancas)
    path = tmp_path / f"{nome}.yaml"
    path.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mudancas", "erro"),
    [
        ({"schema": "Outro"}, "schema"),
        ({"vocabulario": ["cores", "inexistente"]}, "inexistente"),
        ({"entrada": "<mensagem_usuario>{mensagem}</mensagem_usuario>"}, "entrada"),
        ({"system": "sem placeholders"}, "andam juntos"),
    ],
)
def test_prompt_invalido_e_recusado(tmp_path, vocab_regras, mudancas, erro):
    with pytest.raises(PromptInvalido, match=erro):
        carregar_prompt(_gravar(tmp_path, "c01_extrator", **mudancas), vocab_regras[0])


def test_prompt_com_id_diferente_do_arquivo(tmp_path, vocab_regras):
    with pytest.raises(PromptInvalido, match="nome do arquivo"):
        carregar_prompt(_gravar(tmp_path, "outro_nome"), vocab_regras[0])


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _pedido(entrada="x"):
    return Pedido(id="t", modelo="m", system="s", entrada=entrada, schema={})


def test_cache_nao_chama_de_novo_e_muda_com_a_versao(tmp_path):
    falso = Falso("a", "b")
    cache = CacheLLM(tmp_path / "llm.json", falso)
    assert cache.gerar("p", "1", _pedido(), str.upper) == "A"
    assert cache.gerar("p", "1", _pedido(), str.upper) == "A"
    assert len(falso.pedidos) == 1
    assert cache.gerar("p", "2", _pedido(), str.upper) == "B"  # versão nova
    assert (cache.chamadas, cache.tokens_entrada, cache.tokens_saida) == (2, 20, 10)
    # Outro processo lê o arquivo gravado.
    assert CacheLLM(tmp_path / "llm.json", Falso()).gerar("p", "1", _pedido(), str) == "a"


def test_cache_tenta_de_novo_e_so_grava_o_valido(tmp_path):
    def validar(texto):
        return json.loads(texto)["ok"]

    falso = Falso("não é json", '{"ok": 1}')
    cache = CacheLLM(tmp_path / "llm.json", falso)
    assert cache.gerar("p", "1", _pedido(), validar) == 1
    assert len(falso.pedidos) == 2
    assert list(json.loads((tmp_path / "llm.json").read_text()).values()) == ['{"ok": 1}']


def test_cache_desiste_depois_de_duas_tentativas(tmp_path):
    cache = CacheLLM(tmp_path / "llm.json", Falso("x", "y"))
    with pytest.raises(ErroLLM, match="2 tentativas"):
        cache.gerar("p", "1", _pedido(), json.loads)
    assert not (tmp_path / "llm.json").exists()


# ---------------------------------------------------------------------------
# Extrator
# ---------------------------------------------------------------------------


def test_extrator_valida_e_converte_para_atributos_observados(tmp_path, prompt, vocab_regras):
    saida = _saida(
        cores=["amarelo"],
        cores_por_parte=[{"parte": "barriga", "cor": "amarelo"}],
        bico={"comprimento": "curto", "espessura": None, "curvatura": None, "extra": None},
        marcas=[],  # lista vazia vira ausente
    )
    saida["local"] = {"texto": "Parque do Sabiá"}
    extrator, falso = _extrator(tmp_path, prompt, vocab_regras, saida)
    e = extrator.extrair("  uma ave amarela  ")
    assert e.atributos.cores == ["amarelo"]
    assert e.atributos.bico.comprimento == "curto"
    assert e.atributos.marcas is None
    assert (e.local, e.descricao_para_busca, e.fora_de_escopo) == (
        "Parque do Sabiá",
        "Ave pequena e amarela.",
        False,
    )
    pedido = falso.pedidos[0]
    assert pedido.modelo == "gpt-6-luna"  # o do prompt vence o padrão
    assert "<mensagem_usuario>\numa ave amarela\n</mensagem_usuario>" in pedido.entrada
    assert pedido.schema["strict"] is True


def test_extrator_tenta_de_novo_com_valor_fora_do_vocabulario(tmp_path, prompt, vocab_regras):
    extrator, falso = _extrator(
        tmp_path, prompt, vocab_regras, _saida(cores=["amarela"]), _saida(cores=["amarelo"])
    )
    assert extrator.extrair("ave amarela").atributos.cores == ["amarelo"]
    assert len(falso.pedidos) == 2


def test_estado_para_prompt(obs):
    assert json.loads(estado_para_prompt(None, None)) == {
        "atributos": {},
        "descricao_para_busca": None,
    }
    estado = json.loads(estado_para_prompt(obs(cores=["preto"]), "Ave preta."))
    assert estado == {"atributos": {"cores": ["preto"]}, "descricao_para_busca": "Ave preta."}


# ---------------------------------------------------------------------------
# Sugestor (c04_candidatas)
# ---------------------------------------------------------------------------

PROMPT_C04 = ROOT_DIR / "config" / "prompts" / "c04_candidatas.yaml"


def _sugestao(*nomes):
    return {"especies": [{"nome_popular": p, "nome_cientifico": c} for p, c in nomes]}


def test_normalizar_nome():
    assert normalizar_nome("  Bem-te-vi ") == "bem te vi"
    assert normalizar_nome("Saí-azul") == "sai azul"
    assert normalizar_nome("Pitangus  sulphuratus") == "pitangus sulphuratus"


def test_casar_pelo_cientifico_ou_pelo_popular(vocab_regras):
    from avesrag_assistant.llm.contratos import C04Candidatas

    indice = indice_de_nomes(
        [
            ("sp_btv", "Pitangus sulphuratus", ["bem-te-vi"]),
            ("sp_sai", "Dacnis cayana", ["saí-azul"]),
        ]
    )
    sugestao = C04Candidatas.model_validate(
        _sugestao(
            ("Bem-te-vi", "Pitangus sulphuratus"),
            ("Saí azul", "Dacnis errada"),  # o científico não casa; o popular, sim
            ("Tiê-sangue", "Ramphocelus bresilius"),  # fora da base
        )
    )
    assert casar(sugestao, indice) == ["sp_btv", "sp_sai", None]


def test_sugestor_devolve_so_as_da_base_sem_repetir(tmp_path, vocab_regras):
    prompt = carregar_prompt(PROMPT_C04, vocab_regras[0])
    resposta = _sugestao(
        ("Ave verde", "Ave verde"), ("Outra", "Ave inexistente"), ("ave-verde", "x y")
    )
    falso = Falso(resposta)
    sugestor = Sugestor(prompt, CacheLLM(tmp_path / "llm.json", falso), *vocab_regras, "m")
    indice = indice_de_nomes([("sp_verde", "Ave verde", ["ave-verde"])])
    assert sugestor.especies("  uma ave verde ", indice) == ["sp_verde"]
    assert "<mensagem_usuario>\numa ave verde\n</mensagem_usuario>" in falso.pedidos[0].entrada
    assert falso.pedidos[0].schema["strict"] is True
    sugestor.especies("  uma ave verde ", indice)
    assert len(falso.pedidos) == 1  # a segunda vem do cache


def test_c04_limita_a_10_especies():
    from avesrag_assistant.llm.contratos import C04Candidatas

    with pytest.raises(ValidationError):
        C04Candidatas.model_validate(_sugestao(*[("a", "b")] * 11))


def test_indice_da_base_so_com_especies_com_documento():
    from conftest import DOCS, FICHAS, BancoFalso

    # sp_sem_ficha tem documento e não tem ficha; uma ficha sem documento fica de fora.
    fichas = [*FICHAS, {"species_id": "sp_fora", "nome_cientifico": "Ave fora"}]
    indice = indice_da_base(BancoFalso(DOCS, fichas))
    assert indice["ave amarela"] == "sp_amarela"
    assert indice["outra"] == "sp_amarela"
    assert "ave fora" not in indice
