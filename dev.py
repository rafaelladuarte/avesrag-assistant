from script.utils.ingest import load_index
from script.utils.rag import search_docs
from script.api.groq import GroqAPI

gq = GroqAPI(api_key="")

index = load_index()

search_results = search_docs(
    index=index,
    query="Ave de porte médio territorialista comendo gafanhoto, possui um pequeno topete",
    filter={
        "cores": ["amarelo", "preto", "branco"],
        # "tamanho": "Médio",
        "habitat": "urbano"
    }
)

print(search_results)
