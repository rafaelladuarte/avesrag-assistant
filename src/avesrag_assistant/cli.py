"""CLI `avesrag-assistant`.

Assistant 1: `buscar` (uma consulta ao retriever) e `avaliar` (o conjunto de
avaliação nos modos de busca). Assistant 2: `extrair` (o `c01_extrator` numa
mensagem) e a opção `--sugestoes` do `buscar` e do `avaliar` (as espécies
sugeridas pelo `c04_candidatas` como candidatas com bônus); `simular` (as
perguntas medidas com um usuário simulado que responde pela ficha) e
`conversar` (a conversa inteira no terminal, com o estado em JSON por sessão).
"""

import csv
import json
from pathlib import Path

import typer
from avesrag_builder.core.vocabulario import VocabularioInvalido
from avesrag_builder.llm.base import ErroFornecedor
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from avesrag_assistant import __version__, vocabulario
from avesrag_assistant.avaliacao.casos import CasosInvalidos, carregar_casos, conferir_no_banco
from avesrag_assistant.avaliacao.relatorio import gravar_tsv, tabela
from avesrag_assistant.avaliacao.relatorio import inalcancaveis as relatorio_inalcancaveis
from avesrag_assistant.avaliacao.rodar import DEGRADADO, MODOS_AVALIACAO, rodar
from avesrag_assistant.avaliacao.simulacao import linhas_tsv, resumo_perguntas, simular
from avesrag_assistant.avaliacao.simulacao import tabela as tabela_simulacao
from avesrag_assistant.busca.cache import criar_cache
from avesrag_assistant.busca.degradado import buscar_degradado
from avesrag_assistant.busca.modelos import Candidata, validar_consulta
from avesrag_assistant.busca.pontuacao import RankingInvalido, carregar_ranking
from avesrag_assistant.busca.retriever import MODOS, TOP
from avesrag_assistant.busca.retriever import buscar as buscar_especies
from avesrag_assistant.conversa import estado as sessoes
from avesrag_assistant.conversa.config import ConversaInvalida, carregar_conversa
from avesrag_assistant.conversa.orquestrador import Orquestrador, Servicos
from avesrag_assistant.db import get_db
from avesrag_assistant.llm.cache import ErroLLM
from avesrag_assistant.llm.extrator import criar_extrator
from avesrag_assistant.llm.pergunta import criar_redator
from avesrag_assistant.llm.prompts import PromptInvalido
from avesrag_assistant.llm.sugestor import criar_sugestor, indice_da_base
from avesrag_assistant.settings import MissingSettingError, get_settings

MODO_DEGRADADO = DEGRADADO
AJUDA_MODOS_AVALIACAO = f"Modos a avaliar (pode repetir; padrão: {', '.join(MODOS_AVALIACAO)})."
AJUDA_SUGESTOES = (
    "Pede ao LLM (c04_candidatas) espécies para a descrição; nos modos que reordenam, "
    "elas entram como candidatas com o bônus de ranking.sugestoes."
)

