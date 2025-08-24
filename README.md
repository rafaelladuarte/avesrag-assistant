# 🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado

![cover](docs/images/avesrag.jpg)

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit\&logoColor=white)](https://streamlit.io/)
[![LLM](https://img.shields.io/badge/LLM-llama--3.1--8b--instant-green)](#)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🏆 Projeto desenvolvido para o curso **LLM Zoomcamp** da [DataTalks.Club](https://datatalks.club)

## 📌 Problema 

Os aplicativos de identificação de aves existentes funcionam, em geral, com fotos ou sons. No entanto, nem sempre o observador consegue registrar uma imagem ou gravação no momento do avistamento.
Nessas situações, a única referência disponível é a descrição visual da ave, como por exemplo: cor, tamanho, formato do bico ou comportamento.
O AvesRAG foi criado para atender exatamente esse cenário, permitindo a identificação de aves a partir de descrições em texto.

## 📌 Sobre o Projeto

O **AvesRAG Assistant** é um assistente inteligente interativo busca resolver esse problema, ele capaz de **identificar aves** com base em descrições fornecidas pelo usuário. Utilizando a técnica de **RAG (Retrieval-Augmented Generation)**, ele busca informações em uma base de dados personalizada e retorna **até 3 espécies candidatas** com descrições resumidas.

---
## 🖼 Prévia da Interface

> *(Adicione aqui um print da aplicação rodando)*

![preview](images/preview.png)

## 🎯 Objetivos

* Criar uma ferramenta interativa para identificação de aves.
* Utilizar RAG para combinar **busca estruturada** e **geração de texto por LLM**.
* Garantir que o backend e o pipeline sejam modulares e fáceis de adaptar.
* Coletar feedback dos usuários para melhorar continuamente os resultados (Em desenvolvimento).

## 📊 Base de Dados

A base de dados utilizada foi criada a partir de:

* Integração de **bases já existentes**.
* **Scraping** de fontes online.
* **Parametrização de dados via LLM**.

📂 Repositório do construtor da base:
➡ [rafaelladuarte/avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder)

## 🧩 Arquitetura do Sistema

```mermaid
flowchart LR
    A[Usuário] -->|Descrição da ave| B[Streamlit UI]
    B --> | Paramêtros | D[MinSearch - Busca Semântica + Vetorial]
    D --> | Documentos Recuperados | E[LLM - Groq API]
    E --> | Documentos Verificados | B
    B --> | Validação do Resultado | F[Feedback - Em desenvolvimento]
    F --> H[PostgresSQL]
    B --> G[Monitoramento- Em desenvolvimento]
    G --> H
    H --> | Relatório com Métricas | B
```

## ✨ Funcionalidades

✅ Entrada de dados via formulário com validação . \
✅ Busca otimizada com MinSearch (semântica + textual). \
✅ Retorno de **até 3 espécies candidatas**. \
✅ Resumo automático das espécies com imagens.\
🔄 Coleta de feedback do usuário\
🔄 Monitoramento do uso da LLM - API.

## 🔬 Avaliação

### 🔎 Retrieval

* **Testes realizados**:

  * BM25 (textual)
  * Vetorial (embeddings)
  * Busca híbrida (melhor resultado)
* **Resultado**: busca híbrida apresentou maior recall e precisão para descrições curtas.

### 🧠 LLM

* Avaliados diferentes modelos open-source.
  * `llama-3.1-8b-instant`
  * `gemma2-9b-it `
  * `deepseek-r1-distill-llama-70b`
* Testados prompts *zero-shot* vs *few-shot*.
* **Resultado**: `llama-3.1-8b-instant` com *few-shot* teve melhor equilíbrio entre custo e precisão.

## 📊 Feedback e Monitoramento (em desenvolvimento)

* Coleta de feedback de usuários (sim/não sobre utilidade da resposta).
* Armazenamento em PostgreSQL
* Dashboard no Streamlit com métricas:

  * Nº de consultas
  * Espécies mais buscadas
  * Taxa de respostas aceitas
  * Tempo médio de resposta

---
## 🛠 Tecnologias Utilizadas

| Categoria                | Ferramentas                                                                                                             |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| **Linguagem**            | Python 3.11+                                                                                                            |
| **Framework Web**        | [Streamlit](https://streamlit.io/)                                                                                      |
| **LLM (Assistente)**     | `llama-3.1-8b-instant`                                                                                                  |
| **LLMs (Base de dados)** | `gemma2-9b-it`, `deepseek-r1-distill-llama-70b`, `llama-3.1-8b-instant` |
| **Backend de Busca**     | [MinSearch](https://github.com/alexeygrigorev/minsearch) *(adaptado)*                                                   |
| **API LLM**              | [Groq API](https://groq.com/)                                                                                           |
| **Processamento**        | pandas, numpy                                                                                                           |
| **Controle de Versão**   | Git + GitHub                                                                                                            |

## 📂 Estrutura do Projeto

```
📦 avesrag-assistant
├── app.py
├── dev.py
├── images/
├── Pipfile
├── Pipfile.lock
├── README.md
├── .gitignore
├── requirements.txt
├── script/
│   ├── api/
│   ├── data/
│   ├── database/
│   ├── infra/
│   ├── notebooks/
│   └── utils/
└── venv
```

## ⚙️ Instalação e Execução

### 1. Clone o repositório

```bash
git clone https://github.com/usuario/avesrag-assistant.git
cd avesrag-assistant
```

### 2. Crie o ambiente virtual e instale dependências

```bash
python -m venv venv
source venv/bin/activate          # Linux/Mac
source venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### 3. Configure variáveis de ambiente

Crie um arquivo `.env` com:

```
GROQ_API_KEY="suachaveaqui"
POSTGRES_URL="suauriaqui"
```

### 4. Execute a aplicação

```bash
streamlit run app.py
```

## 📈 Critérios de Avaliação Atendidos

* [x] Problema descrito claramente
* [x] Knowledge base + LLM no fluxo
* [x] Avaliação de múltiplos retrieval flows
* [x] Avaliação de diferentes prompts/modelos
* [x] Interface em Streamlit
* [ ] Ingestão automatizada via scripts Python
* [ ] Monitoramento com feedback + dashboard
* [ ] Containerização com Docker
* [x] Reprodutibilidade (instruções + requirements)

## 📈 Próximos Passos

* 🔧 Ajustar pesos e parâmetros de busca no MinSearch
* 🐦 Expandir base para mais espécies brasileiras
* 🧪 Criar testes unitários e de integração
* 📊 Adicionar logging e monitoramento de consultas


## 📜 Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.