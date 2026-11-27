"""Teste de fumaça de modelos para o `c01` e o `c04` (27/11/2026, aprovado pela usuária).

Para cada modelo, 3 chamadas com a descrição pública p019 do `casos.yaml` (nenhum dado de
convidado): o `c04` sem temperatura, o `c04` com temperatura 0 e o `c01`. Diz se o modelo aceita o
JSON Schema estrito do projeto e o parâmetro `temperature`, quanto demora e quantos tokens gasta.
Não passa pelo cache do projeto e grava só em `reports/analise_falhas/fumaca_modelos.{json,md}`.

    poetry run python scripts/fumaca_modelos.py gpt-4.1 gpt-4.1-mini
"""

from __future__ import annotations

import json
import sys
import time

from avesrag_builder.llm.base import Pedido
from avesrag_builder.llm.openai import OpenAIFornecedor

from avesrag_assistant import vocabulario
from avesrag_assistant.avaliacao.casos import carregar_casos
from avesrag_assistant.avaliacao.extrator import comparar
from avesrag_assistant.db import get_db
from avesrag_assistant.llm.cache import CacheLLM
from avesrag_assistant.llm.contratos import C04Candidatas
from avesrag_assistant.llm.extrator import Extrator, estado_para_prompt
from avesrag_assistant.llm.prompts import carregar_prompt
from avesrag_assistant.llm.sugestor import Sugestor, casar, indice_da_base
from avesrag_assistant.settings import get_settings

CASO = "p019"  # calibração; o `c04` do gpt-6-luna acerta em 1º (Nyctibius griseus)


class Bloqueado:
    nome = "bloqueado"

    def gerar(self, pedido):
        raise RuntimeError("o cache do projeto não é usado nesta fumaça")


def main(modelos: list[str]) -> None:
    settings = get_settings()
    settings.require("OPENAI_API_KEY")
    voc, regras = vocabulario.carregar()
    caso = next(
        c
        for c in carregar_casos(settings.AVALIACAO_DIR / "casos.yaml", voc, regras)
        if c.id == CASO
    )
    vazio = settings.REPORTS_DIR / "analise_falhas" / "_cache_nao_usado.json"
    cache = CacheLLM(vazio, Bloqueado())
    sug = Sugestor(
        carregar_prompt(settings.CONFIG_DIR / "prompts" / "c04_candidatas.yaml", voc),
        cache,
        voc,
        regras,
        settings.LLM_MODEL,
    )
    ext = Extrator(
        carregar_prompt(settings.CONFIG_DIR / "prompts" / "c01_extrator.yaml", voc),
        cache,
        voc,
        regras,
        settings.LLM_MODEL,
    )
    indice = indice_da_base(get_db())
    entrada_c04 = sug.prompt.render_entrada(mensagem=caso.descricao.strip())
    entrada_c01 = ext.prompt.render_entrada(
        estado=estado_para_prompt(None, None), pergunta="nenhuma", mensagem=caso.descricao.strip()
    )
    fornecedor = OpenAIFornecedor(settings.OPENAI_API_KEY.get_secret_value())
    resultados = []
    for modelo in modelos:
        for nome, contrato, temperatura in (
            ("c04 sem temperatura", "c04", None),
            ("c04 temperatura 0", "c04", 0.0),
            ("c01 sem temperatura", "c01", None),
        ):
            sug_ou_ext = sug if contrato == "c04" else ext
            pedido = Pedido(
                id=f"fumaca:{contrato}:{modelo}",
                modelo=modelo,
                system=sug_ou_ext._system,
                entrada=entrada_c04 if contrato == "c04" else entrada_c01,
                schema=sug_ou_ext._schema,
                temperatura=temperatura,
            )
            t0 = time.perf_counter()
            r = fornecedor.gerar(pedido)
            seg = time.perf_counter() - t0
            linha = {
                "modelo": modelo,
                "chamada": nome,
                "segundos": round(seg, 1),
                "tokens_entrada": r.tokens_entrada,
                "tokens_saida": r.tokens_saida,
            }
            if r.erro or r.conteudo is None:
                linha |= {"status": "erro", "detalhe": (r.erro or "resposta vazia")[:400]}
            else:
                try:
                    if contrato == "c04":
                        sugestao = C04Candidatas.model_validate_json(r.conteudo)
                        ids = [i for i in casar(sugestao, indice) if i]
                        linha |= {
                            "status": "ok",
                            "sugeridas": len(sugestao.especies),
                            "casadas_com_a_base": len(ids),
                            "certa_na_posicao": (ids.index(caso.esperada) + 1)
                            if caso.esperada in ids
                            else None,
                            "nomes": [e.nome_popular for e in sugestao.especies][:6],
                        }
                    else:
                        extracao = ext._validar(r.conteudo)
                        total = comparar([caso], {caso.id: extracao.atributos})["total"]
                        linha |= {
                            "status": "ok",
                            "precisao": total.precisao,
                            "cobertura": total.cobertura,
                            "fora_de_escopo": extracao.fora_de_escopo,
                        }
                except ValueError as e:
                    linha |= {"status": "invalido", "detalhe": str(e).splitlines()[0][:300]}
            resultados.append(linha)
            print(json.dumps(linha, ensure_ascii=False), flush=True)
    pasta = settings.REPORTS_DIR / "analise_falhas"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "fumaca_modelos.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
