import streamlit as st

from script.api.groq import GroqAPI
from script.database.postgres import PostgreSQL
from script.infra.security import get_secret_value
from script.utils.ingest import load_index
from script.utils.operation import normalize_text
from script.utils.rag import get_result
from script.service.feedback import form_feedback, submit_feedback
from script.service.result import show_result


@st.cache_resource
def get_index():
    return load_index()


index = get_index()
if not index:
    st.error("Erro ao carregar o índice. Tente novamente mais tarde.")
    st.stop()
pg = PostgreSQL(
    uri=get_secret_value("POSTGRES_URI")
)
if not pg.conectar_banco():
    st.error("Erro ao conectar ao banco de dados. Tente novamente mais tarde.")
    st.stop()

list_api_key = [
    get_secret_value("GROQ_API_KEY1"),
    get_secret_value("GROQ_API_KEY2"),
]
if not list_api_key:
    st.error("Erro ao carregar a chave da API. Tente novamente mais tarde.")
    st.stop()

gq = GroqAPI(list_api_key=list_api_key)


st.set_page_config(
    page_title="AvesRAG – Assistente de Identificação de Aves do Cerrado",
    layout="wide"
)

st.title("🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado")
st.subheader("Descreva o que você viu e descubra qual ave pode ter sido.")

with st.form("form_identificacao"):
    st.markdown("### 📋 Descreva a ave que você viu:")
    st.session_state["llm"] = st.checkbox(
            "Habilitar identificação por IA",
            help="Ative esta opção para permitir que o sistema utilize inteligência artificial para ajudar na identificação"
        )
    col1, col2 = st.columns(2)
    with col1:
        tamanho = st.selectbox(
            "Tamanho da ave",
            ["", "Pequena", "Média", "Grande"],
            placeholder="",
            help="Tamanho aproximado da ave em relação a um pombo.",
        )
        cor_principal = st.multiselect(
            "Cor(es) principal(is)",
            [
                "Branco", "Preto", "Marrom", "Amarelo",
                "Azul", "Verde", "Vermelho", "Cinza", "Laranja", "Roxo"
            ],
            placeholder="",
            help="Selecione até 3 cores.",
        )
        bico = st.selectbox(
            "Tipo de bico",
            [
                "", "Generalista", "Sondador", "Filtrador", "Frugívoro",
                "Granívoro", "Insetívoro", "Nectarívoro", "Insetos em troncos",
                "Limícola", "Pescador", "Carniceiro", "Raptorial",
                "Rede de Pescas", "Sementes Duras"
            ],
            placeholder="",
            help="Selecione o tipo de bico que mais se assemelha ao observado."
        )
    with col2:
        habitat = st.multiselect(
            "Habitat observado",
            [
                "Floresta", "Campo", "Urbana", "Mata Ciliar", "Plantação",
                "Brejo", "Mata seca", "Cerrado", "Caatinga", "Buriti",
                "Mangue", "Rio", "Lago"
            ],
            placeholder="",
            help="Selecione até 3 habitats.",
        )
        dieta = st.multiselect(
            "Dieta observada",
            [
                "Insetos", "Frutas", "Sementes",
                "Artrópodes", "Peixes", "Aves",
                "Invertebrados", "Néctar", "Anfíbios",
                "Aranhas", "Largatos", "Reptéis",
                "Grãos", "Mamíferos", "Folhas", "Flores"
            ],
            placeholder="",
            help="Selecione até 3 tipos de alimento.",
        )
        horario = st.multiselect(
            "Horário da observação",
            ["", "Diurna", "Noturna", "Crepuscular"],
            placeholder="",
            help="Selecione o período do dia em que a ave foi observada.",
        )
    descricao = st.text_area(
        "🗣️ Descrição livre: (Campo obrigatório para identificação por IA.)"
    )

    submitted = st.form_submit_button("🔍 Identificar")

if submitted:
    campos_preenchidos = sum(
        [
            bool(tamanho),
            bool(cor_principal),
            bool(bico),
            bool(habitat),
            bool(dieta),
            bool(horario),
            bool(descricao.strip())
        ]
    )

    if st.session_state.llm is True and not descricao.strip():
        st.warning("A descrição é obrigatória para identificação por IA.")
    elif campos_preenchidos <= 2:
        st.warning(
            "Preencha pelo menos 3 campos para realizar a identificação."
        )
    else:
        filter = {
            k: v
            for k, v in {
                "tamanho": normalize_text(tamanho),
                "cores": [normalize_text(c) for c in cor_principal],
                "tipo_bico": normalize_text(bico),
                "habitat": [normalize_text(h) for h in habitat],
                "dieta_principal": [normalize_text(d) for d in dieta],
                "atividade": [normalize_text(h) for h in horario]
            }.items()
            if v
        }

        st.session_state["user_input"] = {
            "tamanho": tamanho,
            "cor_principal": cor_principal,
            "bico": bico,
            "habitat": habitat,
            "dieta": dieta,
            "horario": horario,
            "descricao": descricao.strip()
        }

        st.markdown("## 🐦 Espécies sugeridas:")
        # llm = False
        result = get_result(index, st.session_state.llm, descricao, filter, gq)
        st.session_state["results"] = result
        show_result(result)

if "results" in st.session_state:
    form_feedback()

if "submit_feedback" in st.session_state:
    submit_feedback(
        pg
    )
