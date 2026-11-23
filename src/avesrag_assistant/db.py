"""Conexão com o MongoDB montado pelo builder.

Um cliente por processo, no molde de `avesrag_builder.db.connection`, mas com o
`Settings` do assistant: o `get_db` do builder leria o `.env` do builder.
"""

from pymongo import MongoClient
from pymongo.database import Database

from avesrag_assistant.settings import get_settings

SERVER_SELECTION_TIMEOUT_MS = 5000

_client: MongoClient | None = None


def get_client() -> MongoClient:
    """Cliente MongoDB compartilhado, criado na primeira chamada."""
    global _client
    if _client is None:
        _client = MongoClient(
            get_settings().MONGODB_URI, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS
        )
    return _client


def get_db() -> Database:
    """Banco configurado em MONGODB_DATABASE."""
    return get_client()[get_settings().MONGODB_DATABASE]


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
