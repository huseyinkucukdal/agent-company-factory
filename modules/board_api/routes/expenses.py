"""``/companies/{id}/expenses``: history listing."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query

from modules.factory import CompanyNotFound

from ..auth.deps import AuthenticatedUser, require_user
from ..deps import get_runtime
from ..runtime import BoardRuntime
from ..schemas import ExpenseResponse
from ._helpers import ensure_company_access, not_found


def build_expenses_router() -> APIRouter:
    router = APIRouter(
        prefix="/companies/{company_id}", tags=["expenses"],
    )

    @router.get("/expenses", response_model=list[ExpenseResponse])
    async def list_expenses(
        company_id: str,
        category: str | None = Query(default=None),
        agent_id: str | None = Query(default=None),
        limit: int = Query(default=200, gt=0, le=2000),
        runtime: BoardRuntime = Depends(get_runtime),
        current: AuthenticatedUser = Depends(require_user),
    ) -> list[ExpenseResponse]:
        ensure_company_access(runtime, current, company_id)
        try:
            handle = runtime.factory.get_handle(company_id)
        except CompanyNotFound as exc:
            raise not_found(str(exc), code="company_not_found") from exc

        clauses: list[str] = []
        params: list[Any] = []
        if category is not None:
            clauses.append("category = ?")
            params.append(category)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, agent_id, amount_units, category, memo, approval_id, "
            "       ts_company, ts_real "
            f"FROM expenses{where} ORDER BY ts_real DESC LIMIT ?"
        )
        params.append(limit)
        rows = handle.db.connect().execute(sql, params).fetchall()
        out: list[ExpenseResponse] = []
        for r in rows:
            from decimal import Decimal
            amount_usd = (Decimal(int(r["amount_units"])) / Decimal(10_000)).quantize(
                Decimal("0.0001")
            )
            out.append(
                ExpenseResponse(
                    id=r["id"],
                    agent_id=r["agent_id"],
                    amount_usd=str(amount_usd),
                    category=r["category"],
                    memo=r["memo"],
                    approval_id=r["approval_id"],
                    ts_company=_parse(r["ts_company"]),
                    ts_real=_parse(r["ts_real"]),
                )
            )
        return out

    return router


def _parse(s: str) -> Any:
    from datetime import datetime
    return datetime.fromisoformat(s)


__all__ = ["build_expenses_router"]
