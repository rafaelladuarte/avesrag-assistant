from script.database.minsearch import Index
import json


def load_index():
    with open("app/script/data/oficial_dataset_udi.json") as file:
        documents = json.load(file)

    index = Index(
        text_fields=["resumo_llm"],
        keyword_fields=[
            "cores", "tamanho", "dieta_principal",
            "habitat", "atividade", "tipo_bico", "frequencia_normalizada"
        ]
    )

    index.fit(documents)

    return index
