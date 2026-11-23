"""CLI `avesrag-assistant`.

No Assistant 1 há dois comandos: `buscar` (uma consulta ao retriever) e
`avaliar` (o conjunto de avaliação nos três modos de busca).
"""

import json

import typer
from avesrag_builder.core.vocabulario import VocabularioInvalido
from avesrag_builder.llm.base import ErroFornecedor
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from avesrag_assistant import __version__, vocabulario
from avesrag_assistant.busca.cache import criar_cache
from avesrag_assistant.busca.degradado import buscar_degradado
from avesrag_assistant.busca.modelos import Candidata, validar_consulta
from avesrag_assistant.busca.pontuacao import RankingInvalido, carregar_ranking
from avesrag_assistant.busca.retriever import MODOS, TOP
from avesrag_assistant.busca.retriever import buscar as buscar_especies
from avesrag_assistant.db import get_db
from avesrag_assistant.settings import MissingSettingError, get_settings

MODO_DEGRADADO = "degradado"

app = typer.Typer(
    name="avesrag-assistant",
    help="Identifica aves do Cerrado a partir de uma descrição em texto.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def nao_implementado(comando: str) -> None:
    typer.echo(f"`{comando}` ainda não foi implementado (plano_assistant1.md).", err=True)
    raise typer.Exit(code=2)


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
            candidatas = buscar_especies(db, consulta, vetor, ranking, regras, modo, k)
    except MissingSettingError as e:
        falhar(str(e), 2)
    except ErroFornecedor as e:
        falhar(f"falha no embedding: {e}")
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
) -> None:
    """Roda o conjunto de avaliação nos modos vetor, filtros_duros e reordenado."""
    nao_implementado("avaliar")
