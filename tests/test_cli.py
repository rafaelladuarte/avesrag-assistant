from typer.testing import CliRunner

from avesrag_assistant import __version__
from avesrag_assistant.cli import app

runner = CliRunner()


def test_help_lista_os_comandos():
    resultado = runner.invoke(app, ["--help"])
    assert resultado.exit_code == 0
    assert "buscar" in resultado.output
    assert "avaliar" in resultado.output


def test_version():
    resultado = runner.invoke(app, ["--version"])
    assert resultado.exit_code == 0
    assert __version__ in resultado.output


def test_comandos_ainda_nao_implementados():
    for args in (["buscar", "barriga amarela"], ["avaliar"]):
        resultado = runner.invoke(app, args)
        assert resultado.exit_code == 2
        assert "ainda não foi implementado" in resultado.output