app = typer.Typer(
    name="avesrag-assistant",
    help="Identifica aves do Cerrado a partir de uma descrição em texto.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _versao(valor: bool) -> None:
    if valor:
        typer.echo(f"avesrag-assistant {__version__}")
        raise typer.Exit()


@app.callback()
def principal(
    versao: bool = typer.Option(
        False, "--version", callback=_versao, is_eager=True, help="Mostra a versão e sai."
    ),
) -> None:
    pass


def falhar(mensagem: str, codigo: int = 1) -> None:
    typer.echo(mensagem, err=True)
    raise typer.Exit(code=codigo)


def formatar_candidatas(candidatas: list[Candidata]) -> list[str]:
    """Uma linha por candidata e, abaixo, o que bateu e o que conflitou."""
    linhas = []
    for i, c in enumerate(candidatas, start=1):
        linhas.append(
            f"{i:2}. {c.nome} ({c.nome_cientifico})  final {c.nota_final:+.2f} = "
            f"busca {c.nota_vetor:.3f} (norm. {c.nota_vetor_normalizada:.2f}) "
            f"+ pontos {c.pontos:+.2f}  [{c.dominio}]"
            + (f" + sugerida {c.bonus:+.2f}" if c.bonus else "")
            + (f"  (via {c.origem})" if c.origem not in ("vetor", "texto") else "")
        )
        bateram = [f"{d.atributo} {','.join(d.bateram)}" for d in c.detalhes if d.bateram]
        conflitaram = [
            f"{d.atributo} {','.join(d.conflitaram)}" for d in c.detalhes if d.conflitaram
        ]
        sem_dado = [d.atributo for d in c.detalhes if d.resultado == "sem_dado"]
        if bateram:
            linhas.append(f"      bate: {'; '.join(bateram)}")
        if conflitaram:
            linhas.append(f"      conflita: {'; '.join(conflitaram)}")
        if sem_dado:
            linhas.append(f"      sem dado na ficha: {', '.join(sem_dado)}")
    return linhas


@app.command()
def buscar(
    descricao: str = typer.Argument(..., help="Descrição da ave, em texto livre."),
    atributos: str = typer.Option(
        "{}", "--atributos", help="Atributos observados em JSON (AtributosObservados)."
    ),
    modo: str = typer.Option(
        "reordenado",
        "--modo",
        help=f"Modo da busca: {', '.join((*MODOS, MODO_DEGRADADO))}.",
    ),
    recusar: list[str] = typer.Option(
        [], "--recusar", help="species_id já recusado (pode repetir)."
    ),
    k: int = typer.Option(TOP, "--k", min=1, help="Quantas candidatas mostrar."),
    sugestoes: bool = typer.Option(False, "--sugestoes", help=AJUDA_SUGESTOES),
) -> None:
    """Busca as espécies candidatas para uma descrição."""
    if modo not in (*MODOS, MODO_DEGRADADO):
        falhar(f"modo desconhecido: {modo!r} (use {', '.join((*MODOS, MODO_DEGRADADO))})", 2)
    settings = get_settings()
    try:
        voc, regras = vocabulario.carregar()
        ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
    except (VocabularioInvalido, RankingInvalido) as e:
        falhar(str(e), 2)
    try:
        dados_atributos = json.loads(atributos)
    except json.JSONDecodeError as e:
        falhar(f"--atributos não é um JSON válido: {e}", 2)
    try:
        consulta = validar_consulta(
            {"descricao": descricao, "atributos": dados_atributos, "recusadas": recusar},
            voc,
            regras,
        )
    except ValidationError as e:
        erros = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        falhar(f"consulta inválida:\n{erros}", 2)

    try:
        db = get_db()
        if modo == MODO_DEGRADADO:
            candidatas = buscar_degradado(db, consulta, ranking, regras, k)
            origem_vetor = "sem embedding"
        else:
            cache = criar_cache(settings)
            vetor = cache.vetor(consulta.descricao)
            origem_vetor = (
                f"embedding novo ({cache.tokens} tokens)"
                if cache.chamadas
                else "embedding do cache"
            )
            sugeridas: list[str] = []
            if sugestoes:
                sugestor = criar_sugestor(settings, voc, regras)
                sugeridas = sugestor.especies(descricao, indice_da_base(db))
                typer.echo(f"sugeridas pelo LLM: {', '.join(sugeridas) or 'nenhuma da base'}")
            candidatas = buscar_especies(
                db, consulta, vetor, ranking, regras, modo, k, sugeridas=sugeridas
            )
    except (PromptInvalido, MissingSettingError) as e:
        falhar(str(e), 2)
    except ErroLLM as e:
        falhar(f"falha no LLM: {e}")
    except ErroFornecedor as e:
        falhar(f"falha no embedding ou no LLM: {e}")
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")

    informados = ", ".join(consulta.atributos.informados()) or "nenhum"
    typer.echo(f"modo {modo} · {origem_vetor} · atributos: {informados}")
    if not candidatas:
        typer.echo("nenhuma candidata.")
        return
    for linha in formatar_candidatas(candidatas):
        typer.echo(linha)


@app.command()
def avaliar(
    uso: str | None = typer.Option(
        None, "--uso", help="Só os casos de um uso: calibracao ou teste."
    ),
    modo: list[str] = typer.Option([], "--modo", help=AJUDA_MODOS_AVALIACAO),
    tsv: Path | None = typer.Option(
        None, "--tsv", help="Onde gravar o relatório por caso (padrão: reports/avaliacao.tsv)."
    ),
    sugestoes: bool = typer.Option(False, "--sugestoes", help=AJUDA_SUGESTOES),
) -> None:
    """Roda o conjunto de avaliação nos modos de busca e mostra top 1, top 3 e top 10."""
    modos = tuple(modo) or MODOS_AVALIACAO
    if desconhecidos := [m for m in modos if m not in MODOS_AVALIACAO]:
        falhar(f"modos desconhecidos: {desconhecidos} (use {', '.join(MODOS_AVALIACAO)})", 2)
    if uso not in (None, "calibracao", "teste"):
        falhar(f"uso desconhecido: {uso!r} (use calibracao ou teste)", 2)
    settings = get_settings()
    try:
        voc, regras = vocabulario.carregar()
        ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
        casos = carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras)
    except (VocabularioInvalido, RankingInvalido, CasosInvalidos) as e:
        falhar(str(e), 2)
    if uso:
        casos = [c for c in casos if c.uso == uso]

    try:
        db = get_db()
        conferencia = conferir_no_banco(db, casos)
        if conferencia.inexistentes:
            falhar(f"espécies esperadas fora do registry: {', '.join(conferencia.inexistentes)}", 2)
        cache = criar_cache(settings) if any(m != DEGRADADO for m in modos) else None
        sugeridas = None
        if sugestoes:
            sugestor = criar_sugestor(settings, voc, regras)
            indice = indice_da_base(db)
            sugeridas = {c.id: sugestor.especies(c.descricao, indice) for c in casos}
            llm = sugestor.cache
            typer.echo(
                f"sugestões: {llm.chamadas} chamada(s) ao LLM, "
                f"{llm.tokens_entrada} + {llm.tokens_saida} tokens"
            )
        av = rodar(db, casos, cache, ranking, regras, modos, conferencia.inalcancaveis, sugeridas)
    except (PromptInvalido, MissingSettingError) as e:
        falhar(str(e), 2)
    except ErroLLM as e:
        falhar(f"falha no LLM: {e}")
    except ErroFornecedor as e:
        falhar(f"falha no embedding ou no LLM: {e}")
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")

    typer.echo(
        f"{len(casos)} casos · ranking {ranking.versao} · "
        f"sugestões {'sim' if sugestoes else 'não'} · posições até {av.ate} · "
        f"embedding: {av.chamadas} chamada(s), {av.tokens} tokens"
    )
    for linha in tabela(av):
        typer.echo(linha)
    if fora := relatorio_inalcancaveis(av):
        typer.echo(f"inalcançáveis (sem documento de busca): {', '.join(fora)}")
    caminho = gravar_tsv(av, tsv or settings.REPORTS_DIR / "avaliacao.tsv")
    typer.echo(f"relatório: {caminho}")


