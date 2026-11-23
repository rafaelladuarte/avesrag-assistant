# AvesRAG Assistant

![cover](docs/images/avesrag.jpg)

Assistente que identifica aves do Cerrado a partir de uma descrição em texto ("do tamanho de um pardal, barriga amarela, uma faixa branca em cima do olho"). Ele conversa com a pessoa até ter informação suficiente, busca as espécies candidatas na base montada pelo [avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder), cruza com as observações recentes do eBird na região e apresenta 3 candidatas para confirmar.

> **Esta é a v2, em construção.** A v1 (Streamlit + Groq + MinSearch + PostgreSQL, feita para o LLM Zoomcamp) está no último commit da `main` antes da reescrita: [`0cb81ef`](https://github.com/rafaelladuarte/avesrag-assistant/tree/0cb81ef).

## Estado

| Marco | Entrega | Situação |
| --- | --- | --- |
| Assistant 1 | retriever (busca vetorial + reordenação por atributos) e conjunto de avaliação | em andamento: estrutura do repositório |
| Assistant 2 | extrator de atributos, perguntas e orquestrador, pela linha de comando | — |
| Assistant 3 | geocodificação, eBird como bônus no ranking e redator da resposta | — |
| Assistant 4 | API FastAPI, guardrails e sessões | — |
| Assistant 5 | página web | — |

## Como funciona

Um orquestrador determinístico (máquina de estados) conduz a conversa. O LLM aparece só em três pontos, sempre com saída estruturada e validada: extrair os atributos da descrição, perguntar o que falta e redigir a resposta. A busca usa o `$vectorSearch` do MongoDB e depois reordena as candidatas pelos atributos observados: o que bate na ficha da espécie ganha pontos, o que conflita perde, e o que a ficha não cita é neutro.

## Requisitos

- Python 3.13 e [Poetry](https://python-poetry.org/) 2.
- O [avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder) clonado **ao lado** deste repositório, com o MongoDB de pé e a base do piloto montada (veja o README dele). O assistant usa o vocabulário e as regras de bico do builder e consulta o mesmo banco.

```
pasta/
├── avesrag-dataset-builder/
└── avesrag-assistant/
```

- Uma chave da OpenAI, para os embeddings das consultas (`text-embedding-3-large`).

## Instalação

```bash
cd avesrag-assistant
poetry install
cp .env.example .env    # preencha OPENAI_API_KEY e a MONGODB_URI do builder
```

## Comandos

```bash
poetry run avesrag-assistant --help
poetry run avesrag-assistant buscar "barriga amarela e sobrancelha branca" \
    --atributos '{"cores_por_parte": [{"parte": "barriga", "cor": "amarelo"}], "marcas": ["sobrancelha"]}'
poetry run avesrag-assistant avaliar                 # o conjunto de avaliação, nos 3 modos de busca
poetry run avesrag-assistant avaliar --uso teste     # só os casos de teste
```

`buscar` e `avaliar` ainda não estão implementados.

## Configuração

| Arquivo | O que define |
| --- | --- |
| `config/ranking.yaml` | pesos da reordenação por atributo e parâmetros da busca vetorial |
| `avaliacao/casos.yaml` | conjunto de avaliação: descrições escritas por pessoas, a espécie esperada e os atributos anotados |
| `.env` | `OPENAI_API_KEY`, `MONGODB_URI`, `MONGODB_DATABASE`, `EMBEDDING_MODEL` e, se o builder não estiver na pasta irmã, `BUILDER_DIR` |

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
