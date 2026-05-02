"""Secret store.

A :class:`Secrets` Protocol plus an :class:`EncryptedSqliteSecrets`
implementation that confidentiality-protects values at rest using a
HMAC-SHA256-keystream + HMAC-SHA256-MAC (Encrypt-then-MAC) scheme drawn
exclusively from the standard library. This is intentionally simple and
intended only for casual at-rest secrecy on a single-tenant SQLite file —
production deployments should swap in a vault-backed implementation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets as _secrets
import threading
from typing import Protocol

from modules.storage import CompanyDB

from .exceptions import MissingCredential


class Secrets(Protocol):
    def get(self, service: str, key: str) -> str: ...

    def set(self, service: str, key: str, value: str) -> None: ...


class _NoopSecrets:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get(self, service: str, key: str) -> str:
        with self._lock:
            try:
                return self._values[(service, key)]
            except KeyError as exc:
                raise MissingCredential(f"{service}:{key}") from exc

    def set(self, service: str, key: str, value: str) -> None:
        with self._lock:
            self._values[(service, key)] = value


def in_memory_secrets() -> Secrets:
    """Convenience factory for tests / dev shells."""
    return _NoopSecrets()


# --------------------------------------------------------------------- crypto


def _kdf(master_key: bytes, salt: bytes, info: bytes) -> bytes:
    """HKDF-Extract-and-Expand (one block) using HMAC-SHA256."""
    prk = hmac.new(salt, master_key, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA-256(key || nonce || counter) as a counter-mode keystream."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(
            key + nonce + counter.to_bytes(8, "big")
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _seal(master_key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    enc_key = _kdf(master_key, nonce, b"enc")
    mac_key = _kdf(master_key, nonce, b"mac")
    ct = bytes(p ^ k for p, k in zip(plaintext, _keystream(enc_key, nonce, len(plaintext)), strict=True))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return nonce + tag + ct


def _open(master_key: bytes, blob: bytes) -> bytes:
    if len(blob) < 12 + 32:
        raise ValueError("ciphertext too short")
    nonce, tag, ct = blob[:12], blob[12:44], blob[44:]
    enc_key = _kdf(master_key, nonce, b"enc")
    mac_key = _kdf(master_key, nonce, b"mac")
    expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ValueError("authentication failed")
    return bytes(c ^ k for c, k in zip(ct, _keystream(enc_key, nonce, len(ct)), strict=True))


# --------------------------------------------------------------- sqlite store


_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_secrets (
    service TEXT NOT NULL,
    key TEXT NOT NULL,
    blob BLOB NOT NULL,
    PRIMARY KEY (service, key)
)
"""


class EncryptedSqliteSecrets:
    """Per-company encrypted secret store on top of :class:`CompanyDB`."""

    def __init__(self, db: CompanyDB, master_key: bytes) -> None:
        if len(master_key) < 16:
            raise ValueError("master_key must be at least 16 bytes")
        self._db = db
        self._master = master_key
        self._lock = threading.Lock()
        with self._db.transaction() as conn:
            conn.execute(_SCHEMA)

    @classmethod
    def generate_master_key(cls) -> bytes:
        return _secrets.token_bytes(32)

    def get(self, service: str, key: str) -> str:
        row = self._db.connect().execute(
            "SELECT blob FROM connector_secrets WHERE service = ? AND key = ?",
            (service, key),
        ).fetchone()
        if row is None:
            raise MissingCredential(f"{service}:{key}")
        return _open(self._master, row[0]).decode("utf-8")

    def set(self, service: str, key: str, value: str) -> None:
        blob = _seal(self._master, value.encode("utf-8"))
        with self._lock, self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO connector_secrets (service, key, blob) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(service, key) DO UPDATE SET blob = excluded.blob",
                (service, key, blob),
            )

    def export_blob(self, service: str, key: str) -> str:
        """Return the raw ciphertext (base64) — used by tests to assert that
        the on-disk representation never contains plaintext."""
        row = self._db.connect().execute(
            "SELECT blob FROM connector_secrets WHERE service = ? AND key = ?",
            (service, key),
        ).fetchone()
        if row is None:
            raise MissingCredential(f"{service}:{key}")
        return base64.b64encode(row[0]).decode("ascii")