@app.command()
def extrair(
    mensagem: str = typer.Argument(..., help="Mensagem da pessoa, em texto livre."),
) -> None:
    """Roda o extrator (c01) numa mensagem e mostra os atributos e a descrição reescrita."""
    settings = get_settings()
    try:
        voc, regras = vocabulario.carregar()
        extrator = criar_extrator(settings, voc, regras)
        extracao = extrator.extrair(mensagem)
    except (VocabularioInvalido, PromptInvalido) as e:
        falhar(str(e), 2)
    except MissingSettingError as e:
        falhar(str(e), 2)
    except (ErroFornecedor, ErroLLM) as e:
        falhar(f"falha no extrator: {e}")
    cache = extrator.cache
    origem = (
        f"LLM ({cache.tokens_entrada} + {cache.tokens_saida} tokens)" if cache.chamadas else "cache"
    )
    typer.echo(f"extrator {extrator.prompt.id} {extrator.prompt.versao} · {origem}")
    if extracao.fora_de_escopo:
        typer.echo("fora de escopo")
    typer.echo(f"local: {extracao.local or '-'} · data: {extracao.data_observacao or '-'}")
    typer.echo(f"descricao_para_busca: {extracao.descricao_para_busca}")
    typer.echo(
        "atributos: "
        + json.dumps(extracao.atributos.model_dump(exclude_none=True), ensure_ascii=False)
    )


