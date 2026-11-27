"""Fila de revisão: texto do WikiAves colado à mão (temporário)."""

import pytest

from avesrag_assistant.api import fila


class _Col:
    def __init__(self):
        self.docs = {}

    def count_documents(self, filtro, limit=0):
        return 1 if filtro["species_id"] == "sp_a" else 0

    def find_one(self, filtro):
        return self.docs.get(filtro["_id"])

    def update_one(self, filtro, op, upsert=False):
        self.docs.setdefault(filtro["_id"], {"_id": filtro["_id"]}).update(op["$set"])

    def delete_one(self, filtro):
        self.docs.pop(filtro["_id"], None)


class _Db(dict):
    def __missing__(self, k):
        self[k] = _Col()
        return self[k]


def test_gravar_ler_e_apagar_texto():
    db = _Db()
    r = fila.gravar_texto(db, "sp_a", "  Descrição: bico preto.  ", "rafa")
    assert r["texto"] == "Descrição: bico preto."
    assert fila.ler_texto(db, "sp_a")["atualizado_por"] == "rafa"
    assert fila.gravar_texto(db, "sp_a", "  ", "rafa")["texto"] == ""


def test_recusa_especie_inexistente_e_texto_grande():
    db = _Db()
    with pytest.raises(ValueError):
        fila.gravar_texto(db, "sp_x", "oi", "rafa")
    with pytest.raises(ValueError):
        fila.gravar_texto(db, "sp_a", "a" * (fila.MAX_TEXTO + 1), "rafa")


def test_concluir_e_reabrir_tira_e_devolve_da_fila(tmp_path):
    import json

    arquivo = tmp_path / "fila.json"
    arquivo.write_text(
        json.dumps(
            [
                {
                    "species_id": "sp_a",
                    "nome": "Ave A",
                    "ordem": 1,
                    "motivos": [{"tipo": "uma_cor", "campo": "atributos.cores"}],
                },
                {
                    "species_id": "sp_b",
                    "nome": "Ave B",
                    "ordem": 0,
                    "motivos": [{"tipo": "uma_cor", "campo": "atributos.cores"}],
                },
            ]
        )
    )

    class Col(_Col):
        def find(self, filtro=None, proj=None):
            return list(self.docs.values())

    class Db(dict):
        def __missing__(self, k):
            self[k] = Col()
            return self[k]

    db = Db()
    db["species"].docs = {
        "a": {"species_id": "sp_a", "nome_cientifico": "A a", "atributos": {"cores": ["preto"]}},
        "b": {"species_id": "sp_b", "nome_cientifico": "B b", "atributos": {"cores": ["preto"]}},
    }
    db["species"].count_documents = lambda f, limit=0: 1
    assert [e["species_id"] for e in fila.pendentes(db, arquivo)] == ["sp_b", "sp_a"]  # por ordem
    fila.concluir(db, "sp_b", "rafa")
    assert [e["species_id"] for e in fila.pendentes(db, arquivo)] == ["sp_a"]
    fila.reabrir(db, "sp_b")
    assert len(fila.pendentes(db, arquivo)) == 2
