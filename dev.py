from script.utils.ingest import load_index
from script.infra.security import get_secret_value
from script.utils.rag import rag
from script.api.groq import GroqAPI

import json

gq = GroqAPI(api_key=get_secret_value("GROQ_API_KEY"))

index = load_index()

ft = {
        "cores": ["amarelo", "preto", "branco"],
        "tamanho": "media",
        "habitat": ["urbano"],
        "dieta_principal": ["insetos"],
        "tipo_bico": "generalista"
    }

tamanho = ft.get("tamanho")
cor_principal = ", ".join(ft["cores"]) if ft.get("cores") else None
bico = ft.get("tipo_bico")
habitat = ft.get("habitat")
dieta = ft.get("dieta_principal")
horario = ft.get("horario")

qr = "Ave avistada com "

if tamanho:
    qr += f"tamanho {tamanho} "
if cor_principal:
    qr += f"com cores {cor_principal} "
if bico:
    qr += f"bico do tipo {bico} "
if habitat:
    qr += f"localizado em {habitat} "
if dieta:
    qr += f"se alimentando de {dieta} "
if horario:
    qr += f"visto em atividade no turno {horario} "
qr = qr.strip()

rag_results = rag(
    index=index,
    groq=gq,
    query=qr,
    filter=ft,
)

print(rag_results)

result = json.loads(rag_results)
