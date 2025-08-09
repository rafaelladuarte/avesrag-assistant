from typing import Optional
import os


def get_secret_value(secret_nm: str) -> str:
    secret_vl: Optional[str] = os.getenv(secret_nm)
    if secret_vl:
        return secret_vl
    if not secret_vl:
        print("Secret has no value")
        raise

    return secret_vl
