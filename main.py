import streamlit as st

st.set_page_config(
    page_title="AvesRAG – Assistente de Identificação de Aves do Cerrado",
    layout="wide"
)

st.title("🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado")
st.subheader("Descreva o que você viu e descubra qual ave pode ter sido.")

# Campo opcional para API Key
with st.expander("🔐 Configurações avançadas"):
    api_key = st.text_input("Chave da API da LLM (opcional)", type="password")

# Formulário principal
with st.form("form_identificacao"):
    st.markdown("### 📋 Descreva a ave que você viu:")

    col1, col2 = st.columns(2)
    with col1:
        tamanho = st.selectbox(
            "Tamanho da ave",
            ["Pequena", "Média", "Grande"]
        )
        cor_principal = st.multiselect(
            "Cor(es) principal(is)",
            [
                "Branco", "Preto", "Marrom",
                "Amarelo", "Azul", "Verde",
                "Vermelho", "Cinza"
            ]
        )
        bico = st.selectbox(
            "Formato do bico",
            [
                "Fino", "Largo", "Achatado",
                "Longo", "Curto", "Médio"
                "Reto", "Curvo"
            ]
        )

    with col2:
        habitat = st.selectbox(
            "Habitat observado",
            [
                "Floresta", "Campo", "Urbano", "Mata Ciliar"
                "Plantação", "Brejo", "Mata seca", "Cerrado",
                "Caatinga", "Buriti", "Mangue", "Rio", "Lago"
            ]
        )
        dieta = st.multiselect(
            "Dieta observada",
            [
                "Insetos", "Frutas", "Sementes",
                "Artrópodes", "Peixes", "Aves"
                "Invertebrados", "Néctar", "Anfíbios",
                "Aranhas", "Largatos", "Reptéis",
                "Grãos", "Mamíferos", "Folhas", "Flores",
            ]
        )
        horario = st.selectbox(
            "Horário da observação",
            ["", "Dia", "Noite", "Crepusculo"]
        )

    descricao = st.text_area("🗣️ Descrição livre (opcional)")
    submitted = st.form_submit_button("🔍 Identificar")

# Validação mínima de 3 campos preenchidos
if submitted:
    campos_preenchidos = sum([
        bool(tamanho),
        bool(cor_principal),
        bool(bico),
        bool(habitat),
        bool(dieta),
        bool(horario),
        bool(descricao.strip())
    ])

    if campos_preenchidos < 3:
        st.warning(
            "Por favor, preencha pelo menos 3 campos para " \
            "realizar a identificação.")
    else:
        st.markdown("## 🐦 Espécies sugeridas:")

        resultados_top3 = [
            {
                "nome_cientifico": "Turdus rufiventris",
                "nome_popular": "Sabiá-laranjeira",
                "imagem_url": "https://s3.amazonaws.com/media.wikiaves.com.br/images/7352/2537311_2852aaa7e3f543e8d4f7af0f5ac15211.jpg",
                "descricao": "Ave de médio porte com peito alaranjado. Muito comum em áreas urbanas e conhecida pelo canto melodioso.",
                "fonte": "https://www.wikiaves.com.br/wiki/sabia-laranjeira"
            },
            {
                "nome_cientifico": "Saltator similis",
                "nome_popular": "Trinca-ferro-verdadeiro",
                "imagem_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/Saltator_similis.jpg/640px-Saltator_similis.jpg",
                "descricao": "Ave de canto forte, corpo cinza com traços verdes e bico grosso. Encontrada em matas e bordas de florestas.",
                "fonte": ""
            },
            {
                "nome_cientifico": "Euphonia violacea",
                "nome_popular": "Gaturamo-verdadeiro",
                "imagem_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/45/Euphonia_violacea.jpg/640px-Euphonia_violacea.jpg",
                "descricao": "Pequena ave colorida, com cabeça azul escura e barriga amarela. Muito ativa em áreas de mata.",
                "fonte": ""
            }
        ]

        for i, especie in enumerate(resultados_top3):
            st.markdown(f"### {i+1}. *{especie['nome_cientifico']}* - {especie['nome_popular']}")
            st.image(especie['imagem_url'], width=300)
            st.markdown(f"**Descrição**: {especie['descricao']}")
            st.markdown("---")
