"""Allowlist gate.

Tracks which ``(service, action)`` pairs are permitted for the company.
Every entry is also persisted as an audit event when toggled (caller's
responsibility — we only own the in-memory state).
"""
from __future__ import annotations

import threading


class Allowlist:
    def __init__(self) -> None:
        # store ``service`` for blanket-allow; ``service:action`` for narrow.
        self._entries: set[str] = set()
        self._lock = threading.Lock()

    def allow(self, service: str, action: str | None = None) -> None:
        key = service if action is None else f"{service}:{action}"
        with self._lock:
            self._entries.add(key)

    def revoke(self, service: str, action: str | None = None) -> None:
        key = service if action is None else f"{service}:{action}"
        with self._lock:
            self._entries.discard(key)

    def is_allowed(self, service: str, action: str) -> bool:
        with self._lock:
            return service in self._entries or f"{service}:{action}" in self._entries

    def entries(self) -> list[tuple[str, str | None]]:
        """Snapshot of every authorised ``(service, action)`` pair.

        ``action`` is ``None`` for blanket ``service``-wide entries.
        """
        with self._lock:
            snapshot = sorted(self._entries)
        out: list[tuple[str, str | None]] = []
        for key in snapshot:
            if ":" in key:
                svc, action = key.split(":", 1)
                out.append((svc, action))
            else:
                out.append((key, None))
        return out
