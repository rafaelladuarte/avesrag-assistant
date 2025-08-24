import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer

from rapidfuzz import fuzz
import numpy as np


class Index:
    def __init__(self, text_fields, keyword_fields, vectorizer_params=None):
        self.text_fields = text_fields
        self.keyword_fields = keyword_fields
        if vectorizer_params is None:
            vectorizer_params = {
                "stop_words": [
                    "de", "a", "o", "que", "e", "do", "da", "em", "um", "para"
                ],
                "max_df": 0.8,
                "min_df": 2,
            }

        default_params = {
            'min_df': 1,  # Include terms that appear in at least 1 document
            'max_df': 1.0,  # Include terms that appear in all documents
            'token_pattern': r'(?u)\b\w\w+\b', # Match words with at least 2 characters
            'stop_words': None  # Don't remove any stop words by default
        }
        vectorizer_params = {**default_params, **vectorizer_params}

        self.vectorizers = {
            field: TfidfVectorizer(**vectorizer_params)
            for field in text_fields
        }
        self.keyword_df = None
        self.text_matrices = {}
        self.docs = []

    def fit(self, docs):
        self.docs = docs
        keyword_data = {field: [] for field in self.keyword_fields}

        if not docs:
            self.keyword_df = pd.DataFrame(keyword_data)
            return self

        for field in self.text_fields:
            texts = [
                doc.get(field, '')
                if doc.get(field, '') else ''
                for doc in docs
            ]
            try:
                self.text_matrices[field] = self.vectorizers[
                    field
                ].fit_transform(texts)
            except ValueError as e:
                if "no terms remain" in str(e) or "empty vocabulary" in str(e):
                    dummy_text = "dummy_term"
                    self.text_matrices[field] = self.vectorizers[
                        field
                    ].fit_transform([dummy_text])
                else:
                    raise

        for doc in docs:
            for field in self.keyword_fields:
                keyword_data[field].append(doc.get(field, ''))

        self.keyword_df = pd.DataFrame(keyword_data)

        return self

    def search(
        self,
        query,
        filter_dict=None,
        boost_dict=None,
        num_results=10,
        output_ids=False
    ):
        if filter_dict is None:
            filter_dict = {}
        if boost_dict is None:
            boost_dict = {}

        if not self.docs:
            return []

        partial_score = []

        for field, value in filter_dict.items():
            if field in ['cores', 'dieta_principal', 'habitat']:
                field_scores = self.keyword_df[field].apply(
                    lambda x: self.__similarity_score_list(x, value)
                )
                normalized_scores = field_scores / 100
                boost = boost_dict.get(field, 1)
                weighted_scores = normalized_scores.to_numpy().copy() * boost
                partial_score.append(weighted_scores)
            elif field in ["tamanho", "tipo_bico"]:
                mask = self.keyword_df[field] == value
                boost = boost_dict.get(field, 1)
                filter_scores = mask.to_numpy().copy() * boost
                partial_score.append(filter_scores)

        if partial_score:
            final_score = np.sum(partial_score, axis=0)

        non_zero_mask = final_score > 0
        non_zero_count = np.sum(non_zero_mask)

        if non_zero_count == 0:
            return []

        num_results = min(num_results, non_zero_count)

        non_zero_indices = np.where(non_zero_mask)[0]

        sorted_indices = non_zero_indices[
            np.argsort(-final_score[non_zero_indices])
        ]

        top_indices = sorted_indices[:num_results]

        if output_ids:
            return [{**self.docs[i], '_id': int(i)} for i in top_indices]
        return [self.docs[i] for i in top_indices]

    def __similarity_score_list(self, row_list, filter_list, threshold=70):
        if not row_list or not filter_list:
            return 0.0

        matched = 0
        for filtro in filter_list:
            melhor = max(
                fuzz.ratio(filtro.lower(), item.lower())
                for item in row_list
            )
            if melhor >= threshold:
                matched += 1

        recall = matched / len(filter_list)
        precision = matched / len(row_list)

        if precision + recall == 0:
            return 0.0

        return 2 * (precision * recall) / (precision + recall)
