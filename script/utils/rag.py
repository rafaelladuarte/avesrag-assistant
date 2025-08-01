
def search_docs(query, filter, index):
    boost = {'caracteristicas': 3.0, "dieta_principal": 1.0, "cores": 0.5}

    results = index.search(
        query=query,
        filter_dict=filter,
        boost_dict=boost,
        num_results=5
    )

    return results


def rag(query, groq):
    search_results = search_docs(query)
    prompt = build_prompt(query, search_results)
    answer = groq.send_prompt(prompt)
    return answer


def build_prompt(query, search_results):
    prompt_template = """
    You're a course teaching assistant. Answer the QUESTION based on
    the CONTEXT from the FAQ database.
    Use only the facts from the CONTEXT when answering the QUESTION.

    QUESTION: {question}

    CONTEXT:
    {context}
    """.strip()

    context = ""

    for doc in search_results:
        context = (
            context,
            f"section: {doc['section']}\n"
            f"question:{doc['question']}\n"
            f"answer: {doc['text']}\n\n"
        )

    prompt = prompt_template.format(question=query, context=context).strip()
    return prompt
