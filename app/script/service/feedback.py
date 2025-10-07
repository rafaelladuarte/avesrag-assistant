import streamlit as st
import json


def process_feedback(pg, feedback, especie, user_input):
    feedback_data = {
        "especie": especie['taxonomia'],
        "nome_comum": especie['nome_pt'],
        "info_corretas": True if feedback["info_corretas"] == "Sim" else False,
        "especie_correta": True if feedback["especie_correta"] == "Sim" else False,
        "observacao": feedback["observacao"].strip() if feedback.get("observacao") else None,
        "user_input": json.dumps(
            {
                "tamanho": user_input.get("tamanho", None),
                "cor_principal": user_input.get("cor_principal", None),
                "tipo_bico": user_input.get("bico", ""),
                "habitat": user_input.get("habitat", None),
                "dieta": user_input.get("dieta", None),
                "horario": user_input.get("horario", None),
                "descricao": user_input.get("descricao", None),
                "llm": st.session_state.get("llm", False)
            },
            ensure_ascii=False
        )
    }

    pg.inserir_feedback(
        feedback_data["especie"],
        feedback_data["nome_comum"],
        feedback_data["info_corretas"],
        feedback_data["especie_correta"],
        feedback_data["observacao"],
        feedback_data["user_input"]
    )

    st.success("Feedback enviado com sucesso. Muito obrigada!")

    return True


def form_feedback():
    with st.expander("Feedback sobre a identificação"):
        form_id = "main"
        st.markdown("*Preencha o formulário abaixo para fornecer feedback sobre uma das espécies exibidas.*")
        with st.form("feedback_form_" + form_id):
            especie_options = [
                especie['nome_pt']
                for especie in st.session_state.results
            ]

            st.session_state["selected_especie"] = st.selectbox(
                "Selecione a espécie para o feedback:",
                options=[""] + especie_options,
                index=0,
                key="selected_especie_" + form_id
            )
            st.session_state["info_corretas"] = st.radio(
                "As informações fornecidas sobre a espécie estão corretas?",
                ["Sim", "Não"],
                key="info_corretas_" + form_id
            )
            st.session_state["especie_correta"] = st.radio(
                "Essa é a espécie que você estava buscando?",
                ["Sim", "Não"],
                key="especie_correta_" + form_id
            )
            st.session_state["observacao"] = st.text_area(
                "Observações adicionais (opcional):",
                key="observacao_" + form_id,
                placeholder="Ex.: A ave que observei estava em outro ambiente, ou a espécie foi exibida com cores incorretas."
            )
            st.session_state["submit_feedback"] = st.form_submit_button("Enviar Feedback")


def submit_feedback(pg):
    if st.session_state.submit_feedback is True:
        if not st.session_state.selected_especie:
            st.warning("Por favor, selecione uma espécie antes de enviar.")
        else:
            especie = next(
                (
                    e
                    for e in st.session_state.results
                    if e["nome_pt"] == st.session_state.selected_especie
                ),
                None
            )
            if especie:
                feedback_data = {
                    "info_corretas": st.session_state.info_corretas,
                    "especie_correta": st.session_state.especie_correta,
                    "observacao": st.session_state.observacao
                }
                is_process = process_feedback(
                    pg,
                    feedback_data,
                    especie,
                    st.session_state.get("user_input")
                )

                if is_process:
                    st.session_state.clear
