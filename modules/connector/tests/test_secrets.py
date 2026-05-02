"""Encrypted secret store tests."""
from __future__ import annotations

import base64

import pytest

from modules.connector.exceptions import MissingCredential
from modules.connector.secrets import EncryptedSqliteSecrets


def test_round_trip(encrypted_secrets: EncryptedSqliteSecrets) -> None:
    encrypted_secrets.set("openai", "api_key", "sk-secret-1234567890")
    assert encrypted_secrets.get("openai", "api_key") == "sk-secret-1234567890"


def test_missing_credential_raises(
    encrypted_secrets: EncryptedSqliteSecrets,
) -> None:
    with pytest.raises(MissingCredential):
        encrypted_secrets.get("openai", "missing")


def test_blob_does_not_contain_plaintext(
    encrypted_secrets: EncryptedSqliteSecrets,
) -> None:
    plaintext = "sk-very-secret-payload"
    encrypted_secrets.set("openai", "api_key", plaintext)
    blob_b64 = encrypted_secrets.export_blob("openai", "api_key")
    raw = base64.b64decode(blob_b64)
    assert plaintext.encode() not in raw


def test_wrong_master_key_fails(company_db: object) -> None:
    from modules.storage import CompanyDB

    assert isinstance(company_db, CompanyDB)
    k1 = EncryptedSqliteSecrets.generate_master_key()
    k2 = EncryptedSqliteSecrets.generate_master_key()
    s1 = EncryptedSqliteSecrets(company_db, k1)
    s1.set("svc", "k", "value")
    s2 = EncryptedSqliteSecrets(company_db, k2)
    with pytest.raises(ValueError):
        s2.get("svc", "k")


def test_master_key_too_short() -> None:
    with pytest.raises(ValueError):
        EncryptedSqliteSecrets(db=None, master_key=b"short")  # type: ignore[arg-type]
