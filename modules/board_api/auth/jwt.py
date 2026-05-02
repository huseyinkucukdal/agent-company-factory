"""JWT encode/decode + refresh-token store.

* Access tokens are short-lived (default 15 minutes), HS256-signed.
* Refresh tokens are opaque (random string); only their SHA-256 digest is
  persisted, so a stolen DB row cannot be replayed against the API.
* Rotation: ``swap_refresh`` revokes the old refresh in the same
  transaction it issues the new one.
"""
from __future__ import annotations

import hashlib
import logging
import secrets as stdlib_secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from modules.storage import BoardDB

from ..exceptions import (
    TokenExpired,
    TokenInvalid,
    TokenRevoked,
)
from ..rbac import UserRole
from ..settings import BoardAPISettings

_log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    refresh_id: str


@dataclass(frozen=True)
class AccessClaims:
    sub: str
    role: UserRole
    issued_at: datetime
    expires_at: datetime
    jti: str


class JWTCodec:
    """Encodes/decodes access tokens and persists refresh tokens."""

    def __init__(self, db: BoardDB, settings: BoardAPISettings) -> None:
        self._db = db
        self._settings = settings

    # ---------------------------------------------------------------- access

    def issue_access(self, *, user_id: str, role: UserRole) -> tuple[str, datetime]:
        now = _utcnow()
        exp = now + self._settings.access_token_ttl
        payload: dict[str, Any] = {
            "sub": user_id,
            "role": role.value,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
            "jti": uuid.uuid4().hex,
            "type": "access",
        }
        token = jwt.encode(
            payload, self._settings.jwt_secret, algorithm=self._settings.jwt_algorithm,
        )
        return token, exp

    def decode_access(self, token: str) -> AccessClaims:
        try:
            data = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
                options={"require": ["exp", "iat", "sub"]},
            )
        except JWTError as exc:
            msg = str(exc).lower()
            if "expired" in msg:
                raise TokenExpired(str(exc)) from exc
            raise TokenInvalid(str(exc)) from exc

        if data.get("type") != "access":
            raise TokenInvalid("not_access_token")
        try:
            role = UserRole(data["role"])
        except (KeyError, ValueError) as exc:
            raise TokenInvalid("missing_role") from exc

        return AccessClaims(
            sub=str(data["sub"]),
            role=role,
            issued_at=datetime.fromtimestamp(int(data["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(int(data["exp"]), tz=UTC),
            jti=str(data.get("jti", "")),
        )

    # ---------------------------------------------------------------- refresh

    def issue_refresh(self, *, user_id: str) -> tuple[str, datetime, str]:
        """Generate a new refresh token. Returns ``(token, expires_at, row_id)``."""
        token = stdlib_secrets.token_urlsafe(48)
        rid = uuid.uuid4().hex
        now = _utcnow()
        exp = now + self._settings.refresh_token_ttl
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO refresh_tokens "
                "(id, user_id, token_hash, expires_at, issued_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (rid, user_id, _hash_refresh(token), exp.isoformat(), now.isoformat()),
            )
        return token, exp, rid

    def issue_pair(self, *, user_id: str, role: UserRole) -> TokenPair:
        access, access_exp = self.issue_access(user_id=user_id, role=role)
        refresh, refresh_exp, rid = self.issue_refresh(user_id=user_id)
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            access_expires_at=access_exp,
            refresh_expires_at=refresh_exp,
            refresh_id=rid,
        )

    def swap_refresh(
        self, refresh_token: str, *, role: UserRole,
    ) -> TokenPair:
        """Rotate a refresh token. Old one is revoked atomically.

        Raises :class:`TokenInvalid` when unknown,
        :class:`TokenExpired` when past ``expires_at``,
        :class:`TokenRevoked` when already revoked.
        """
        token_hash = _hash_refresh(refresh_token)
        now = _utcnow()
        new_token = stdlib_secrets.token_urlsafe(48)
        new_id = uuid.uuid4().hex
        new_exp = now + self._settings.refresh_token_ttl

        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT id, user_id, expires_at, revoked "
                "FROM refresh_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                raise TokenInvalid("refresh_unknown")
            if row["revoked"]:
                raise TokenRevoked("refresh_revoked")
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                raise TokenExpired("refresh_expired")
            user_id = row["user_id"]
            conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 WHERE id = ?",
                (row["id"],),
            )
            conn.execute(
                "INSERT INTO refresh_tokens "
                "(id, user_id, token_hash, expires_at, issued_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    new_id,
                    user_id,
                    _hash_refresh(new_token),
                    new_exp.isoformat(),
                    now.isoformat(),
                ),
            )

        access, access_exp = self.issue_access(user_id=user_id, role=role)
        return TokenPair(
            access_token=access,
            refresh_token=new_token,
            access_expires_at=access_exp,
            refresh_expires_at=new_exp,
            refresh_id=new_id,
        )

    def revoke_refresh(self, refresh_token: str) -> bool:
        """Mark a refresh token as revoked. Returns True if a row changed."""
        token_hash = _hash_refresh(refresh_token)
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 "
                "WHERE token_hash = ? AND revoked = 0",
                (token_hash,),
            )
            return cur.rowcount > 0

    def revoke_all_for_user(self, user_id: str) -> int:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "UPDATE refresh_tokens SET revoked = 1 "
                "WHERE user_id = ? AND revoked = 0",
                (user_id,),
            )
            return int(cur.rowcount)

    def purge_expired(self, *, before: datetime | None = None) -> int:
        """Delete refresh tokens past their ``expires_at``. Returns row count."""
        cutoff = before or _utcnow()
        with self._db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM refresh_tokens WHERE expires_at <= ? OR revoked = 1",
                (cutoff.isoformat(),),
            )
            return int(cur.rowcount)


def _row(row: sqlite3.Row) -> dict[str, Any]:  # for tests / debugging
    return {k: row[k] for k in row.keys()}


__all__ = ["AccessClaims", "JWTCodec", "TokenPair"]
