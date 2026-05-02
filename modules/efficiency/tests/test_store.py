"""FindingStore: open / dedupe / auto-close / acknowledge."""
from __future__ import annotations

import pytest

from modules.efficiency import (
    FindingCandidate,
    FindingNotFound,
    FindingStatus,
    FindingStore,
    InvalidTransition,
    Severity,
    SubjectType,
)


def _candidate(**overrides: object) -> FindingCandidate:
    base: dict[str, object] = {
        "detector_code": "loop.tool_repeat",
        "severity": Severity.WARN,
        "subject_type": SubjectType.AGENT,
        "subject_id": "agent-1",
        "evidence": {"count": 4},
        "recommendation": "look",
    }
    base.update(overrides)
    return FindingCandidate(**base)  # type: ignore[arg-type]


def test_upsert_opens_new_finding(finding_store: FindingStore) -> None:
    finding, opened = finding_store.upsert_from_candidate(_candidate())
    assert opened is True
    assert finding.occurrences == 1
    assert finding.status is FindingStatus.OPEN
    assert finding.evidence == {"count": 4}


def test_upsert_deduplicates_open_finding(finding_store: FindingStore) -> None:
    f1, _ = finding_store.upsert_from_candidate(_candidate())
    f2, opened = finding_store.upsert_from_candidate(
        _candidate(evidence={"count": 7})
    )
    assert opened is False
    assert f2.id == f1.id
    assert f2.occurrences == 2
    assert f2.evidence == {"count": 7}


def test_different_subject_opens_new(finding_store: FindingStore) -> None:
    a, _ = finding_store.upsert_from_candidate(_candidate(subject_id="a"))
    b, opened_b = finding_store.upsert_from_candidate(_candidate(subject_id="b"))
    assert opened_b is True
    assert a.id != b.id


def test_auto_close_marks_closed(finding_store: FindingStore) -> None:
    finding, _ = finding_store.upsert_from_candidate(_candidate())
    closed = finding_store.auto_close(finding.id)
    assert closed.status is FindingStatus.CLOSED
    assert closed.closed_at is not None


def test_acknowledge_keeps_open_state(finding_store: FindingStore) -> None:
    finding, _ = finding_store.upsert_from_candidate(_candidate())
    ack = finding_store.acknowledge(finding.id)
    assert ack.status is FindingStatus.ACKNOWLEDGED


def test_cannot_ack_closed(finding_store: FindingStore) -> None:
    finding, _ = finding_store.upsert_from_candidate(_candidate())
    finding_store.close(finding.id)
    with pytest.raises(InvalidTransition):
        finding_store.acknowledge(finding.id)


def test_get_unknown_raises(finding_store: FindingStore) -> None:
    with pytest.raises(FindingNotFound):
        finding_store.get("missing")


def test_list_filters_by_status_and_severity(
    finding_store: FindingStore,
) -> None:
    a, _ = finding_store.upsert_from_candidate(_candidate(subject_id="a"))
    b, _ = finding_store.upsert_from_candidate(
        _candidate(
            subject_id="b",
            severity=Severity.CRITICAL,
            detector_code="tool.error_storm",
            subject_type=SubjectType.TOOL,
        )
    )
    finding_store.close(a.id)

    open_only = finding_store.list(status=FindingStatus.OPEN)
    assert {f.id for f in open_only} == {b.id}

    crit = finding_store.list(severities=[Severity.CRITICAL])
    assert {f.id for f in crit} == {b.id}
