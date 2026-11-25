"""Convites do beta (plano_assistant4.md §4.1).

O token é mostrado uma vez, na criação. O arquivo guarda só o hash (SHA-256), o
nome de quem recebeu, a data e se foi revogado; não carrega dado pessoal além do
nome que a usuária escolheu. O arquivo fica fora do git.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PAPEIS = ("convidado", "curadoria")


class ConviteInvalido(ValueError):
    """Nome repetido, nome vazio ou convite que não existe."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Convites:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _ler(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _gravar(self, dados: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".convites.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(dados, fh, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def criar(self, nome: str, papel: str = "convidado") -> str:
        """Um convite novo para `nome`; devolve o token, que não fica guardado.

        `curadoria` pode corrigir as fichas; `convidado` só usa a página."""
        if papel not in PAPEIS:
            raise ConviteInvalido(f"papel inválido: {papel!r} (use {list(PAPEIS)})")
        nome = nome.strip()
        if not nome:
            raise ConviteInvalido("o nome do convite não pode ser vazio")
        dados = self._ler()
        if nome in dados:
            raise ConviteInvalido(f"já existe um convite para {nome!r}")
        token = secrets.token_urlsafe(24)
        dados[nome] = {
            "hash": _hash(token),
            "criado_em": datetime.now(UTC).isoformat(timespec="seconds"),
            "papel": papel,
            "revogado": False,
        }
        self._gravar(dados)
        return token

    def validar(self, token: str | None) -> str | None:
        """O nome do convite, ou None se o token não existe ou foi revogado."""
        if not token:
            return None
        procurado = _hash(token)
        achado = None
        for nome, d in self._ler().items():  # percorre tudo: tempo igual para todos os casos
            if hmac.compare_digest(d["hash"], procurado) and not d.get("revogado"):
                achado = nome
        return achado

    def papel_de(self, nome: str) -> str:
        """O papel de um convite (os anteriores à curadoria são `convidado`)."""
        return self._ler().get(nome, {}).get("papel", "convidado")

    def listar(self) -> list[tuple[str, str, bool]]:
        return [(n, d["criado_em"], d.get("revogado", False)) for n, d in self._ler().items()]

    def revogar(self, nome: str) -> None:
        dados = self._ler()
        if nome not in dados:
            raise ConviteInvalido(f"não existe convite para {nome!r}")
        dados[nome]["revogado"] = True
        self._gravar(dados)
