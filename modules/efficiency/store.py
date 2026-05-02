"""Persistence for findings — open/update/close with dedupe."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from modules.storage import CompanyDB
from modules.storage.migrations.runner import (
    apply_migrations,
    load_migrations_from_package,
)

from .exceptions import FindingNotFound, InvalidTransition
from .models import Finding, FindingCandidate, FindingStatus, Severity, SubjectType

_MODULE_KEY = "efficiency"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _row_to_finding(row: sqlite3.Row) -> Finding:
    return Finding(
        id=row["id"],
        detector_code=row["detector_code"],
        severity=Severity(row["severity"]),
        subject_type=SubjectType(row["subject_type"]),
        subject_id=row["subject_id"],
        opened_at=_parse_dt(row["opened_at"]) or _utcnow(),
        last_seen_at=_parse_dt(row["last_seen_at"]) or _utcnow(),
        closed_at=_parse_dt(row["closed_at"]),
        occurrences=int(row["occurrences"]),
        evidence=json.loads(row["evidence_json"]),
        recommendation=row["recommendation"],
        status=FindingStatus(row["status"]),
    )


class FindingStore:
    """Per-company SQLite-backed store for :class:`Finding` records."""

    def __init__(
        self,
        db: CompanyDB,
        *,
        time_provider: Any = None,
    ) -> None:
        self._db = db
        self._now = time_provider or _utcnow

    def migrate(self) -> None:
        migrations = load_migrations_from_package("modules.efficiency.migrations")
        apply_migrations(
            self._db.connect(), module=_MODULE_KEY, migrations=migrations
        )

    # -------------------------------------------------------------- queries

    def get(self, finding_id: str) -> Finding:
        row = self._db.connect().execute(
            "SELECT * FROM efficiency_findings WHERE id = ?", (finding_id,)
        ).fetchone()
        if row is None:
            raise FindingNotFound(finding_id)
        return _row_to_finding(row)

    def list(
        self,
        *,
        status: FindingStatus | None = None,
        severities: Iterable[Severity] | None = None,
        detector_code: str | None = None,
        limit: int = 500,
    ) -> Sequence[Finding]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if severities is not None:
            sev_list = list(severities)
            if not sev_list:
                return []
            placeholders = ",".join("?" for _ in sev_list)
            clauses.append(f"severity IN ({placeholders})")
            params.extend(s.value for s in sev_list)
        if detector_code is not None:
            clauses.append("detector_code = ?")
            params.append(detector_code)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM efficiency_findings{where} "
            "ORDER BY opened_at DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._db.connect().execute(sql, params).fetchall()
        return [_row_to_finding(r) for r in rows]

    def find_open(
        self,
        *,
        detector_code: str,
        subject_type: SubjectType,
        subject_id: str | None,
    ) -> Finding | None:
        """Return the (single) open finding for a (detector, subject) key, if any."""
        if subject_id is None:
            row = self._db.connect().execute(
                "SELECT * FROM efficiency_findings "
                "WHERE detector_code = ? AND subject_type = ? "
                "  AND subject_id IS NULL "
                "  AND status IN ('open', 'acknowledged') "
                "LIMIT 1",
                (detector_code, subject_type.value),
            ).fetchone()
        else:
            row = self._db.connect().execute(
                "SELECT * FROM efficiency_findings "
                "WHERE detector_code = ? AND subject_type = ? "
                "  AND subject_id = ? "
                "  AND status IN ('open', 'acknowledged') "
                "LIMIT 1",
                (detector_code, subject_type.value, subject_id),
            ).fetchone()
        return _row_to_finding(row) if row else None

    def list_open(self, *, detector_code: str) -> Sequence[Finding]:
        rows = self._db.connect().execute(
            "SELECT * FROM efficiency_findings "
            "WHERE detector_code = ? AND status IN ('open', 'acknowledged') "
            "ORDER BY opened_at",
            (detector_code,),
        ).fetchall()
        return [_row_to_finding(r) for r in rows]

    # --------------------------------------------------------------- writes

    def upsert_from_candidate(
        self, candidate: FindingCandidate
    ) -> tuple[Finding, bool]:
        """Open a new finding or refresh an existing open one.

        Returns (finding, opened_now): ``opened_now`` is True only when a
        brand-new row was created. Callers use that to emit the
        ``efficiency.finding.opened`` event.
        """
        existing = self.find_open(
            detector_code=candidate.detector_code,
            subject_type=candidate.subject_type,
            subject_id=candidate.subject_id,
        )
        now = self._now()
        if existing is not None:
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE efficiency_findings "
                    "SET last_seen_at = ?, "
                    "    occurrences = occurrences + 1, "
                    "    evidence_json = ?, "
                    "    severity = ?, "
                    "    recommendation = COALESCE(?, recommendation) "
                    "WHERE id = ?",
                    (
                        now.isoformat(),
                        json.dumps(candidate.evidence, sort_keys=True),
                        candidate.severity.value,
                        candidate.recommendation,
                        existing.id,
                    ),
                )
            return self.get(existing.id), False

        finding_id = _new_id()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO efficiency_findings "
                "(id, detector_code, severity, subject_type, subject_id, "
                " opened_at, last_seen_at, occurrences, evidence_json, "
                " recommendation, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'open')",
                (
                    finding_id,
                    candidate.detector_code,
                    candidate.severity.value,
                    candidate.subject_type.value,
                    candidate.subject_id,
                    now.isoformat(),
                    now.isoformat(),
                    json.dumps(candidate.evidence, sort_keys=True),
                    candidate.recommendation,
                ),
            )
        return self.get(finding_id), True

    def auto_close(self, finding_id: str) -> Finding:
        """Close a finding because the underlying condition is no longer firing."""
        existing = self.get(finding_id)
        if existing.status is FindingStatus.CLOSED:
            return existing
        now = self._now()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE efficiency_findings "
                "SET status = 'closed', closed_at = ? "
                "WHERE id = ?",
                (now.isoformat(), finding_id),
            )
        return self.get(finding_id)

    def acknowledge(self, finding_id: str) -> Finding:
        existing = self.get(finding_id)
        if existing.status is FindingStatus.CLOSED:
            raise InvalidTransition(
                f"cannot ack a closed finding: {finding_id}"
            )
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE efficiency_findings SET status = 'acknowledged' "
                "WHERE id = ?",
                (finding_id,),
            )
        return self.get(finding_id)

    def close(self, finding_id: str) -> Finding:
        existing = self.get(finding_id)
        if existing.status is FindingStatus.CLOSED:
            return existing
        now = self._now()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE efficiency_findings "
                "SET status = 'closed', closed_at = ? "
                "WHERE id = ?",
                (now.isoformat(), finding_id),
            )
        return self.get(finding_id)
