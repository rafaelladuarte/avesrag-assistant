import psycopg2


class PostgreSQL:
    def __init__(self, uri):
        self.uri = uri

    def conectar_banco(self):
        try:
            conexao = psycopg2.connect(
                self.uri
            )
            return conexao
        except psycopg2.Error as e:
            print(f"Erro ao conectar ao banco de dados: {e}")
            return None

    def inserir_feedback(
        self,
        especie,
        nome_comum,
        info_corretas,
        especie_correta,
        observacao,
        user_input
    ):
        conexao = self.conectar_banco()
        if conexao is None:
            return

        try:
            cursor = conexao.cursor()

            query = """
            INSERT INTO feedback_aves_rag (
                especie,
                nome_comum,
                info_corretas,
                especie_correta,
                observacao,
                user_input
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
            """

            valores = (
                especie,
                nome_comum,
                info_corretas,
                especie_correta,
                observacao,
                user_input
            )

            cursor.execute(query, valores)
            feedback_id = cursor.fetchone()[0]

            conexao.commit()
            print(f"Feedback inserido com sucesso! ID: {feedback_id}")

        except psycopg2.Error as e:
            print(f"Erro ao inserir dados: {e}")
            conexao.rollback()

        finally:
            cursor.close()
            conexao.close()
