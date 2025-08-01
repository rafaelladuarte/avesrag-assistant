from script.database.minsearch import Index
import json


def load_index():
    with open("script/data/parameterized_dataset_udi.json") as file:
        documents = json.load(file)

    index = Index(
        text_fields=["caracteristicas", "alimentacao", "habitos"],
        keyword_fields=["cores", "tamanho", "dieta_principal", "habitat"]
    )

    index.fit(documents)

    return index
