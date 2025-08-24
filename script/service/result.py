import streamlit as st


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
            col1, col2 = st.columns([1, 2])
            with col1:
                st.image(especie['url_image'], width=300)
            with col2:
                st.markdown("**Validação de Identificação**:")
                st.markdown(
                    f"- **Cores**: {especie['cores']}\n"
                    f"- **Tamanho**: {especie['tamanho']}\n"
                    f"- **Tipo de Bico**: {especie['tipo_bico']}\n"
                    f"- **Alimentação**: {especie['dieta_principal']}\n"
                    f"- **Habitat**: {especie['habitat']}\n"
                )
        except KeyError as e:
            st.error(f"Erro nos dados da espécie {i+1}: {str(e)}")

        st.markdown("---")
