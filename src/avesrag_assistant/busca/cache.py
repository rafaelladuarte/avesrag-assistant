"""Cache dos embeddings das consultas (plano_assistant1.md §4, critério R6).

A chave é `modelo:sha256(texto)`: a segunda avaliação do mesmo conjunto não
chama a API. O arquivo fica em `reports/`, fora do git, e é gravado de forma
atômica depois de cada chamada ao fornecedor.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from avesrag_builder.llm.base import Embedder

from avesrag_assistant.armazem import Armazem, ArmazemArquivo
from avesrag_assistant.settings import Settings
from avesrag_assistant.uso import CONTADOR

ARQUIVO_PADRAO = "cache_embeddings.json"
COLECAO_PADRAO = "cache_embeddings"


class CacheEmbeddings:
    def __init__(self, onde: Path | str | Armazem, embedder: Embedder, modelo: str):
        """`onde`: um caminho (arquivo JSON) ou um armazém (arquivo ou MongoDB)."""
        self.armazem: Armazem = ArmazemArquivo(onde) if isinstance(onde, (Path, str)) else onde
        self.path = getattr(self.armazem, "path", None)
        self.embedder = embedder
        self.modelo = modelo
        self.chamadas = 0  # chamadas ao fornecedor neste processo
        self.tokens = 0

    def _chave(self, texto: str) -> str:
        return f"{self.modelo}:{hashlib.sha256(texto.strip().encode('utf-8')).hexdigest()}"

    def vetores(self, textos: list[str]) -> list[list[float]]:
        """Um vetor por texto, na mesma ordem; só os ausentes vão ao fornecedor, numa chamada."""
        chaves = [self._chave(t) for t in textos]
        dados = self.armazem.obter(list(dict.fromkeys(chaves)))
        faltando = list(dict.fromkeys(c for c in chaves if c not in dados))
        if faltando:
            texto_de = {c: t.strip() for c, t in zip(chaves, textos, strict=True)}
            resposta = self.embedder.embeddings([texto_de[c] for c in faltando], self.modelo)
            self.chamadas += 1
            CONTADOR.mais_embedding()
            self.tokens += resposta.tokens
            novos = dict(zip(faltando, resposta.vetores, strict=True))
            self.armazem.gravar(novos)
            dados.update(novos)
        return [dados[c] for c in chaves]

    def vetor(self, texto: str) -> list[float]:
        return self.vetores([texto])[0]


def criar_cache(settings: Settings, armazem: Armazem | None = None) -> CacheEmbeddings:
    """Cache com o fornecedor da OpenAI do builder; exige OPENAI_API_KEY."""
    from avesrag_builder.llm.openai import OpenAIFornecedor

    settings.require("OPENAI_API_KEY")
    embedder = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    return CacheEmbeddings(
        armazem or settings.REPORTS_DIR / ARQUIVO_PADRAO, embedder, settings.EMBEDDING_MODEL
    )
