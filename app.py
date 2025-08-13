import streamlit as st

from script.api.groq import GroqAPI
from script.utils.ingest import load_index
from script.utils.operation import normalize_text
from script.utils.rag import get_result


try:
    index = load_index()
    if index is None:
        st.error("Erro ao carregar o índice. Tente novamente mais tarde.")
        st.stop()
except Exception as e:
    st.error(f"Erro ao carregar o índice: {str(e)}")
    st.stop()

st.set_page_config(
    page_title="AvesRAG – Assistente de Identificação de Aves do Cerrado",
    layout="wide"
)


def show_result(result):
    if not result:
        st.info("Nenhuma espécie encontrada com os critérios fornecidos.")
        return

    for i, especie in enumerate(result):
        try:

            st.markdown(
                f"### {i+1}. *{especie['taxonomia']}* - {especie['nome_pt']}"
            )
            st.markdown(
                f"**Descrição**: {especie.get('resumo_llm', 'Não disponível')}"
            )
            st.markdown(
                f"**Fonte**: {especie.get('url_wikiaves', 'Não disponível')}"
            )
            st.image(especie['url_image'], width=300)

            st.markdown("**Validação de Identificação**:")
            st.markdown(
                f"- **Cores**: {especie['cores']}\n"
                f"- **Tamanho**: {especie['tamanho']}\n"
                f"- **Tipo de Bico**: {especie['tipo_bico']}\n"
                f"- **Alimentação**: {especie['dieta_principal']}\n"
            )

            st.markdown("---")
        except KeyError as e:
            st.error(f"Erro nos dados da espécie {i+1}: {str(e)}")


st.title("🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado")
st.subheader("Descreva o que você viu e descubra qual ave pode ter sido.")


with st.expander("🔐 Configurações avançadas"):
    api_key = st.text_input("Chave da API da LLM (opcional)", type="password")
    gq = None
    if api_key:
        try:
            gq = GroqAPI(api_key)
        except Exception as e:
            st.error(f"Erro ao inicializar a API: {str(e)}")


with st.form("form_identificacao"):
    st.markdown("### 📋 Descreva a ave que você viu:")
    col1, col2 = st.columns(2)
    with col1:
        tamanho = st.selectbox(
            "Tamanho da ave",
            ["", "Pequena", "Média", "Grande"]
        )
        cor_principal = st.multiselect(
            "Cor(es) principal(is)",
            [
                "Branco", "Preto", "Marrom", "Amarelo",
                "Azul", "Verde", "Vermelho", "Cinza", "Laranja", "Roxo"
            ]
        )
        bico = st.selectbox(
            "Tipo de bico",
            [
                "", "Generalista", "Sondador", "Filtrador", "Frugívoro",
                "Granívoro", "Insetívoro", "Nectarívoro", "Insetos em troncos",
                "Limícola", "Pescador", "Carniceiro", "Raptorial",
                "Rede de Pescas", "Sementes Duras"
            ]
        )
    with col2:
        habitat = st.multiselect(
            "Habitat observado",
            [
                "Floresta", "Campo", "Urbano", "Mata Ciliar", "Plantação",
                "Brejo", "Mata seca", "Cerrado", "Caatinga", "Buriti",
                "Mangue", "Rio", "Lago"
            ]
        )
        dieta = st.multiselect(
            "Dieta observada",
            [
                "Insetos", "Frutas", "Sementes",
                "Artrópodes", "Peixes", "Aves",
                "Invertebrados", "Néctar", "Anfíbios",
                "Aranhas", "Largatos", "Reptéis",
                "Grãos", "Mamíferos", "Folhas", "Flores"
            ]
        )
        horario = st.multiselect(
            "Horário da observação",
            ["", "Diurna", "Noturna", "Crepuscular"]
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
    if api_key and not descricao.strip():
        st.warning("A descrição é obrigatória para identificação por IA.")
    elif campos_preenchidos <= 2:
        st.warning(
            "Preencha pelo menos 3 campos para realizar a identificação."
        )
    else:
        llm = bool(api_key and gq)

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
        st.markdown("## 🐦 Espécies sugeridas:")
        result = get_result(index, llm, descricao, filter, gq)
        show_result(result)
