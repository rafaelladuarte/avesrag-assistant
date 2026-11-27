"""CLI `avesrag-assistant`.

Assistant 1: `buscar` (uma consulta ao retriever) e `avaliar` (o conjunto de
avaliação nos modos de busca). Assistant 2: `extrair` (o `c01_extrator` numa
mensagem) e a opção `--sugestoes` do `buscar` e do `avaliar` (as espécies
sugeridas pelo `c04_candidatas` como candidatas com bônus); `simular` (as
perguntas medidas com um usuário simulado que responde pela ficha) e
`conversar` (a conversa inteira no terminal, com o estado em JSON por sessão),
`moderar` (a mesma conversa numa página local, para as sessões moderadas) e
`sessoes` (o relatório dessas sessões) e `sortear-aves` (as aves e as fotos
dessas sessões; encerradas em 27/11) e `beta` (o relatório do beta fechado, que as
substitui como teste com pessoas). Assistant 4: `servir` (a API e a página do beta fechado, só em
127.0.0.1) e `convite` (criar, listar e revogar os convites).
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
from avesrag_assistant.avaliacao import beta as beta_conversas
from avesrag_assistant.avaliacao.casos import CasosInvalidos, carregar_casos, conferir_no_banco
from avesrag_assistant.avaliacao.relatorio import gravar_tsv, tabela
from avesrag_assistant.avaliacao.relatorio import inalcancaveis as relatorio_inalcancaveis
from avesrag_assistant.avaliacao.rodar import DEGRADADO, MODOS_AVALIACAO, rodar
from avesrag_assistant.avaliacao.sessoes import (
    ARQUIVO_TSV,
    carregar_moderadas,
    especies_de_calibracao,
    resumir,
)
from avesrag_assistant.avaliacao.sessoes import gravar_tsv as gravar_tsv_sessoes
from avesrag_assistant.avaliacao.sessoes import linhas as linhas_sessoes
from avesrag_assistant.avaliacao.sessoes import linhas_do_teste as linhas_do_teste_sessoes
from avesrag_assistant.avaliacao.simulacao import (
    entradas_e_saidas,
    linhas_tsv,
    resumo_perguntas,
    simular,
)
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
from avesrag_assistant.servicos import construir_servicos
from avesrag_assistant.settings import MissingSettingError, get_settings

MODO_DEGRADADO = DEGRADADO
AJUDA_MODOS_AVALIACAO = f"Modos a avaliar (pode repetir; padrão: {', '.join(MODOS_AVALIACAO)})."
AJUDA_SUGESTOES = (
    "Pede ao LLM (c04_candidatas) espécies para a descrição; nos modos que reordenam, "
    "elas entram como candidatas com o bônus de ranking.sugestoes."
)

convite_app = typer.Typer(
    help="Convites do beta fechado (o token aparece uma vez; o arquivo guarda só o hash).",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
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
    calibrado: bool = typer.Option(
        False,
        "--calibrado",
        help="Ruído calibrado pelos formulários: respostas que conflitam com a ficha "
        "(simulacao.conflito) e 'não sei' (simulacao.nao_sei); ignora --ruido.",
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
        sims = simular(
            db, casos, cache, ranking, voc, regras, conversa, sugeridas, ruido, semente, calibrado
        )
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
        f"ruído {'calibrado' if calibrado else f'{ruido:g}'} · semente {semente} · "
        f"posições até {ranking.vetor.especies}"
    )
    for linha in tabela_simulacao(sims):
        typer.echo(linha)
    for linha in resumo_perguntas(sims):
        typer.echo(linha)
    entraram, sairam = entradas_e_saidas(sims)
    typer.echo(f"com as perguntas: {entraram} caso(s) entraram no top 3 e {sairam} saíram")
    caminho = tsv or settings.REPORTS_DIR / "simulacao.tsv"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter="\t", lineterminator="\n").writerows(linhas_tsv(sims))
    typer.echo(f"relatório: {caminho}")


SAIR = ("sair", "fim", "tchau")


def _servicos(settings, voc, regras) -> Servicos:
    """Os serviços da conversa: Mongo, ranking, regras e os três prompts do chat."""
    return construir_servicos(
        settings,
        voc,
        regras,
        db=get_db(),
        extrator=criar_extrator,
        redator=criar_redator,
        sugestor=criar_sugestor,
        cache_embeddings=criar_cache,
    )


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
        estado = (
            sessoes.carregar(diretorio, sessao)
            if sessao
            else sessoes.EstadoConversa(id=sessoes.novo_id())
        )
        orquestrador = Orquestrador(_servicos(settings, voc, regras), estado)
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


@app.command()
def moderar(
    porta: int = typer.Option(8765, "--porta", min=1, max=65535, help="Porta local."),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Endereço; o padrão só aceita conexões desta máquina."
    ),
    aves_csv: Path | None = typer.Option(
        None,
        "--aves",
        help="CSV das aves (padrão: avaliacao/especies_sessoes.csv, do sortear-aves).",
    ),
    permitir_calibracao: bool = typer.Option(
        False,
        "--permitir-calibracao",
        help="Aceita aves da calibração (só para ensaiar a página: essas sessões não valem no teste).",
    ),
) -> None:
    """Página local para as sessões moderadas: a pessoa vê a foto e conversa com o assistente."""
    from avesrag_assistant.web.servidor import Moderacao, carregar_aves, criar_servidor

    settings = get_settings()
    try:
        voc, regras = vocabulario.carregar()
        aves = carregar_aves(aves_csv or settings.AVALIACAO_DIR / "especies_sessoes.csv")
        na_calibracao = sorted(
            a.codigo
            for a in aves.values()
            if a.species_id in especies_de_calibracao(settings.AVALIACAO_DIR / "casos.yaml")
        )
        if na_calibracao and not permitir_calibracao:
            falhar(
                f"aves da calibração, que não servem para o teste (a foto entrega a resposta): "
                f"{', '.join(na_calibracao)}. Use as aves de sortear-aves ou --permitir-calibracao.",
                2,
            )
        moderacao = Moderacao(
            _servicos(settings, voc, regras), aves, settings.REPORTS_DIR / "sessoes"
        )
        servidor = criar_servidor(moderacao, host, porta)
    except (VocabularioInvalido, RankingInvalido, ConversaInvalida, PromptInvalido) as e:
        falhar(str(e), 2)
    except (FileNotFoundError, MissingSettingError, OSError) as e:
        falhar(str(e), 2)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")
    typer.echo(f"sessões moderadas em http://{host}:{porta}/ ({len(aves)} aves; Ctrl+C para sair)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
    typer.echo(f"sessões salvas em {settings.REPORTS_DIR / 'sessoes'}")


@app.command()
def servir(
    porta: int = typer.Option(8000, "--porta", min=1, max=65535, help="Porta local."),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Endereço; o padrão só aceita conexões desta máquina."
    ),
    sessoes_em: str = typer.Option(
        "mongo",
        "--sessoes",
        help="Onde guardar as sessões: mongo (collection conversations) ou arquivo.",
    ),
) -> None:
    """API e página do beta fechado (por convite), para expor por um túnel com HTTPS."""
    import uvicorn

    from avesrag_assistant.api.config import ConfigInvalida
    from avesrag_assistant.api.fabrica import SemConvites, criar_app_do_ambiente

    if sessoes_em not in ("mongo", "arquivo"):
        falhar("--sessoes deve ser mongo ou arquivo", 2)
    settings = get_settings()
    try:
        app_web = criar_app_do_ambiente(settings, sessoes_em)  # type: ignore[arg-type]
    except (
        VocabularioInvalido,
        RankingInvalido,
        ConversaInvalida,
        PromptInvalido,
        ConfigInvalida,
        SemConvites,
    ) as e:
        falhar(str(e), 2)
    except (FileNotFoundError, MissingSettingError, OSError) as e:
        falhar(str(e), 2)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")
    typer.echo(f"beta em http://{host}:{porta}/ (sessões: {sessoes_em}; Ctrl+C para sair)")
    uvicorn.run(
        app_web, host=host, port=porta, log_level="warning", proxy_headers=False, access_log=False
    )


@convite_app.command("criar")
def convite_criar(
    nome: str = typer.Argument(..., help="Quem vai usar (aparece na sessão)."),
    curadoria: bool = typer.Option(
        False, "--curadoria", help="Papel de curadoria: pode corrigir as fichas pela página."
    ),
) -> None:
    """Cria um convite e mostra o link; o token não fica guardado."""
    from avesrag_assistant.api.convites import ConviteInvalido, Convites

    try:
        token = Convites(get_settings().CONVITES_PATH).criar(
            nome, "curadoria" if curadoria else "convidado"
        )
    except ConviteInvalido as e:
        falhar(str(e), 2)
    typer.echo(f"convite de {nome}: guarde agora, o token não aparece de novo")
    typer.echo(f"  /entrar?c={token}")
    typer.echo(
        "  (junte ao endereço do túnel, ex.: https://exemplo.trycloudflare.com/entrar?c=...)"
    )


@convite_app.command("listar")
def convite_listar() -> None:
    """Os convites criados e se estão ativos."""
    from avesrag_assistant.api.convites import Convites

    arquivo = Convites(get_settings().CONVITES_PATH)
    convites = arquivo.listar()
    for nome, criado, revogado in convites:
        estado = "revogado" if revogado else "ativo"
        typer.echo(f"{nome}\t{arquivo.papel_de(nome)}\t{criado}\t{estado}")
    if not convites:
        typer.echo("nenhum convite")


@convite_app.command("revogar")
def convite_revogar(nome: str = typer.Argument(..., help="O nome do convite.")) -> None:
    """Revoga um convite: o link deixa de funcionar na próxima requisição."""
    from avesrag_assistant.api.convites import ConviteInvalido, Convites

    try:
        Convites(get_settings().CONVITES_PATH).revogar(nome)
    except ConviteInvalido as e:
        falhar(str(e), 2)
    typer.echo(f"convite de {nome} revogado")


app.add_typer(convite_app, name="convite")


@app.command(name="revisar-fichas")
def revisar_fichas(
    saida: Path | None = typer.Option(
        None, "--saida", help="CSV de saída (padrão: avaliacao/revisao_fichas.csv)."
    ),
) -> None:
    """Lista as fichas para revisão manual (conflitos das avaliações e sessões, coerência, backlog)."""
    from avesrag_builder.core.regras_fusao import carregar_regras_fusao

    from avesrag_assistant.avaliacao import revisao
    from avesrag_assistant.busca.modelos import validar_atributos

    settings = get_settings()
    try:
        voc, regras_bico = vocabulario.carregar()
        ranking = carregar_ranking(settings.CONFIG_DIR / "ranking.yaml")
        casos = carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras_bico)
        regras = carregar_regras_fusao(settings.builder_config_dir / "regras_fusao.yaml", voc)
        fichas = revisao.reunir_fichas(get_db(), regras)
    except (VocabularioInvalido, RankingInvalido, CasosInvalidos, FileNotFoundError) as e:
        falhar(str(e), 2)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")
    observacoes = [
        revisao.Observacao(c.id, c.esperada, c.atributos, c.autoria != "llm", c.descricao)
        for c in casos
        if c.atributos.informados()
    ]
    for arquivo in sorted((settings.REPORTS_DIR / "sessoes").glob("*.json")):
        texto = arquivo.read_text(encoding="utf-8")
        try:
            atributos = validar_atributos(
                json.loads(texto).get("atributos") or {}, voc, regras_bico
            )
        except ValidationError:
            continue  # sessão antiga, com atributo que saiu do vocabulário
        if obs := revisao.sessao_como_observacoes(texto, atributos):
            observacoes.append(obs)
    linhas = revisao.montar_linhas(fichas, observacoes, ranking, regras_bico, regras)
    destino = revisao.gravar_csv(linhas, saida or settings.AVALIACAO_DIR / "revisao_fichas.csv")
    for linha in revisao.resumo(linhas):
        typer.echo(linha)
    typer.echo(f"arquivo: {destino}")


@app.command(name="sessoes")
def relatorio_sessoes() -> None:
    """Relatório das sessões moderadas: acerto, apresentação, posição e perguntas."""
    settings = get_settings()
    estados = carregar_moderadas(settings.REPORTS_DIR / "sessoes")
    resumos = [resumir(e) for e in estados]
    for linha in linhas_sessoes(resumos):
        typer.echo(linha)
    if estados:
        previstas: list[str] = []
        csv_aves = settings.AVALIACAO_DIR / "especies_sessoes.csv"
        if csv_aves.exists():
            from avesrag_assistant.web.servidor import carregar_aves

            previstas = list(carregar_aves(csv_aves))
        typer.echo("")
        for linha in linhas_do_teste_sessoes(
            resumos, especies_de_calibracao(settings.AVALIACAO_DIR / "casos.yaml"), previstas
        ):
            typer.echo(linha)
        caminho = gravar_tsv_sessoes(estados, settings.REPORTS_DIR / ARQUIVO_TSV)
        typer.echo(f"tabela: {caminho}")


@app.command(name="beta")
def relatorio_beta(
    origem: str = typer.Option(
        "mongo",
        "--origem",
        help="Onde ler as conversas: mongo (collection conversations) ou arquivo (reports/sessoes).",
    ),
    excluir: list[str] = typer.Option(
        [],
        "--excluir",
        help="Nome de um convite a deixar de fora, como o seu nos testes (pode repetir).",
    ),
    arquivar: bool = typer.Option(
        True,
        "--arquivar/--sem-arquivar",
        help="Guarda o resumo sem texto livre em reports/beta/ antes de o TTL apagar a conversa.",
    ),
    minimo_conversas: int = typer.Option(
        beta_conversas.MINIMO_CONVERSAS, "--minimo-conversas", min=1, help="Critério do marco 2."
    ),
    minimo_pessoas: int = typer.Option(
        beta_conversas.MINIMO_PESSOAS, "--minimo-pessoas", min=1, help="Critério do marco 2."
    ),
) -> None:
    """Relatório do beta fechado: conversas, confirmações e votos por convite (AS6)."""
    from datetime import timedelta

    from avesrag_assistant.api.config import ConfigInvalida, carregar_api
    from avesrag_assistant.api.fabrica import SemConvites, convites_do_ambiente
    from avesrag_assistant.api.sessoes import RepositorioArquivo, RepositorioMongo
    from avesrag_assistant.conversa.estado import agora

    if origem not in ("mongo", "arquivo"):
        falhar("--origem deve ser mongo ou arquivo", 2)
    settings = get_settings()
    pasta = settings.REPORTS_DIR / "beta"
    try:
        config = carregar_api(settings.CONFIG_DIR / "api.yaml")
        if origem == "mongo":
            repositorio = RepositorioMongo(get_db(), config.sessoes.ttl_dias)
        else:
            repositorio = RepositorioArquivo(settings.REPORTS_DIR / "sessoes")
        lidas = repositorio.todas()
    except ConfigInvalida as e:
        falhar(str(e), 2)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")

    estados = [e for e in lidas if not e.ave]  # as moderadas têm o próprio relatório (`sessoes`)
    sal = beta_conversas.carregar_sal(pasta)
    vivos = [beta_conversas.anonimizar(beta_conversas.resumir(e), sal) for e in estados]
    guardados = beta_conversas.ler_arquivo(pasta / beta_conversas.ARQUIVO_RESUMOS)
    todos = beta_conversas.mesclar(guardados, vivos)
    novas = len(set(todos) - set(guardados))

    try:
        nomes = {
            beta_conversas.pseudonimo(nome, sal): nome
            for nome, _, _ in convites_do_ambiente(settings).listar()
        }
    except SemConvites:
        nomes = {}
    fora = {beta_conversas.pseudonimo(nome, sal) for nome in excluir}
    resumos = [r for r in todos.values() if r.convite not in fora]
    resumos.sort(key=lambda r: (r.criada_em, r.sessao))

    typer.echo(
        f"lidas {len(lidas)} conversa(s) de {origem}; {len(lidas) - len(estados)} da moderação "
        f"ignorada(s); {len(todos) - len(resumos)} excluída(s) por convite; "
        f"{len(guardados) - len(set(guardados) & {r.sessao for r in vivos})} só no arquivo"
    )
    typer.echo("")
    for linha in beta_conversas.linhas(resumos, nomes, minimo_conversas, minimo_pessoas):
        typer.echo(linha)

    typer.echo("")
    if arquivar:
        caminho = beta_conversas.gravar_arquivo(pasta / beta_conversas.ARQUIVO_RESUMOS, todos)
        beta_conversas.gravar_tsv(pasta / beta_conversas.ARQUIVO_TSV, todos)
        typer.echo(
            f"arquivo: {caminho} ({len(todos)} conversa(s), {novas} nova(s); "
            f"tabela em {beta_conversas.ARQUIVO_TSV})"
        )
    if origem == "mongo" and estados:
        mais_antiga = min(beta_conversas.como_utc(e.atualizada_em) for e in estados)
        expira = mais_antiga + timedelta(days=config.sessoes.ttl_dias)
        dias = (expira - agora()).days
        aviso = "" if arquivar or dias > 3 else " e NÃO foram arquivadas: rode sem --sem-arquivar"
        typer.echo(
            f"a conversa mais antiga no banco expira em {dias} dia(s){aviso}; "
            f"rode este comando de novo antes disso"
        )


@app.command(name="sortear-aves")
def sortear_aves(
    semente: int = typer.Option(20261124, "--semente", help="Semente do sorteio."),
    comum: int = typer.Option(8, "--comum", min=0, help="Espécies da faixa comum."),
    intermediaria: int = typer.Option(7, "--intermediaria", min=0, help="Da faixa intermediária."),
    pouco: int = typer.Option(5, "--pouco", min=0, help="Da faixa pouco conhecida."),
    max_familia: int = typer.Option(2, "--max-familia", min=1, help="Máximo por família."),
    saida: Path | None = typer.Option(
        None, "--saida", help="CSV de saída (padrão: avaliacao/especies_sessoes.csv)."
    ),
    refazer: bool = typer.Option(
        False, "--refazer", help="Sobrescreve o CSV existente (perde a revisão das fotos)."
    ),
) -> None:
    """Sorteia as aves e as fotos das sessões moderadas, fora da calibração."""
    from avesrag_assistant.avaliacao import sorteio

    settings = get_settings()
    destino = saida or settings.AVALIACAO_DIR / "especies_sessoes.csv"
    if destino.exists() and not refazer:
        falhar(
            f"{destino} já existe (com a revisão das fotos); use --refazer para sortear de novo", 2
        )
    try:
        voc, regras = vocabulario.carregar()
        casos = carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras)
    except (VocabularioInvalido, CasosInvalidos) as e:
        falhar(str(e), 2)
    calibracao = {c.esperada for c in casos if c.uso == "calibracao"}
    try:
        especies = sorteio.carregar_especies(get_db(), calibracao)
    except PyMongoError as e:
        falhar(f"falha no MongoDB ({settings.MONGODB_URI}): {e}")
    sorteio.tercis(especies)
    por_faixa = {"comum": comum, "intermediaria": intermediaria, "pouco_conhecida": pouco}
    escolhidas = sorteio.sortear(especies, por_faixa, semente, max_familia)
    caminho = sorteio.gravar_csv(escolhidas, destino)
    typer.echo(
        f"{len(especies)} espécies elegíveis (fora das {len(calibracao)} da calibração) · "
        f"semente {semente} · {len(escolhidas)} sorteadas"
    )
    for i, e in enumerate(escolhidas, start=1):
        typer.echo(f"S{i:<3} {e.faixa:<16} {e.fotos_wikiaves:>6}  {e.nome} ({e.nome_cientifico})")
    typer.echo(f"lista: {caminho}")
