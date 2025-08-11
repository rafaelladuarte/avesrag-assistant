import json
import streamlit as st


def search_docs(query, filter, index):
    boost = {
        "resumo_llm": 0.8,
        "cores": 2.0,
        "dieta_principal": 0.5,
        "tipo_bico": 0.5
    }

    results = index.search(
        query=query,
        filter_dict=filter,
        boost_dict=boost,
        num_results=5
    )

    return results


def build_prompt(query, search_results):

    json_example = {
        "taxonomias": [
            "taxonomia da especie 1",
            "taxonomia da especie 2",
            "taxonomia da especie 3"
        ]
    }

    prompt_template = """
    Você é um assistente ornitólogo especializado em aves brasileiras.
    Sua tarefa é identificar até 3 espécies de aves que correspondam à
    descrição fornecida na seção QUESTÃO. Use somente as informações
    disponíveis no CONTEXTO extraído da base de dados. Não adicione
    informações externas.

    QUESTÃO:
    {question}

    CONTEXTO:
    {context}

    Instruções de saída:
    Retorne exatamente uma lista com até 3 espécies de aves no formato JSON
    mostrado abaixo. As espécies devem estar ordenadas da mais para a menos
    semelhante ao contexto fornecido.

    A resposta deve conter apenas o JSON, sem comentários, títulos ou qualquer
    outro texto adicional.

    Exemplo exato do formato esperado:
    {json_example}
    """.strip()

    context = []

    for doc in search_results:
        context.append(
            {
                "taxonomia": doc['taxonomia'],
                "nome_popular": doc['nome_pt'],
                "caracteristicas": doc['caracteristicas'],
                "alimentacao": doc['alimentacao'],
                "habitos": doc['habitos']
            }
        )

    prompt = prompt_template.format(
        question=query,
        context=json.dumps(context),
        json_example=json.dumps(json_example)
    )
    return prompt


def filter_result(answer, search_results):
    mapa_taxonomia = {s["taxonomia"]: s for s in search_results}

    answer = json.loads(answer).get("taxonomias")

    return [
        {
            "taxonomia": s["taxonomia"],
            "nome_pt": s["nome_pt"],
            "url_wikiaves": s["url_wikiaves"],
            "resumo_llm": s["resumo_llm"],
            "url_image": s["url_image"]
        }
        for a in answer
        if (s := mapa_taxonomia.get(a))
    ]


def rag(index,  groq, query, filter):
    search_results = search_docs(query, filter, index)
    prompt = build_prompt(query, search_results)
    answer = groq.send_prompt(prompt)
    result = filter_result(answer, search_results)
    return result


def get_result(index,  llm, query, filter, groq=None):
    try:
        if llm and groq:
            result = rag(index=index, groq=groq, query=query, filter=filter)
            if isinstance(result, str):
                result = json.loads(result)
        else:
            result = search_docs(index=index, query='', filter=filter)
        return result[:3]
    except Exception as e:
        st.error(f"Erro ao processar a busca: {str(e)}")
        return []
