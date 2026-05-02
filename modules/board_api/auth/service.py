"""User registration / login / refresh / logout / lookup."""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from modules.storage import BoardDB

from ..exceptions import (
    EmailAlreadyExists,
    InvalidCredentials,
    UserNotFound,
)
from ..rbac import UserRole
from .jwt import JWTCodec, TokenPair
from .password import hash_password, verify_password


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True)
class BoardUser:
    id: str
    email: str
    role: UserRole
    created_at: datetime
    last_login: datetime | None
    deleted: bool


class AuthService:
    """Board user lifecycle. Owns the ``users`` table."""

    def __init__(self, db: BoardDB, codec: JWTCodec) -> None:
        self._db = db
        self._codec = codec

    # ----------------------------------------------------------- queries

    def get(self, user_id: str) -> BoardUser:
        row = self._db.connect().execute(
            "SELECT * FROM users WHERE id = ? AND deleted = 0",
            (user_id,),
        ).fetchone()
        if row is None:
            raise UserNotFound(user_id)
        return _row_to_user(row)

    def get_by_email(self, email: str) -> BoardUser:
        row = self._db.connect().execute(
            "SELECT * FROM users WHERE email = ? AND deleted = 0",
            (_normalise_email(email),),
        ).fetchone()
        if row is None:
            raise UserNotFound(email)
        return _row_to_user(row)

    def list(self, *, include_deleted: bool = False) -> list[BoardUser]:
        if include_deleted:
            rows = self._db.connect().execute(
                "SELECT * FROM users ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._db.connect().execute(
                "SELECT * FROM users WHERE deleted = 0 ORDER BY created_at"
            ).fetchall()
        return [_row_to_user(r) for r in rows]

    def count_admins(self) -> int:
        row = self._db.connect().execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = ? AND deleted = 0",
            (UserRole.ADMIN.value,),
        ).fetchone()
        return int(row["n"])

    # --------------------------------------------------------- register

    def register(
        self, *, email: str, password: str, role: UserRole | None = None,
    ) -> BoardUser:
        """Create a new user.

        First call promotes the new account to ``ADMIN`` regardless of the
        role argument. Subsequent calls default to ``OBSERVER`` unless an
        admin invokes the user-management route to specify a role.
        """
        normalised = _normalise_email(email)
        if not normalised or "@" not in normalised:
            raise ValueError("invalid_email")
        if not password or len(password) < 8:
            raise ValueError("password_too_short")

        is_first = self.count_admins() == 0
        chosen_role = UserRole.ADMIN if is_first else (role or UserRole.OBSERVER)
        new_id = uuid.uuid4().hex
        password_hash = hash_password(password)

        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO users "
                    "(id, email, password_hash, role, created_at, deleted) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        new_id,
                        normalised,
                        password_hash,
                        chosen_role.value,
                        _utcnow_iso(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise EmailAlreadyExists(normalised) from exc

        return self.get(new_id)

    # ------------------------------------------------------------- login

    def login(self, *, email: str, password: str) -> tuple[BoardUser, TokenPair]:
        normalised = _normalise_email(email)
        row = self._db.connect().execute(
            "SELECT * FROM users WHERE email = ? AND deleted = 0",
            (normalised,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise InvalidCredentials("bad_email_or_password")
        user = _row_to_user(row)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (_utcnow_iso(), user.id),
            )
        pair = self._codec.issue_pair(user_id=user.id, role=user.role)
        return self.get(user.id), pair

    def refresh(self, refresh_token: str) -> tuple[BoardUser, TokenPair]:
        # We need the user's role to issue a new access token; resolve it
        # from the existing refresh-token row prior to swap.
        token_hash_row = self._db.connect().execute(
            "SELECT user_id FROM refresh_tokens "
            "WHERE token_hash = ? AND revoked = 0",
            (_token_digest(refresh_token),),
        ).fetchone()
        if token_hash_row is None:
            # Let the codec produce the canonical TokenInvalid/Revoked error.
            self._codec.swap_refresh(refresh_token, role=UserRole.OBSERVER)
            raise InvalidCredentials("refresh_unknown")  # pragma: no cover
        user = self.get(token_hash_row["user_id"])
        pair = self._codec.swap_refresh(refresh_token, role=user.role)
        return user, pair

    def logout(self, refresh_token: str) -> bool:
        return self._codec.revoke_refresh(refresh_token)

    # ---------------------------------------------------------- mutations

    def set_role(self, user_id: str, role: UserRole) -> BoardUser:
        user = self.get(user_id)
        if user.role is role:
            return user
        if user.role is UserRole.ADMIN and role is not UserRole.ADMIN:
            # Demoting last admin is forbidden.
            if self.count_admins() <= 1:
                raise ValueError("cannot_demote_last_admin")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET role = ? WHERE id = ?",
                (role.value, user_id),
            )
        return self.get(user_id)

    def soft_delete(self, user_id: str) -> None:
        user = self.get(user_id)
        if user.role is UserRole.ADMIN and self.count_admins() <= 1:
            raise ValueError("cannot_delete_last_admin")
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET deleted = 1, email = email || ':deleted:' || id "
                "WHERE id = ?",
                (user_id,),
            )
        # Revoke all live tokens.
        self._codec.revoke_all_for_user(user_id)

    # -------------------------------------------------------- assignments

    def assign_company(self, user_id: str, company_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO company_assignments (user_id, company_id) "
                "VALUES (?, ?)",
                (user_id, company_id),
            )

    def unassign_company(self, user_id: str, company_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM company_assignments "
                "WHERE user_id = ? AND company_id = ?",
                (user_id, company_id),
            )

    def assignments(self, user_id: str) -> set[str]:
        rows = self._db.connect().execute(
            "SELECT company_id FROM company_assignments WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r["company_id"] for r in rows}


def _token_digest(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_user(row: Any) -> BoardUser:
    return BoardUser(
        id=row["id"],
        email=row["email"],
        role=UserRole(row["role"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_login=(
            datetime.fromisoformat(row["last_login"]) if row["last_login"] else None
        ),
        deleted=bool(row["deleted"]),
    )


__all__ = ["AuthService", "BoardUser"]
