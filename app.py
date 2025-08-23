import streamlit as st
import pandas as pd
import os

from script.api.groq import GroqAPI
from script.infra.security import get_secret_value
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


def feedback(feedback, especie, user_input):
    feedback_data = {
        "especie": especie['taxonomia'],
        "nome_comum": especie['nome_pt'],
        "info_corretas": feedback["info_corretas"],
        "especie_correta": feedback["especie_correta"],
        "observacao": feedback["observacao"].strip() if feedback.get("observacao") else "Nenhuma observação fornecida",
        "user_input": {
            "tamanho": user_input.get("tamanho", ""),
            "cor_principal": user_input.get("cor_principal", []),
            "tipo_bico": user_input.get("bico", ""),
            "habitat": user_input.get("habitat", []),
            "dieta": user_input.get("dieta", []),
            "horario": user_input.get("horario", []),
            "descricao": user_input.get("descricao", "")
        }
    }
    st.success("Feedback enviado com sucesso. Muito obrigada!")
    # st.json(feedback_data)

    df = pd.DataFrame([{
        **{k: v for k, v in feedback_data.items() if k != "user_input"},
        "user_input": str(feedback_data["user_input"])
    }])
    file_path = "feedback_aves.csv"
    if os.path.exists(file_path):
        df.to_csv(file_path, mode='a', header=False, index=False)
    else:
        df.to_csv(file_path, index=False)

    return feedback_data


def show_result(result, user_input=None):
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
                f"- **Habitat**: {especie['habitat']}\n"
            )

            with st.expander("Feedback sobre a identificação"):
                st.markdown("*Preencha o formulário abaixo apenas se desejar fornecer feedback sobre esta identificação.*")
                with st.form(f"validacao_form_{i}"):
                    info_corretas = st.radio(
                        "As informações fornecidas sobre a espécie estão corretas?",
                        ["Sim", "Não"],
                        key=f"info_corretas_{i}"
                    )
                    especie_correta = st.radio(
                        "Essa é a espécie que você estava buscando?",
                        ["Sim", "Não"],
                        key=f"especie_correta_{i}"
                    )
                    observacao = st.text_area(
                        "Observações adicionais (opcional):",
                        key=f"observacao_{i}",
                        placeholder="Ex.: A ave que observei estava em outro ambiente, ou a espécie foi exibida com cores incorretas."
                    )
                    submit_feedback = st.form_submit_button("Enviar Feedback")

                    if submit_feedback:
                        if info_corretas is None or especie_correta is None:
                            st.warning("Por favor, preencha ao menos uma opção antes de enviar.")
                        else:
                            feedback_data = {
                                "info_corretas": info_corretas,
                                "especie_correta": especie_correta,
                                "observacao": observacao
                            }
                            feedback(
                                feedback_data,
                                especie,
                                st.session_state.get("user_input", {})
                            )

            st.markdown("---")
        except KeyError as e:
            st.error(f"Erro nos dados da espécie {i+1}: {str(e)}")


st.title("🦜 AvesRAG – Assistente de Identificação de Aves do Cerrado")
st.subheader("Descreva o que você viu e descubra qual ave pode ter sido.")


with st.expander("🔐 Configurações avançadas"):
    # api_key = st.text_input("Chave da API da LLM", type="password")
    # api_key = st.secrets.get("GROQ_API_KEY", None)
    llm = st.checkbox(
        "Habilitar identificação por IA",
        value=True
    )


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
    api_key = None
    gq = None
    if llm:
        try:
            api_key = get_secret_value("GROQ_API_KEY")
            gq = GroqAPI(api_key)
        except Exception as e:
            st.error(f"Erro ao inicializar a API: {str(e)}")

    if api_key and not descricao.strip():
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
        result = get_result(index, llm, descricao, filter, gq)
        st.session_state["results"] = result
        show_result(result, st.session_state.user_input)

if "results" in st.session_state and st.session_state.results:
    st.markdown("## 🐦 Espécies sugeridas:")
    show_result(st.session_state.results)
