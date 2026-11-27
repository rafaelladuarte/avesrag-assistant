"""Monta `deploy/vercel/`: a pasta que a Vercel recebe (`vercel deploy` de dentro dela).

Só o assistant sobe. O builder (ingestão, fusão, extração, scraping) não vai: o assistant usa dele,
como biblioteca, 18 módulos pequenos (modelos, vocabulário, nomes das collections, contratos do LLM
e o fornecedor da OpenAI). Como a Vercel não vê a pasta irmã, o pacote leva só esses módulos
(`MODULOS_DO_BUILDER`, conferidos por um teste) e o `config/` do builder com o vocabulário e as
regras de bico, que são dados compartilhados.

Não leva: `.env`, `reports/`, `avaliacao/`, `config/convites.json` (os convites vão pela variável de
ambiente `CONVITES_JSON`) nem os testes. Uso:

    poetry run python scripts/preparar_vercel.py [--saida deploy/vercel]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BUILDER = RAIZ.parent / "avesrag-dataset-builder"
IGNORAR = shutil.ignore_patterns("__pycache__", "*.pyc", "convites.json", ".env*")

# Os módulos do builder que o assistant importa em tempo de execução (sem a curadoria, que traz o
# `fuse` e só roda localmente). O teste `test_modulos_do_builder_cobrem_o_que_a_api_importa` falha
# se o assistant passar a importar outro.
MODULOS_DO_BUILDER = (
    "__init__",
    "core/__init__",
    "core/logging",
    "core/models",
    "core/settings",
    "core/texto",
    "core/vocabulario",
    "db/__init__",
    "db/busca",
    "db/collections",
    "docs/__init__",
    "docs/indices",
    "docs/montar",
    "extract/__init__",
    "extract/contratos",
    "llm/__init__",
    "llm/base",
    "llm/openai",
)

APP_PY = '''"""Ponto de entrada da Vercel: o app FastAPI do beta (gerado por scripts/preparar_vercel.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from avesrag_assistant.api.fabrica import criar_app_do_ambiente  # noqa: E402

app = criar_app_do_ambiente()
'''

REQUIREMENTS = """fastapi
pymongo
pydantic>=2.13,<3
pydantic-settings>=2.2.1,<3
pyyaml
requests
openai
"""

VERCEL_JSON = """{
  "functions": {
    "app.py": {
      "maxDuration": 90,
      "includeFiles": "{src,config,builder}/**"
    }
  }
}
"""

VERCELIGNORE = """.env*
reports
avaliacao
tests
__pycache__
*.pyc
"""

LEIAME = """# Pacote da Vercel (gerado)

Gerado por `scripts/preparar_vercel.py`: não edite aqui, edite o repositório e gere de novo.

Variáveis de ambiente do projeto na Vercel:

- `OPENAI_API_KEY`: chave da OpenAI
- `MONGODB_URI` e `MONGODB_DATABASE`: o banco com `species` e `species_retrieval_docs`
- `CONVITES_JSON`: o conteúdo de `config/convites.json` (só os hashes dos tokens)
- `ARMAZENAMENTO=mongo` (a Vercel já liga sozinho pela variável `VERCEL`)
"""


def _copiar(origem: Path, destino: Path) -> None:
    if not origem.is_dir():
        raise SystemExit(f"não encontrei {origem}")
    shutil.copytree(origem, destino, ignore=IGNORAR)


def preparar(saida: Path) -> list[str]:
    if saida.exists():
        shutil.rmtree(saida)
    (saida / "src").mkdir(parents=True)
    _copiar(RAIZ / "src" / "avesrag_assistant", saida / "src" / "avesrag_assistant")
    for modulo in MODULOS_DO_BUILDER:
        origem = BUILDER / "src" / "avesrag_builder" / f"{modulo}.py"
        if not origem.exists():
            raise SystemExit(f"não encontrei {origem}")
        destino = saida / "src" / "avesrag_builder" / f"{modulo}.py"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem, destino)
    _copiar(RAIZ / "config", saida / "config")
    for nome in ("vocabulario.yaml", "regras_bico.yaml"):  # só os dados que o assistant lê
        destino = saida / "builder" / "config"
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUILDER / "config" / nome, destino / nome)
    arquivos = {
        "app.py": APP_PY,
        "requirements.txt": REQUIREMENTS,
        "vercel.json": VERCEL_JSON,
        ".vercelignore": VERCELIGNORE,
        "LEIAME.md": LEIAME,
    }
    for nome, conteudo in arquivos.items():
        (saida / nome).write_text(conteudo, encoding="utf-8")
    return sorted(str(p.relative_to(saida)) for p in saida.iterdir())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", type=Path, default=RAIZ / "deploy" / "vercel")
    args = ap.parse_args()
    itens = preparar(args.saida)
    print(f"pacote em {args.saida}: {', '.join(itens)}")


if __name__ == "__main__":
    main()
