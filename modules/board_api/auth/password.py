"""Password hashing helpers.

bcrypt via passlib. Cost factor pinned at 12 — slow enough to deter
offline cracking, fast enough not to murder login latency.
"""
from __future__ import annotations

from passlib.context import CryptContext

_pwd_context: CryptContext = CryptContext(
    schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12,
)


def hash_password(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("password must be non-empty")
    return _pwd_context.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    if not plaintext or not hashed:
        return False
    try:
        return bool(_pwd_context.verify(plaintext, hashed))
    except ValueError:
        return False


__all__ = ["hash_password", "verify_password"]
