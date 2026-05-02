"""Per-company LLM request counter — persisted in the company DB.

Survives API restarts and page refreshes. Exposed by the Board API for
the replay UI so operators can see how chatty each company has been with
its provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from modules.storage import CompanyDB

_MODULE_KEY = "llm"


@dataclass(frozen=True)
class LLMRequestStats:
    total: int
    failed: int
    rate_limited: int
    last_provider: str | None
    last_model: str | None
    last_at: str | None


class LLMRequestCounter:
    """Increments a single-row table on every provider call.

    Cheap: one ``UPDATE`` per LLM request. The replay route reads the
    same row.
    """

    def __init__(self, db: "CompanyDB") -> None:
        self._db = db

    def migrate(self) -> None:
        apply_migrations(
            self._db.connect(),
            module=_MODULE_KEY,
            migrations=load_migrations_from_package(
                "modules.llm.migrations.company",
            ),
        )

    def increment(
        self,
        *,
        ok: bool,
        rate_limited: bool = False,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE llm_request_counter SET "
                "total = total + 1, "
                "failed = failed + ?, "
                "rate_limited = rate_limited + ?, "
                "last_provider = COALESCE(?, last_provider), "
                "last_model = COALESCE(?, last_model), "
                "last_at = ? "
                "WHERE id = 1",
                (
                    0 if ok else 1,
                    1 if rate_limited else 0,
                    provider,
                    model,
                    now,
                ),
            )

    def read(self) -> LLMRequestStats:
        row = self._db.connect().execute(
            "SELECT total, failed, rate_limited, last_provider, "
            "last_model, last_at FROM llm_request_counter WHERE id = 1"
        ).fetchone()
        if row is None:
            return LLMRequestStats(0, 0, 0, None, None, None)
        return LLMRequestStats(
            total=int(row["total"]),
            failed=int(row["failed"]),
            rate_limited=int(row["rate_limited"]),
            last_provider=row["last_provider"],
            last_model=row["last_model"],
            last_at=row["last_at"],
        )


__all__ = ["LLMRequestCounter", "LLMRequestStats"]
