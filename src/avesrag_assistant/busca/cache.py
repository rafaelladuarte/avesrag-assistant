"""Cache dos embeddings das consultas (plano_assistant1.md §4, critério R6).

A chave é `modelo:sha256(texto)`: a segunda avaliação do mesmo conjunto não
chama a API. O arquivo fica em `reports/`, fora do git, e é gravado de forma
atômica depois de cada chamada ao fornecedor.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from avesrag_builder.llm.base import Embedder

from avesrag_assistant.settings import Settings

ARQUIVO_PADRAO = "cache_embeddings.json"


class CacheEmbeddings:
    def __init__(self, path: Path, embedder: Embedder, modelo: str):
        self.path = Path(path)
        self.embedder = embedder
        self.modelo = modelo
        self.chamadas = 0  # chamadas ao fornecedor neste processo
        self.tokens = 0
        self._dados: dict[str, list[float]] | None = None

    def _chave(self, texto: str) -> str:
        return f"{self.modelo}:{hashlib.sha256(texto.strip().encode('utf-8')).hexdigest()}"

    def _carregar(self) -> dict[str, list[float]]:
        if self._dados is None:
            if self.path.exists():
                self._dados = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self._dados = {}
        return self._dados

    def _gravar(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._dados), encoding="utf-8")
        tmp.replace(self.path)

    def vetores(self, textos: list[str]) -> list[list[float]]:
        """Um vetor por texto, na mesma ordem; só os ausentes vão ao fornecedor, numa chamada."""
        dados = self._carregar()
        chaves = [self._chave(t) for t in textos]
        faltando = list(dict.fromkeys(c for c in chaves if c not in dados))
        if faltando:
            texto_de = {c: t.strip() for c, t in zip(chaves, textos, strict=True)}
            resposta = self.embedder.embeddings([texto_de[c] for c in faltando], self.modelo)
            self.chamadas += 1
            self.tokens += resposta.tokens
            dados.update(zip(faltando, resposta.vetores, strict=True))
            self._gravar()
        return [dados[c] for c in chaves]

    def vetor(self, texto: str) -> list[float]:
        return self.vetores([texto])[0]


def criar_cache(settings: Settings) -> CacheEmbeddings:
    """Cache com o fornecedor da OpenAI do builder; exige OPENAI_API_KEY."""
    from avesrag_builder.llm.openai import OpenAIFornecedor

    settings.require("OPENAI_API_KEY")
    embedder = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    return CacheEmbeddings(
        settings.REPORTS_DIR / ARQUIVO_PADRAO, embedder, settings.EMBEDDING_MODEL
    )
