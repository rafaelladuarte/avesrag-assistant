from rapidfuzz import fuzz


def score_similaridade_lista(row_list, filter_list, threshold=80):
    if not row_list or not filter_list:
        return 0.0

    matched = 0
    for filtro in filter_list:
        best_score = max(
            (fuzz.ratio(filtro.lower(), item.lower()) for item in row_list),
            default=0
        )
        if best_score >= threshold:
            matched += 1

    recall = matched / len(filter_list)
    precision = matched / len(row_list)

    if precision + recall == 0:
        return 0.0

    f1 = 2 * (precision * recall) / (precision + recall)
    return f1


filtro = ['amarelo', 'preto', 'branco']

lista1 = ['amarelo', 'preto', 'branco']  # match perfeito
lista2 = ['preto', 'branco', 'azul', 'verde', 'rosa', 'amarelo']  # com ruído
lista3 = ['amarelado', 'acinzentado', 'branco sujo']  # parecidos
lista4 = ['vermelho', 'azul']  # nenhum match

print("Lista 1:", score_similaridade_lista(lista1, filtro))  # = 1.0
print("Lista 2:", score_similaridade_lista(lista2, filtro))  # < 1.0
print("Lista 3:", score_similaridade_lista(lista3, filtro))  # > 0
print("Lista 4:", score_similaridade_lista(lista4, filtro))  # = 0.0
