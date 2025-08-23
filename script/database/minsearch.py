import pandas as pd

from sklearn.feature_extraction.text import TfidfVectorizer
# from sklearn.metrics.pairwise import cosine_similarity

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

        # Set default vectorizer parameters to ensure we always have terms
        default_params = {
            'min_df': 1,  # Include terms that appear in at least 1 document
            'max_df': 1.0,  # Include terms that appear in all documents
            # Match words with at least 2 characters
            'token_pattern': r'(?u)\b\w\w+\b',
            'stop_words': None  # Don't remove any stop words by default
        }
        # Update with user parameters, but ensure defaults
        # are used if not specified
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

        # Handle empty documents case
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
                    # If no terms remain, create a dummy matrix
                    # with a single term
                    # A term that won't be filtered out
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

        # query_vecs = {
        #     field: self.vectorizers[field].transform([query])
        #     for field in self.text_fields
        # }
        # scores = np.zeros(len(self.docs))

        # Compute cosine similarity for each text field and apply boost
        # for field, query_vec in query_vecs.items():
        #     sim = cosine_similarity(
        #         query_vec, self.text_matrices[field]
        #     ).flatten()
        #     boost = boost_dict.get(field, 1)
        #     scores += sim * boost

        partial_score = []

        for field, value in filter_dict.items():
            if field in ['cores', 'dieta_principal', 'habitat']:
                field_scores = self.keyword_df[field].apply(
                    lambda x: self.__similarity_score_list(x, value)
                )
                normalized_scores = field_scores / 100
                # weighted_scores = scores * normalized_scores.to_numpy()
                boost = boost_dict.get(field, 1)
                weighted_scores = normalized_scores.to_numpy().copy() * boost
                partial_score.append(weighted_scores)
            elif field in ["tamanho", "tipo_bico"]:
                mask = self.keyword_df[field] == value
                # filter_scores = scores * mask.to_numpy()
                boost = boost_dict.get(field, 1)
                filter_scores = mask.to_numpy().copy() * boost
                partial_score.append(filter_scores)

        if partial_score:
            final_score = np.sum(partial_score, axis=0)

            # top_50_indices = np.argsort(-final_score)[:50]
            # score_frequency = self.keyword_df[
            # "frequencia_normalizada"].values
            # final_score[top_50_indices] = (
            # 0.8 * final_score[top_50_indices]) + (
            # 0.2 * score_frequency[top_50_indices])
            # score_frequency = self.keyword_df[
            # "frequencia_normalizada"].values
            # final_score = (0.6 * final_score) + (0.4 * score_frequency)

        # Get number of non-zero scores
        non_zero_mask = final_score > 0
        non_zero_count = np.sum(non_zero_mask)

        if non_zero_count == 0:
            return []

        # Ensure num_results doesn't exceed the number of non-zero scores
        num_results = min(num_results, non_zero_count)

        # Get indices of non-zero scores
        non_zero_indices = np.where(non_zero_mask)[0]

        # Sort non-zero scores in descending order
        sorted_indices = non_zero_indices[
            np.argsort(-final_score[non_zero_indices])
        ]

        # Take top num_results
        top_indices = sorted_indices[:num_results]

        # Return corresponding documents
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