@app.command(name="simular")
def simular_perguntas(
    uso: str | None = typer.Option(
        None, "--uso", help="Só os casos de um uso: calibracao ou teste."
    ),
    ruido: float = typer.Option(
        0.0,
        "--ruido",
        min=0.0,
        max=1.0,
        help="Probabilidade de a resposta trocar a cor por uma vizinha ou virar 'não sei'.",
    ),
    semente: int = typer.Option(0, "--semente", help="Semente do ruído."),
    sugestoes: bool = typer.Option(False, "--sugestoes", help=AJUDA_SUGESTOES),
    tsv: Path | None = typer.Option(
        None, "--tsv", help="Onde gravar o relatório por caso (padrão: reports/simulacao.tsv)."
    ),
) -> None:
    """Mede as perguntas: top 1, 3 e 10 depois de 0, 1, 2 e 3 respostas tiradas da ficha."""
    if uso not in (None, "calibracao", "teste"):
        falhar(f"uso desconhecido: {uso!r} (use calibracao ou teste)", 2)
    settings = get_settings()
    try:
        voc, regras = vocabulario.carregar()
        ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
        conversa = carregar_conversa(settings.CONFIG_DIR / "conversa.yaml", voc)
        casos = carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras)
    except (VocabularioInvalido, RankingInvalido, ConversaInvalida, CasosInvalidos) as e:
        falhar(str(e), 2)
    if uso:
        casos = [c for c in casos if c.uso == uso]

    try:
        db = get_db()
        conferencia = conferir_no_banco(db, casos)
        if conferencia.inexistentes:
            falhar(f"espécies esperadas fora do registry: {', '.join(conferencia.inexistentes)}", 2)
        casos = [c for c in casos if c.esperada not in conferencia.inalcancaveis]
        cache = criar_cache(settings)
        sugeridas = None
        if sugestoes:
            sugestor = criar_sugestor(settings, voc, regras)
            indice = indice_da_base(db)
            sugeridas = {c.id: sugestor.especies(c.descricao, indice) for c in casos}
        sims = simular(db, casos, cache, ranking, voc, regras, conversa, sugeridas, ruido, semente)
    except (PromptInvalido, MissingSettingError) as e:
        falhar(str(e), 2)
    except ErroLLM as e:
        falhar(f"falha no LLM: {e}")
    except ErroFornecedor as e:
        falhar(f"falha no embedding ou no LLM: {e}")
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")

    typer.echo(
        f"{len(casos)} casos alcançáveis · ranking {ranking.versao} · conversa {conversa.versao} · "
        f"modo {conversa.busca.modo} · sugestões {'sim' if sugestoes else 'não'} · "
        f"ruído {ruido:g} · posições até {ranking.vetor.especies}"
    )
    for linha in tabela_simulacao(sims):
        typer.echo(linha)
    for linha in resumo_perguntas(sims):
        typer.echo(linha)
    caminho = tsv or settings.REPORTS_DIR / "simulacao.tsv"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(linhas_tsv(sims))
    typer.echo(f"relatório: {caminho}")


SAIR = ("sair", "fim", "tchau")


@app.command()
def conversar(
    sessao: str | None = typer.Option(None, "--sessao", help="Retoma a sessão com este id."),
    mensagem: list[str] = typer.Option(
        [],
        "--mensagem",
        help="Processa esta mensagem e sai, sem o modo interativo (pode repetir).",
    ),
) -> None:
    """Conversa no terminal: descrição, perguntas, 3 candidatas e confirmação."""
    settings = get_settings()
    diretorio = settings.REPORTS_DIR / "sessoes"
    try:
        voc, regras = vocabulario.carregar()
        ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
        conversa = carregar_conversa(settings.CONFIG_DIR / "conversa.yaml", voc)
        estado = (
            sessoes.carregar(diretorio, sessao)
            if sessao
            else sessoes.EstadoConversa(id=sessoes.novo_id())
        )
        db = get_db()
        cache = criar_cache(settings)
        servicos = Servicos(
            db=db,
            ranking=ranking,
            conversa=conversa,
            vocabulario=voc,
            regras_bico=regras,
            extrator=criar_extrator(settings, voc, regras),
            redator=criar_redator(settings, voc, regras),
            vetor=cache.vetor,
            sugestor=criar_sugestor(settings, voc, regras),
            indice=indice_da_base(db),
        )
        orquestrador = Orquestrador(servicos, estado)
    except (VocabularioInvalido, RankingInvalido, ConversaInvalida, PromptInvalido) as e:
        falhar(str(e), 2)
    except (FileNotFoundError, ValueError, MissingSettingError) as e:
        falhar(str(e), 2)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")

    def turno(texto: str) -> None:
        try:
            resposta = orquestrador.processar(texto)
        except ErroFornecedor as e:
            falhar(f"falha no LLM ou no embedding: {e}")
        except PyMongoError as e:
            falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")
        sessoes.salvar(orquestrador.estado, diretorio)
        typer.echo(f"assistente: {resposta}")

    typer.echo(f"sessão {estado.id} (etapa {estado.etapa}) · retome com --sessao {estado.id}")
    if mensagem:
        for texto in mensagem:
            typer.echo(f"você: {texto}")
            turno(texto)
        return
    if not estado.mensagens:
        typer.echo("assistente: Olá! Me conte como era a ave que você viu.")
    while orquestrador.estado.etapa != "fim":
        try:
            texto = input("você: ").strip()
        except EOFError:
            break
        if not texto:
            continue
        if texto.lower() in SAIR:
            break
        turno(texto)
    typer.echo(f"sessão salva: {sessoes.caminho(diretorio, estado.id)}")
