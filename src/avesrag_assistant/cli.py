"""CLI `avesrag-assistant`.

No Assistant 1 há dois comandos: `buscar` (uma consulta ao retriever) e
`avaliar` (o conjunto de avaliação nos três modos de busca).
"""

import typer

from avesrag_assistant import __version__

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


@app.command()
def buscar(
    descricao: str = typer.Argument(..., help="Descrição da ave, em texto livre."),
    atributos: str = typer.Option(
        "{}", "--atributos", help="Atributos observados em JSON (AtributosObservados)."
    ),
) -> None:
    """Busca as 10 espécies candidatas para uma descrição."""
    nao_implementado("buscar")


@app.command()
def avaliar(
    uso: str | None = typer.Option(
        None, "--uso", help="Só os casos de um uso: calibracao ou teste."
    ),
) -> None:
    """Roda o conjunto de avaliação nos modos vetor, filtros_duros e reordenado."""
    nao_implementado("avaliar")
