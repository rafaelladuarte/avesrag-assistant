# AvesRAG Assistant

![cover](docs/images/avesrag.jpg)

Assistente que identifica aves do Cerrado a partir de uma descrição em texto ("do tamanho de um pardal, barriga amarela, uma faixa branca em cima do olho"). Ele conversa com a pessoa até ter informação suficiente, busca as espécies candidatas na base montada pelo [avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder) e apresenta 3 candidatas para confirmar. O cruzamento com as observações recentes do eBird na região vem no Assistant 3.

> **Esta é a v2, em construção.** A v1 (Streamlit + Groq + MinSearch + PostgreSQL, feita para o LLM Zoomcamp) está no último commit da `main` antes da reescrita: [`0cb81ef`](https://github.com/rafaelladuarte/avesrag-assistant/tree/0cb81ef).

## Estado

| Marco | Entrega | Situação |
| --- | --- | --- |
| Assistant 1 | retriever (busca vetorial + reordenação por atributos) e conjunto de avaliação | concluído |
| Assistant 2 | extrator de atributos, sugestões de espécies, perguntas e orquestrador, pela linha de comando; página local para sessões moderadas | implementado; falta o teste com pessoas |
| Assistant 3 | geocodificação, eBird como bônus no ranking e redator da resposta | — |
| Assistant 4 | API FastAPI, guardrails e sessões | — |
| Assistant 5 | página web | — |

## Como funciona

Um orquestrador determinístico (máquina de estados) conduz a conversa. O LLM aparece só em quatro pontos, sempre com saída JSON validada por Pydantic e sem decidir o fluxo:

1. **extrair** os atributos de cada mensagem, no vocabulário da base (`c01_extrator`);
2. **sugerir** espécies para a descrição (`c04_candidatas`): os nomes são conferidos contra as fichas, e as que existem ganham um bônus no ranking, nunca um filtro;
3. **redigir** a pergunta que o código escolheu (`c02_pergunta`);
4. **redigir** a resposta final (`c03_redator`, Assistant 3).

A busca usa o `$vectorSearch` do MongoDB e reordena as candidatas pelos atributos observados: o que bate na ficha da espécie ganha pontos, o que conflita perde, e o que a ficha não cita é neutro. Depois de cada mensagem, a busca roda de novo, e o código pergunta o atributo que mais separa as 10 candidatas da vez (até 3 perguntas). Então mostra as 3 primeiras; num "não", elas saem e a conversa segue, até 2 rodadas.

## Requisitos

- Python 3.13 e [Poetry](https://python-poetry.org/) 2.
- O [avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder) clonado **ao lado** deste repositório, com o MongoDB de pé e a base do piloto montada (veja o README dele). O assistant usa o vocabulário e as regras de bico do builder e consulta o mesmo banco.

```
pasta/
├── avesrag-dataset-builder/
└── avesrag-assistant/
```

- Uma chave da OpenAI, para os embeddings das consultas (`text-embedding-3-large`) e o LLM do chat (`gpt-6-luna`).

## Instalação

```bash
cd avesrag-assistant
poetry install
cp .env.example .env    # preencha OPENAI_API_KEY e a MONGODB_URI do builder
```

## Comandos

```bash
poetry run avesrag-assistant --help

# Busca
poetry run avesrag-assistant buscar "barriga amarela e sobrancelha branca" \
    --atributos '{"cores_por_parte": [{"parte": "barriga", "cor": "amarelo"}], "marcas": ["sobrancelha"]}'
poetry run avesrag-assistant buscar "..." --modo hibrido --sugestoes   # o modo da conversa
poetry run avesrag-assistant buscar "bem-te-vi" --modo degradado        # por palavras, sem embedding

# Conversa
poetry run avesrag-assistant extrair "ave pequena, barriga amarela"     # os atributos de uma mensagem
poetry run avesrag-assistant conversar                                  # no terminal; --sessao <id> retoma

# Avaliação
poetry run avesrag-assistant avaliar --uso calibracao --modo hibrido --sugestoes
poetry run avesrag-assistant simular --uso calibracao --sugestoes --calibrado   # perguntas, usuário simulado

# Sessões moderadas (teste com pessoas)
poetry run avesrag-assistant moderar        # página em http://127.0.0.1:8765/
poetry run avesrag-assistant sessoes        # resultado das sessões
poetry run avesrag-assistant sortear-aves   # as aves e as fotos das sessões (já sorteadas)
```

- `buscar` mostra as candidatas com a nota final, a nota da busca, os pontos, o bônus de sugestão e o que bateu, conflitou ou falta na ficha. Modos: `vetor`, `filtros_duros`, `reordenado` (o padrão), `hibrido` e `degradado`.
- `avaliar` mostra top 1, top 3, top 10 e a posição mediana da espécie esperada por modo e grava o detalhe por caso em `reports/avaliacao.tsv` (use `--tsv` nas execuções parciais).
- `simular` mede as perguntas com um usuário que responde pela ficha da espécie certa, com ou sem erros na proporção medida nos formulários.
- `conversar` e `moderar` guardam cada sessão em `reports/sessoes/<id>.json`; as sessões moderadas também em `reports/sessoes_moderadas.tsv`.
- Os embeddings e as respostas do LLM ficam em cache (`reports/cache_embeddings.json`, `reports/cache_llm.json`): rodar de novo não chama a API.

## Resultados (calibração, 24/11)

Top 3 das 50 descrições escritas por pessoas (formulários), com a espécie certa entre as 3 primeiras:

| Etapa | Top 3 |
| --- | --- |
| busca com os atributos anotados | 10% |
| + sugestões de espécies pelo LLM | 44% |
| + 3 perguntas, usuário simulado sem erro | 70% |
| + 3 perguntas, usuário simulado com os erros dos formulários | 56% |

Esses casos foram usados para ajustar o ranking e as perguntas; o teste é feito com sessões moderadas, com outras aves. A meta do piloto (a espécie certa entre as 3 primeiras em 70% das descrições) vale para o sistema completo, com o eBird.

## Configuração

| Arquivo | O que define |
| --- | --- |
| `config/ranking.yaml` | pesos da reordenação por atributo, busca vetorial, modo `hibrido` e bônus das sugestões |
| `config/conversa.yaml` | perguntas (quantas, quais atributos, como pedir), regra para mostrar as candidatas, rodadas de confirmação e o usuário simulado |
| `config/prompts/` | prompts do chat: `c01_extrator`, `c02_pergunta`, `c04_candidatas` |
| `avaliacao/casos.yaml` | conjunto de avaliação: as descrições, a espécie esperada, os atributos anotados (o gabarito do extrator), a origem e o uso (`calibracao` ou `teste`) |
| `avaliacao/especies_sessoes.csv` | as 20 aves e fotos das sessões moderadas, fora da calibração |
| `.env` | `OPENAI_API_KEY`, `MONGODB_URI`, `MONGODB_DATABASE`, `EMBEDDING_MODEL`, `LLM_MODEL` e, se o builder não estiver na pasta irmã, `BUILDER_DIR` |

## Desenvolvimento

```bash
poetry run pytest                    # unitários: sem .env, sem rede, sem MongoDB
poetry run pytest -m integration     # precisa do MongoDB do builder de pé
poetry run ruff check src tests && poetry run ruff format --check src tests
poetry run pre-commit install
```

O código, as mensagens e os testes são escritos em português.

## Licença

MIT. Veja [LICENSE](LICENSE).
