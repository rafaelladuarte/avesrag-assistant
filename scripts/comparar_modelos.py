"""Compara modelos nos contratos do chat (`c01`, `c02`, `c04`) na calibração (27/11/2026).

Etapa aprovada pela usuária depois do teste de fumaça (`fumaca_modelos.py`). Regras:

- só os modelos de `MODELOS` (os do teste de fumaça); qualquer outro é recusado, em especial o
  `gpt-6-luna`, que é cobrado;
- só as 31 descrições de calibração das pessoas (`divisao.yaml`); o teste não é tocado aqui;
- os prompts ficam como estão (`c04` 1.0, `c01` 1.1, `c02` 1.0), para isolar o efeito do modelo;
- temperatura 0 em todas as chamadas;
- uma trava de tokens por grupo de cota (grandes 250 mil, mini e nano 2,5 milhões por dia): soma
  as chamadas das últimas 24 horas e para antes de estourar;
- as respostas ficam em `reports/analise_falhas/modelos/`, uma por caso, e a chamada só é refeita
  se não houver resposta guardada (a execução pode ser interrompida e retomada).

    poetry run python scripts/comparar_modelos.py c04 --modelos gpt-5.4 gpt-5.4-mini gpt-4.1
    poetry run python scripts/comparar_modelos.py avaliar-c04 --modelos gpt-5.4 gpt-5.4-mini gpt-4.1
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from avesrag_builder.llm.base import Pedido
from avesrag_builder.llm.openai import OpenAIFornecedor

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analise_falhas as af  # noqa: E402

from avesrag_assistant.avaliacao.extrator import comparar  # noqa: E402
from avesrag_assistant.avaliacao.extrator import itens as af_itens  # noqa: E402
from avesrag_assistant.avaliacao.simulacao import simular  # noqa: E402
from avesrag_assistant.llm.contratos import C02Pergunta, C04Candidatas  # noqa: E402
from avesrag_assistant.llm.extrator import Extrator, estado_para_prompt  # noqa: E402
from avesrag_assistant.llm.pergunta import RedatorPergunta  # noqa: E402
from avesrag_assistant.llm.prompts import carregar_prompt  # noqa: E402
from avesrag_assistant.llm.sugestor import Sugestor, casar, indice_da_base  # noqa: E402

MODELOS = {
    "gpt-5.4": "grande",
    "gpt-4.1": "grande",
    "gpt-5.4-mini": "mini",
    "gpt-4.1-mini": "mini",
    "gpt-5.4-nano": "mini",
}
LIMITE_24H = {"grande": 235_000, "mini": 2_400_000}  # abaixo de 250 mil e de 2,5 milhões
MARGEM_CHAMADA = 6_000  # tokens que uma chamada pode somar além do já gasto
SEMENTES = 5


class CotaPerto(RuntimeError):
    """A próxima chamada poderia passar da cota gratuita: parar e avisar."""


# -- contabilidade de tokens -------------------------------------------------------------------


class Uso:
    """Tokens das últimas 24 horas por grupo, em `modelos/uso_tokens.json` (inclui a fumaça)."""

    def __init__(self, pasta: Path):
        self.arquivo = pasta / "uso_tokens.json"
        if self.arquivo.exists():
            self.eventos = json.loads(self.arquivo.read_text(encoding="utf-8"))
        else:
            self.eventos = []
            fumaca = pasta.parent / "fumaca_modelos.json"
            if fumaca.exists():  # a fumaça de hoje já consumiu parte da cota
                agora = datetime.now(UTC).isoformat()
                for r in json.loads(fumaca.read_text(encoding="utf-8")):
                    self.eventos.append(
                        [agora, MODELOS[r["modelo"]], r["tokens_entrada"] + r["tokens_saida"]]
                    )

    def ultimas_24h(self, grupo: str) -> int:
        corte = datetime.now(UTC) - timedelta(hours=24)
        return sum(
            t
            for quando, g, t in self.eventos
            if g == grupo and datetime.fromisoformat(quando) > corte
        )

    def checar(self, modelo: str) -> None:
        grupo = MODELOS[modelo]
        if self.ultimas_24h(grupo) + MARGEM_CHAMADA > LIMITE_24H[grupo]:
            raise CotaPerto(
                f"{grupo}: {self.ultimas_24h(grupo)} tokens nas últimas 24 h; "
                f"limite de segurança {LIMITE_24H[grupo]}"
            )

    def registrar(self, modelo: str, tokens: int) -> None:
        self.eventos.append([datetime.now(UTC).isoformat(), MODELOS[modelo], tokens])
        self.arquivo.write_text(json.dumps(self.eventos), encoding="utf-8")


def conferir_modelos(modelos: list[str]) -> None:
    fora = [m for m in modelos if m not in MODELOS]
    if fora:
        raise SystemExit(f"modelo(s) não aprovados: {fora}; permitidos: {sorted(MODELOS)}")


# -- chamadas ----------------------------------------------------------------------------------


class Contexto:
    def __init__(self, lado: str = "calibracao"):
        self.amb = af.montar()
        s = self.amb.settings
        s.require("OPENAI_API_KEY")
        self.pasta = s.REPORTS_DIR / "analise_falhas" / "modelos"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.fornecedor = OpenAIFornecedor(s.OPENAI_API_KEY.get_secret_value())
        self.uso = Uso(self.pasta)
        voc, regras = self.amb.voc, self.amb.regras
        bloqueado = af.CacheLLM(self.pasta / "_nunca_usado.json", af.FornecedorBloqueado())
        cfg = s.CONFIG_DIR / "prompts"
        self.sug = Sugestor(
            carregar_prompt(cfg / "c04_candidatas.yaml", voc), bloqueado, voc, regras, s.LLM_MODEL
        )
        self.ext = Extrator(
            carregar_prompt(cfg / "c01_extrator.yaml", voc), bloqueado, voc, regras, s.LLM_MODEL
        )
        self.red = RedatorPergunta(
            carregar_prompt(cfg / "c02_pergunta.yaml", voc), bloqueado, voc, regras, s.LLM_MODEL
        )
        divisao = af.ler_divisao(self.amb.saida)
        self.lado = lado
        self.prefixo = "" if lado == "calibracao" else "teste_"
        self.casos_lado = [c for c in self.amb.casos if divisao[c.id] == lado]
        self.calibracao = [c for c in self.amb.casos if divisao[c.id] == "calibracao"]

    def chamar(self, modelo: str, nome: str, system: str, entrada: str, schema: dict) -> dict:
        self.uso.checar(modelo)
        pedido = Pedido(
            id=f"cmp:{nome}:{modelo}",
            modelo=modelo,
            system=system,
            entrada=entrada,
            schema=schema,
            temperatura=0.0,
        )
        t0 = time.perf_counter()
        r = self.fornecedor.gerar(pedido)
        seg = round(time.perf_counter() - t0, 1)
        self.uso.registrar(modelo, r.tokens_entrada + r.tokens_saida)
        return {
            "conteudo": r.conteudo,
            "erro": r.erro,
            "segundos": seg,
            "tokens_entrada": r.tokens_entrada,
            "tokens_saida": r.tokens_saida,
        }


def carregar(arquivo: Path) -> dict:
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def gravar(arquivo: Path, dados: dict) -> None:
    arquivo.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")


def cmd_c04(ctx: Contexto, modelos: list[str], repeticoes: int) -> None:
    for r in range(repeticoes):
        for modelo in modelos:
            arquivo = ctx.pasta / f"{ctx.prefixo}c04_{modelo}_r{r}.json"
            dados = carregar(arquivo)
            for caso in ctx.casos_lado:
                if caso.id in dados:
                    continue
                entrada = ctx.sug.prompt.render_entrada(mensagem=caso.descricao.strip())
                dados[caso.id] = ctx.chamar(
                    modelo, "c04", ctx.sug._system, entrada, ctx.sug._schema
                )
                gravar(arquivo, dados)
            print(f"c04 {modelo} r{r}: {len(dados)} respostas; tokens 24h "
                  f"{ctx.uso.ultimas_24h(MODELOS[modelo])} ({MODELOS[modelo]})", flush=True)  # fmt: skip


def cmd_c01(ctx: Contexto, modelos: list[str]) -> None:
    for modelo in modelos:
        arquivo = ctx.pasta / f"{ctx.prefixo}c01_{modelo}.json"
        dados = carregar(arquivo)
        for caso in ctx.casos_lado:
            if caso.id in dados:
                continue
            entrada = ctx.ext.prompt.render_entrada(
                estado=estado_para_prompt(None, None),
                pergunta="nenhuma",
                mensagem=caso.descricao.strip(),
            )
            dados[caso.id] = ctx.chamar(modelo, "c01", ctx.ext._system, entrada, ctx.ext._schema)
            gravar(arquivo, dados)
        print(f"c01 {modelo}: {len(dados)} respostas; tokens 24h "
              f"{ctx.uso.ultimas_24h(MODELOS[modelo])} ({MODELOS[modelo]})", flush=True)  # fmt: skip


def cmd_c02(ctx: Contexto, modelos: list[str]) -> None:
    pedido = ctx.amb.conversa.perguntas.pedidos["bico:espessura"]
    estado = json.dumps(
        {"cores": ["azul"], "cores_por_parte": [{"parte": "cabeca", "cor": "preto"}]},
        ensure_ascii=False,
    )
    entrada = ctx.red.prompt.render_entrada(pedido=pedido, estado=estado)
    arquivo = ctx.pasta / "c02.json"
    dados = carregar(arquivo)
    for modelo in modelos:
        if modelo not in dados:
            dados[modelo] = ctx.chamar(modelo, "c02", ctx.red._system, entrada, ctx.red._schema)
            gravar(arquivo, dados)
        try:
            texto = C02Pergunta.model_validate_json(dados[modelo]["conteudo"]).pergunta.strip()
        except (ValueError, TypeError):
            texto = None
        ok = bool(texto) and texto.endswith("?") and len(texto) <= 120
        print(f"c02 {modelo:14s} {'ok' if ok else 'FORA DA REGRA'}: {texto!r}", flush=True)


# -- avaliação ---------------------------------------------------------------------------------


def sugeridas_do(dados: dict, indice: dict, casos) -> tuple[dict[str, list[str]], dict]:
    saida, estat = {}, {"sugeridas": [], "casadas": [], "invalidas": 0}
    for caso in casos:
        bruto = (dados.get(caso.id) or {}).get("conteudo")
        try:
            sugestao = C04Candidatas.model_validate_json(bruto)
        except (ValueError, TypeError):
            estat["invalidas"] += 1
            saida[caso.id] = []
            continue
        ids = list(dict.fromkeys(i for i in casar(sugestao, indice) if i))
        saida[caso.id] = ids
        estat["sugeridas"].append(len(sugestao.especies))
        estat["casadas"].append(len(ids))
    return saida, estat


def recall(sugeridas: dict[str, list[str]], casos, k: int) -> float:
    return sum(1 for c in casos if c.esperada in sugeridas[c.id][:k]) / len(casos)


def simular_com(amb, casos, sugeridas, sementes: int = SEMENTES) -> dict:
    por_semente = []
    for s in range(sementes):
        sims = simular(
            amb.db,
            casos,
            amb.cache,
            amb.ranking,
            amb.voc,
            amb.regras,
            amb.conversa,
            sugeridas,
            0.0,
            s,
            True,
        )
        por_semente.append(sims)
    fracao = {
        s.caso.id: sum(
            1
            for sims in por_semente
            for x in sims
            if x.caso.id == s.caso.id and x.posicoes[3] is not None and x.posicoes[3] <= 3
        )
        / len(por_semente)
        for s in por_semente[0]
    }
    pos0 = {s.caso.id: s.posicoes[0] for s in por_semente[0]}
    t3 = [af.topo([x.posicoes[3] for x in sims], 3) for sims in por_semente]
    t10 = [af.topo([x.posicoes[3] for x in sims], 10) for sims in por_semente]
    zero = af.topo([x.posicoes[0] for x in por_semente[0]], 3)
    return {"top3_0": zero, "top3_3": t3, "top10_3": t10, "fracao": fracao, "pos0": pos0}


def cmd_avaliar_c04(ctx: Contexto, modelos: list[str], repeticoes: int) -> None:
    amb, casos = ctx.amb, ctx.calibracao
    indice = indice_da_base(amb.db)
    linhas = ["| Fonte | Sugestões/caso (casadas) | Recall@1 | @3 | @10 | Top 3 (0 perg.) | Top 3 (3 perg.) média (faixa) | Top 10 (3 perg.) |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip

    def linha(nome: str, sugeridas: dict, estat: dict | None) -> str:
        m = simular_com(amb, casos, sugeridas)
        n = (
            f"{statistics.mean(estat['sugeridas']):.1f} ({statistics.mean(estat['casadas']):.1f})"
            if estat and estat["sugeridas"]
            else "3,3 (—)"
        )
        return (
            f"| {nome} | {n} | {recall(sugeridas, casos, 1):.0%} | {recall(sugeridas, casos, 3):.0%} | "
            f"{recall(sugeridas, casos, 10):.0%} | {m['top3_0']:.0%} | {af.faixa(m['top3_3'])} | "
            f"{statistics.mean(m['top10_3']):.0%} |"
        )

    referencia = {c.id: amb.sugeridas[c.id] for c in casos}
    linhas.append(linha("gpt-6-luna (cache de 24/11)", referencia, None))
    print(linhas[-1], flush=True)
    for modelo in modelos:
        for r in range(repeticoes):
            dados = carregar(ctx.pasta / f"c04_{modelo}_r{r}.json")
            if len(dados) < len(casos):
                print(f"{modelo} r{r}: só {len(dados)} de {len(casos)} respostas, pulado")
                continue
            sugeridas, estat = sugeridas_do(dados, indice, casos)
            linhas.append(linha(f"{modelo} r{r}", sugeridas, estat))
            print(linhas[-1], flush=True)
    (ctx.pasta / "c04_resultados.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def cmd_avaliar_c01(ctx: Contexto, modelos: list[str]) -> None:
    casos = ctx.calibracao
    linhas = ["| Modelo | Respostas válidas | Precisão | Cobertura | Latência média | Tokens de entrada/saída por chamada |", "|---|---|---|---|---|---|"]  # fmt: skip
    for modelo in modelos:
        dados = carregar(ctx.pasta / f"c01_{modelo}.json")
        extraidos, invalidas = {}, 0
        for c in casos:
            try:
                extraidos[c.id] = ctx.ext._validar(dados[c.id]["conteudo"]).atributos
            except (KeyError, ValueError, TypeError):
                invalidas += 1
        total = comparar(casos, extraidos)["total"]
        chamadas = [dados[c.id] for c in casos if c.id in dados]
        seg = statistics.mean(x["segundos"] for x in chamadas) if chamadas else float("nan")
        tin = statistics.mean(x["tokens_entrada"] for x in chamadas) if chamadas else 0
        tout = statistics.mean(x["tokens_saida"] for x in chamadas) if chamadas else 0
        linhas.append(
            f"| {modelo} | {len(extraidos)}/{len(casos)} | {total.precisao:.0%} | {total.cobertura:.0%} | {seg:.1f} s | {tin:.0f} / {tout:.0f} |"
        )
        print(linhas[-1], flush=True)
    (ctx.pasta / "c01_resultados.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")


def cmd_simular_c01(ctx: Contexto, modelos: list[str]) -> None:
    """Etapa (a): a simulação com os atributos EXTRAÍDOS pelo `c01` de cada modelo, em vez da anotação.

    Só troca de onde vêm os atributos iniciais; as sugestões do `c04` ficam fixas (duas fontes) e as
    respostas simuladas às perguntas seguem a ficha, como antes. Não chama a API."""
    amb, casos = ctx.amb, ctx.calibracao
    indice = indice_da_base(amb.db)
    fontes = {
        "gpt-6-luna (cache)": {c.id: amb.sugeridas[c.id] for c in casos},
        "gpt-5.4 (r1)": sugeridas_do(carregar(ctx.pasta / "c04_gpt-5.4_r1.json"), indice, casos)[0],
    }
    extraidos: dict[str, dict] = {}
    for modelo in modelos:
        dados = carregar(ctx.pasta / f"c01_{modelo}.json")
        extraidos[modelo] = {
            c.id: ctx.ext._validar(dados[c.id]["conteudo"]).atributos for c in casos
        }
    linhas = ["| Sugestões do `c04` | Atributos iniciais de | Itens a mais / a menos que a anotação | Top 3 (0 perg.) | Top 3 (3 perg.) média (faixa) | Top 10 (3 perg.) | Casos +/− no top 3 (3 perg.) | Casos que entram/saem do top 3 (0 perg.) |", "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for nome_fonte, sugeridas in fontes.items():
        base = simular_com(amb, casos, sugeridas)
        linhas.append(
            f"| {nome_fonte} | anotação | — | {base['top3_0']:.0%} | {af.faixa(base['top3_3'])} | {statistics.mean(base['top10_3']):.0%} | — | — |"
        )
        print(linhas[-1], flush=True)
        for modelo in modelos:
            casos_ext = [c.model_copy(update={"atributos": extraidos[modelo][c.id]}) for c in casos]
            m = simular_com(amb, casos_ext, sugeridas)
            mais = sum(
                len(af_itens(extraidos[modelo][c.id]) - af_itens(c.atributos)) for c in casos
            )
            menos = sum(
                len(af_itens(c.atributos) - af_itens(extraidos[modelo][c.id])) for c in casos
            )
            ganha = sum(1 for c in m["fracao"] if m["fracao"][c] - base["fracao"][c] >= 0.4)
            perde = sum(1 for c in m["fracao"] if base["fracao"][c] - m["fracao"][c] >= 0.4)
            entra = sum(1 for c in m["pos0"] if (m["pos0"][c] or 99) <= 3 < (base["pos0"][c] or 99))
            sai = sum(1 for c in m["pos0"] if (base["pos0"][c] or 99) <= 3 < (m["pos0"][c] or 99))
            linhas.append(
                f"| {nome_fonte} | {modelo} | +{mais} / −{menos} | {m['top3_0']:.0%} | {af.faixa(m['top3_3'])} | {statistics.mean(m['top10_3']):.0%} | +{ganha} / −{perde} | +{entra} / −{sai} |"
            )
            print(linhas[-1], flush=True)
    (ctx.pasta / "c01_simulacao.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")


# -- teste (uma vez) ---------------------------------------------------------------------------

APROVADOS_TESTE = {"c04": {"gpt-5.4"}, "c01": {"gpt-5.4-mini"}, "c02": {"gpt-5.4-nano"}}
SEMENTES_TESTE = 10  # como no baseline do teste (58%, faixa 47 a 68%)


def conferir_teste(cmd: str, modelos: list[str], repeticoes: int = 1) -> None:
    contrato = cmd.split("-")[0]
    fora = [m for m in modelos if m not in APROVADOS_TESTE.get(contrato, set())]
    if fora or repeticoes != 1:
        raise SystemExit(
            f"teste: só {APROVADOS_TESTE.get(contrato)} e 1 repetição; recebi {modelos}, {repeticoes}"
        )


def cmd_c02_teste(ctx: Contexto, modelo: str) -> None:
    """15 perguntas variadas (os pedidos do `conversa.yaml`), com o estado de cada descrição do teste."""
    ped = ctx.amb.conversa.perguntas
    pedidos = [(k, v) for k, v in ped.pedidos.items() if "{parte}" not in v]
    nomes = ctx.amb.conversa.perguntas.nomes_partes
    pedidos += [
        (f"cores_por_parte:{p}", ped.pedidos["cores_por_parte"].format(parte=nomes[p]))
        for p in ("cabeca", "peito", "barriga", "dorso", "asas", "olho")
    ]
    pedidos = pedidos[:15]
    arquivo = ctx.pasta / f"teste_c02_{modelo}.json"
    dados = carregar(arquivo)
    for i, (chave, pedido) in enumerate(pedidos):
        if chave in dados:
            continue
        caso = ctx.casos_lado[i % len(ctx.casos_lado)]
        estado = json.dumps(caso.atributos.model_dump(exclude_none=True), ensure_ascii=False)
        entrada = ctx.red.prompt.render_entrada(pedido=pedido, estado=estado)
        dados[chave] = ctx.chamar(modelo, "c02", ctx.red._system, entrada, ctx.red._schema) | {
            "pedido": pedido,
            "estado": estado,
            "caso": caso.id,
        }
        gravar(arquivo, dados)
    print(f"c02 teste {modelo}: {len(dados)} perguntas", flush=True)


PALAVRAS_DE_VALOR = ("azul", "verde", "vermelh", "amarel", "laranja", "preto", "branco", "marrom", "cinza", "roxo", "bege", "rosa", "listrad", "topete", "curto", "comprid", "fino", "grosso", "curvo", "reto", "chão", "copa", "árvore", "água")  # fmt: skip


def avaliar_c02(dados: dict) -> list[str]:
    linhas = [
        "| Pedido | Pergunta | Tamanho | Termina em ? | Cita valor que o pedido e o estado não citam |",
        "|---|---|---|---|---|",
    ]
    for chave, x in dados.items():
        try:
            texto = C02Pergunta.model_validate_json(x["conteudo"]).pergunta.strip()
        except (ValueError, TypeError):
            linhas.append(f"| {chave} | (inválida) | | | |")
            continue
        base = (x["pedido"] + " " + x["estado"]).lower()
        citados = [w for w in PALAVRAS_DE_VALOR if w in texto.lower() and w not in base]
        linhas.append(
            f"| {chave} | {texto} | {len(texto)} | {'sim' if texto.endswith('?') else 'NÃO'} | {', '.join(citados) or 'não'} |"
        )
    return linhas


def cmd_avaliar_teste(ctx: Contexto) -> None:
    amb, casos = ctx.amb, ctx.casos_lado
    indice = indice_da_base(amb.db)
    luna = {c.id: amb.sugeridas[c.id] for c in casos}
    novo, estat = sugeridas_do(carregar(ctx.pasta / "teste_c04_gpt-5.4_r0.json"), indice, casos)
    dados_c01 = carregar(ctx.pasta / "teste_c01_gpt-5.4-mini.json")
    extraidos, invalidas = {}, 0
    for c in casos:
        try:
            extraidos[c.id] = ctx.ext._validar(dados_c01[c.id]["conteudo"]).atributos
        except (KeyError, ValueError, TypeError):
            invalidas += 1
    total = comparar(casos, extraidos)["total"]
    casos_ext = [c.model_copy(update={"atributos": extraidos[c.id]}) for c in casos]
    saida = ["# Teste (19 descrições, uma vez)", "", "## `c04`: recall das sugestões", "", "| Fonte | Sugestões/caso (casadas) | Recall@1 | @3 | @10 |", "|---|---|---|---|---|"]  # fmt: skip
    for nome, sug, est in (("gpt-6-luna (cache)", luna, None), ("gpt-5.4 (r0)", novo, estat)):
        n = (
            f"{statistics.mean(est['sugeridas']):.1f} ({statistics.mean(est['casadas']):.1f})"
            if est
            else "—"
        )
        saida.append(
            f"| {nome} | {n} | {recall(sug, casos, 1):.0%} | {recall(sug, casos, 3):.0%} | {recall(sug, casos, 10):.0%} |"
        )
    casos_a_3 = sum(1 for c in casos if c.esperada in luna[c.id][:3])
    casos_n_3 = sum(1 for c in casos if c.esperada in novo[c.id][:3])
    saida += ["", f"Recall@3 em casos: gpt-6-luna {casos_a_3} de {len(casos)}; gpt-5.4 {casos_n_3} de {len(casos)}.", "", "## `c01`: gpt-5.4-mini", "", f"Respostas válidas {len(extraidos)}/{len(casos)}; precisão {total.precisao:.0%}; cobertura {total.cobertura:.0%}.", "", f"## Simulação do top 3 ({SEMENTES_TESTE} sementes, ruído calibrado, 3 perguntas)", "", "| Sugestões do `c04` | Atributos iniciais | Top 3 (0 perg.) | Top 3 (3 perg.) média (faixa) | Top 10 (3 perg.) |", "|---|---|---|---|---|"]  # fmt: skip
    resultados = {}
    for nome_s, sug in (("gpt-6-luna (cache)", luna), ("gpt-5.4 (r0)", novo)):
        for nome_a, lista in (("anotação", casos), ("extração gpt-5.4-mini", casos_ext)):
            m = simular_com(amb, lista, sug, SEMENTES_TESTE)
            resultados[(nome_s, nome_a)] = m
            saida.append(
                f"| {nome_s} | {nome_a} | {m['top3_0']:.0%} | {af.faixa(m['top3_3'])} | {statistics.mean(m['top10_3']):.0%} |"
            )
            print(saida[-1], flush=True)
    base = statistics.mean(resultados[("gpt-6-luna (cache)", "anotação")]["top3_3"])
    cand = statistics.mean(resultados[("gpt-5.4 (r0)", "extração gpt-5.4-mini")]["top3_3"])
    ok_top = cand >= base - 0.10
    ok_rec = casos_n_3 >= casos_a_3 - 2
    saida += ["", "## Regra de adoção (combinada antes do teste)", "", f"- Top 3 com 3 perguntas do candidato (extração `gpt-5.4-mini` + sugestões `gpt-5.4`): {cand:.0%}; baseline (anotação + `gpt-6-luna`): {base:.0%}; limite: baseline − 10 pontos = {base - 0.10:.0%} → **{'passa' if ok_top else 'NÃO passa'}**.", f"- Recall@3 do `c04`: {casos_n_3} casos contra {casos_a_3}; limite: no máximo 2 casos a menos → **{'passa' if ok_rec else 'NÃO passa'}**.", f"- Veredito: **{'as duas condições passam' if ok_top and ok_rec else 'a regra NÃO foi atendida'}**.", "", "## `c02`: gpt-5.4-nano (15 perguntas)", ""]  # fmt: skip
    arq02 = ctx.pasta / "teste_c02_gpt-5.4-nano.json"
    if arq02.exists():
        saida += avaliar_c02(carregar(arq02))
    (ctx.pasta / "teste_resultados.md").write_text("\n".join(saida) + "\n", encoding="utf-8")


def principal() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nome in (
        "c04",
        "avaliar-c04",
        "c01",
        "avaliar-c01",
        "c02",
        "simular-c01",
        "c04-teste",
        "c01-teste",
        "c02-teste",
    ):
        p = sub.add_parser(nome)
        p.add_argument("--modelos", nargs="+", required=True)
        if nome in ("c04", "avaliar-c04", "c04-teste"):
            p.add_argument("--repeticoes", type=int, default=1 if nome == "c04-teste" else 3)
    sub.add_parser("avaliar-teste")  # lê só o que já foi guardado; não chama a API
    args = ap.parse_args()
    teste = args.cmd.endswith("-teste")
    if args.cmd != "avaliar-teste":
        conferir_modelos(args.modelos)
    if teste and args.cmd != "avaliar-teste":
        conferir_teste(args.cmd, args.modelos, getattr(args, "repeticoes", 1))
    ctx = Contexto("teste" if teste else "calibracao")
    try:
        if args.cmd in ("c04", "c04-teste"):
            cmd_c04(ctx, args.modelos, args.repeticoes)
        elif args.cmd == "avaliar-c04":
            cmd_avaliar_c04(ctx, args.modelos, args.repeticoes)
        elif args.cmd in ("c01", "c01-teste"):
            cmd_c01(ctx, args.modelos)
        elif args.cmd == "avaliar-c01":
            cmd_avaliar_c01(ctx, args.modelos)
        elif args.cmd == "simular-c01":
            cmd_simular_c01(ctx, args.modelos)
        elif args.cmd == "c02-teste":
            cmd_c02_teste(ctx, args.modelos[0])
        elif args.cmd == "avaliar-teste":
            cmd_avaliar_teste(ctx)
        else:
            cmd_c02(ctx, args.modelos)
    except CotaPerto as e:
        raise SystemExit(f"parado antes de estourar a cota gratuita: {e}") from e


if __name__ == "__main__":
    principal()
