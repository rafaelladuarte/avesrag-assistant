"""Chamadas ao LLM com cache das respostas válidas (plano_assistant2.md §3).

A chave é `prompt:versao:sha256(modelo, system, entrada, schema)`: a mesma
mensagem com o mesmo prompt não chama a API de novo, e mudar a versão do prompt
invalida. Só a resposta que passou na validação é gravada. Se a resposta não
validar, o pedido é refeito uma vez, e depois a falha sobe como `ErroLLM`
(Modelo de Dados, regras comuns, item 3). O arquivo fica em `reports/`, fora
do git, gravado de forma atômica.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from avesrag_builder.llm.base import Fornecedor, Pedido

from avesrag_assistant.armazem import Armazem, ArmazemArquivo
from avesrag_assistant.uso import CONTADOR

ARQUIVO_PADRAO = "cache_llm.json"
COLECAO_PADRAO = "cache_llm"
TENTATIVAS = 2
T = TypeVar("T")


class ErroLLM(RuntimeError):
    """O LLM não devolveu uma resposta válida depois das tentativas."""


class CacheLLM:
    def __init__(self, onde: Path | str | Armazem, fornecedor: Fornecedor):
        """`onde`: um caminho (arquivo JSON) ou um armazém (arquivo ou MongoDB)."""
        self.armazem: Armazem = ArmazemArquivo(onde) if isinstance(onde, (Path, str)) else onde
        self.path = getattr(self.armazem, "path", None)
        self.fornecedor = fornecedor
        self.chamadas = 0  # chamadas ao fornecedor neste processo
        self.tokens_entrada = 0
        self.tokens_saida = 0

    @staticmethod
    def chave(prompt_id: str, versao: str, pedido: Pedido) -> str:
        conteudo = json.dumps(
            [pedido.modelo, pedido.system, pedido.entrada, pedido.schema],
            ensure_ascii=False,
            sort_keys=True,
        )
        return f"{prompt_id}:{versao}:{hashlib.sha256(conteudo.encode('utf-8')).hexdigest()}"

    def gerar(self, prompt_id: str, versao: str, pedido: Pedido, validar: Callable[[str], T]) -> T:
        """A resposta validada: do cache, ou do fornecedor (até 2 tentativas)."""
        chave = self.chave(prompt_id, versao, pedido)
        guardado = self.armazem.obter([chave])
        if chave in guardado:
            return validar(guardado[chave])
        erros = []
        for _ in range(TENTATIVAS):
            resposta = self.fornecedor.gerar(pedido)
            self.chamadas += 1
            CONTADOR.mais_llm()
            self.tokens_entrada += resposta.tokens_entrada
            self.tokens_saida += resposta.tokens_saida
            if resposta.erro is not None or resposta.conteudo is None:
                erros.append(resposta.erro or "resposta vazia")
                continue
            try:
                valor = validar(resposta.conteudo)
            except ValueError as e:  # inclui ValidationError e JSONDecodeError
                erros.append(str(e).splitlines()[0])
                continue
            self.armazem.gravar({chave: resposta.conteudo})
            return valor
        raise ErroLLM(f"{prompt_id}: sem resposta válida em {TENTATIVAS} tentativas: {erros}")
