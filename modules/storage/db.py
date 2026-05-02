"""SQLite database wrappers.

`BoardDB` is the single board-level DB.
`CompanyDB` is per-company; one file per company under `<root>/companies/<id>/company.db`.

Both share `_DBBase` for connection handling, transactions, and PRAGMA setup.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .exceptions import CorruptDatabase
from .migrations.runner import run_storage_migrations


class _DBBase:
    """Connection-per-thread SQLite handle with WAL + immediate transactions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                isolation_level=None,  # we manage transactions explicitly
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=5.0,
            )
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Begin IMMEDIATE; commit on success, rollback on error.

        Retries up to 3 times on transient `database is locked`.
        """
        conn = self.connect()
        attempts = 3
        for i in range(attempts):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and i < attempts - 1:
                    time.sleep(0.05 * (2**i))
                    continue
                raise
        try:
            yield conn
        except BaseException:
            with suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def integrity_check(self) -> None:
        conn = self.connect()
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise CorruptDatabase(str(row))


class CompanyDB(_DBBase):
    """Per-company SQLite handle.

    Each company has its own file at `<root>/companies/<id>/company.db`.
    """

    def __init__(self, company_id: str, root: Path) -> None:
        self.company_id = company_id
        self.root = Path(root)
        super().__init__(self._compute_path(self.root, company_id))

    @staticmethod
    def _compute_path(root: Path, company_id: str) -> Path:
        return Path(root) / "companies" / company_id / "company.db"

    @classmethod
    def init(cls, company_id: str, root: Path) -> CompanyDB:
        instance = cls(company_id, root)
        instance.db_path.parent.mkdir(parents=True, exist_ok=True)
        return instance

    def migrate(self) -> None:
        run_storage_migrations(self.connect(), scope="company")

    def archive(self, dest: Path) -> None:
        """Copy the live DB to `dest` using `VACUUM INTO` (consistent snapshot)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        self.connect().execute("VACUUM INTO ?", (str(dest),))


class BoardDB(_DBBase):
    """Board-level SQLite handle (`<root>/board.db`)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        super().__init__(self.root / "board.db")

    @classmethod
    def init(cls, root: Path) -> BoardDB:
        instance = cls(root)
        instance.db_path.parent.mkdir(parents=True, exist_ok=True)
        return instance

    def migrate(self) -> None:
        run_storage_migrations(self.connect(), scope="board")
