"""O pacote da Vercel: o que entra e o que não entra (scripts/preparar_vercel.py)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _carregar():
    spec = importlib.util.spec_from_file_location(
        "preparar_vercel", ROOT / "scripts" / "preparar_vercel.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_pacote_leva_o_necessario_e_deixa_o_resto(tmp_path):
    preparar = _carregar()
    saida = tmp_path / "vercel"
    preparar.preparar(saida)
    assert (saida / "app.py").read_text().count("criar_app_do_ambiente") == 2
    assert (saida / "src" / "avesrag_assistant" / "api" / "fabrica.py").exists()
    assert (saida / "src" / "avesrag_builder" / "core" / "vocabulario.py").exists()
    # só a biblioteca mínima do builder, nunca o pipeline
    for pasta in ("fuse", "ingest", "resolve", "validate", "extract/pipeline.py"):
        assert not (saida / "src" / "avesrag_builder" / pasta).exists()
    assert sorted(p.name for p in (saida / "builder" / "config").iterdir()) == [
        "regras_bico.yaml",
        "vocabulario.yaml",
    ]
    assert (saida / "config" / "api.yaml").exists()
    assert (saida / "builder" / "config" / "vocabulario.yaml").exists()
    assert (saida / "web").exists() is False
    assert (saida / "src" / "avesrag_assistant" / "web" / "static" / "app.js").exists()
    # nada de segredo, dado de teste nem convites
    assert not list(saida.rglob("convites.json"))
    assert not list(saida.rglob(".env*")) or all(p.name == ".env*" for p in saida.rglob(".env*"))
    assert not (saida / "reports").exists() and not (saida / "avaliacao").exists()
    requisitos = (saida / "requirements.txt").read_text().split()
    for pacote in ("fastapi", "pymongo", "openai", "requests"):
        assert pacote in requisitos


def test_pacote_acha_o_builder_dentro_dele(tmp_path, monkeypatch):
    preparar = _carregar()
    saida = tmp_path / "vercel"
    preparar.preparar(saida)
    assert (saida / "builder" / "config" / "regras_bico.yaml").exists()


def test_modulos_do_builder_cobrem_o_que_a_api_importa():
    """Em um processo limpo: tudo do builder que a API e os criadores do LLM importam está na lista."""
    import subprocess
    import sys

    codigo = (
        "import sys\n"
        "import avesrag_assistant.api.fabrica, avesrag_assistant.busca.cache\n"
        "import avesrag_assistant.llm.extrator, avesrag_assistant.llm.pergunta\n"
        "import avesrag_assistant.llm.sugestor, avesrag_builder.llm.openai\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('avesrag_builder'))))\n"
    )
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=True)
    carregados = set(r.stdout.split())
    permitidos = {
        "avesrag_builder." + m.removesuffix("/__init__").replace("/", ".")
        if m != "__init__"
        else "avesrag_builder"
        for m in _carregar().MODULOS_DO_BUILDER
    }
    assert carregados <= permitidos, sorted(carregados - permitidos)
