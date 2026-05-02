"""Runtime container assembled by ``build_app``.

Holds everything the routes need to reach: the auth runtime, the factory,
the audit log, the link service, and the board DB. Stashed on
``app.state.runtime`` for ``Depends(get_runtime)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from modules.factory import CompanyFactory
from modules.storage import BoardDB

from .audit import AuditLog
from .auth.deps import AuthRuntime
from .links import LinkService

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from modules.inter_company import InterCompanyService


@dataclass
class BoardRuntime:
    db: BoardDB
    factory: CompanyFactory
    auth: AuthRuntime
    audit: AuditLog
    links: LinkService
    inter_company: "InterCompanyService | None" = None


__all__ = ["BoardRuntime"]
