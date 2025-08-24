import streamlit as st

from script.utils.operation import normalize_text
from script.utils.rag import get_result
from script.service.result import show_result


def form_indetify():
    with st.form("form_identificacao"):
        st.session_state["llm"] = st.checkbox(
            "Habilitar identificação por IA",
            value=True,
            default=False,
            help="Ative esta opção para permitir que o sistema utilize inteligência artificial para ajudar na identificação"
        )
        st.markdown("### 📋 Descreva a ave que você viu:")
        col1, col2 = st.columns(2)
        with col1:
            tamanho = st.selectbox(
                "Tamanho da ave",
                ["Pequena", "Média", "Grande"],
                help="Tamanho aproximado da ave em relação a um pombo.",
            )
            cor_principal = st.multiselect(
                "Cor(es) principal(is)",
                [
                    "Branco", "Preto", "Marrom", "Amarelo",
                    "Azul", "Verde", "Vermelho", "Cinza", "Laranja", "Roxo"
                ],
                help="Selecione até 3 cores.",
            )
            bico = st.selectbox(
                "Tipo de bico",
                [
                    "Generalista", "Sondador", "Filtrador", "Frugívoro",
                    "Granívoro", "Insetívoro", "Nectarívoro", "Insetos em troncos",
                    "Limícola", "Pescador", "Carniceiro", "Raptorial",
                    "Rede de Pescas", "Sementes Duras"
                ],
                help="Selecione o tipo de bico que mais se assemelha ao observado.",
            )
        with col2:
            habitat = st.multiselect(
                "Habitat observado",
                [
                    "Floresta", "Campo", "Urbano", "Mata Ciliar", "Plantação",
                    "Brejo", "Mata seca", "Cerrado", "Caatinga", "Buriti",
                    "Mangue", "Rio", "Lago"
                ],
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
                help="Selecione até 3 tipos de alimento.",
            )
            horario = st.multiselect(
                "Horário da observação",
                ["", "Diurna", "Noturna", "Crepuscular"],
                help="Selecione o período do dia em que a ave foi observada.",
            )
        descricao = st.text_area(
            "🗣️ Descrição livre: (Campo obrigatório para identificação por IA.)"
        )
        st.session_state["submitted"] = st.form_submit_button("🔍 Identificar")

    return tamanho, cor_principal, bico, habitat, dieta, horario, descricao


def submit_identify(
    gq,
    index,
    tamanho,
    cor_principal,
    bico,
    habitat,
    dieta,
    horario,
    descricao,
    llm
):
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

    if llm and not descricao.strip():
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
        llm = False
        result = get_result(index, llm, descricao, filter, gq)
        st.session_state["results"] = result
        show_result(result)