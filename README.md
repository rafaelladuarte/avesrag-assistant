# 🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit\&logoColor=white)](https://streamlit.io/)
[![LLM](https://img.shields.io/badge/LLM-llama--3.1--8b--instant-green)](#)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🏆 Projeto desenvolvido para o **LLM Zoomcamp** da [DataTalks.Club](https://datatalks.club)

---

## 🖼 Prévia da Interface

> *(Adicione aqui um print da aplicação rodando)*

![preview](docs/interface-preview.png)

---

## 📌 Sobre o Projeto

O **Bird ID Assistant** é um assistente inteligente capaz de **identificar aves** com base em descrições fornecidas pelo usuário.
Utilizando a técnica de **RAG (Retrieval-Augmented Generation)**, ele busca informações em uma base de dados personalizada e retorna **até 3 espécies candidatas** com descrições resumidas.

---

## 🎯 Objetivos

* Criar uma ferramenta interativa para identificação de aves.
* Utilizar RAG para combinar **busca estruturada** e **geração de texto por LLM**.
* Garantir que o backend e o pipeline sejam modulares e fáceis de adaptar.

---

## 📊 Base de Dados

A base de dados utilizada foi criada a partir de:

* Integração de **bases já existentes**.
* **Scraping** de fontes online.
* **Parametrização de dados via LLM**.

📂 Repositório do construtor da base:
➡ [rafaelladuarte/avesrag-dataset-builder](https://github.com/rafaelladuarte/avesrag-dataset-builder)

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

---

## 📂 Estrutura do Projeto

```
📦 avesrag-assistant
(desenvolvimento)
```


## ✨ Funcionalidades

✅ Entrada de dados via formulário com validação \
✅ Busca otimizada com MinSearch \
✅ Retorno de **até 3 espécies candidatas** \
✅ Resumo automático das espécies encontradas com imagens

---

## 📈 Próximos Passos

* 🔧 Ajustar pesos e parâmetros de busca no MinSearch
* 🐦 Expandir base para mais espécies brasileiras
* 🧪 Criar testes unitários e de integração
* 📊 Adicionar logging e monitoramento de consultas

---

## 📜 Licença

Distribuído sob a licença MIT. Veja o arquivo [LICENSE](LICENSE) para mais detalhes.
