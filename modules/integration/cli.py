"""``acf`` command-line interface for Module 17.

Implemented with :mod:`argparse` to avoid pulling another runtime
dependency. Sub-commands map 1:1 to the lifecycle and inspection
operations described in ``modules/integration/PLAN.md``.

Examples (inside the Docker container)::

    python -m modules.integration init
    python -m modules.integration db migrate
    python -m modules.integration company list
    python -m modules.integration company create --spec /tmp/acme.json
    python -m modules.integration approvals pending
    python -m modules.integration events tail <company_id> --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from modules.factory import CompanySpec, CompanyStatus, ExtraAgentSpec
from modules.identity import Role
from modules.llm import LLMSettings, build_llm_factory
from modules.storage import BoardDB

from . import bootstrap_runtime, migrate_all

_log = logging.getLogger(__name__)


# --------------------------------------------------- LLM stand-in


# Production-ready LLM dispatcher. With LLM_PROVIDER=mock (or unset) and no
# keys this still constructs MockLLMClient instances per agent, matching
# the previous _SilentLLM behaviour. Set LLM_PROVIDER=github_models or
# LLM_PROVIDER=anthropic in the environment to use a real provider.
_silent_llm_factory = build_llm_factory(LLMSettings.from_env())


# --------------------------------------------------- helpers


def _data_dir(args: argparse.Namespace) -> Path:
    return Path(
        getattr(args, "data_dir", None)
        or os.environ.get("ACF_DATA_DIR", "/app/data"),
    )


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, default=str, indent=2, sort_keys=True))


def _spec_from_json(payload: dict[str, Any]) -> CompanySpec:
    extra = tuple(
        ExtraAgentSpec(
            role_title=ex["role_title"],
            first_name=ex.get("first_name", ""),
            last_name=ex.get("last_name", ""),
            role_description=ex.get("role_description", ""),
            persona_ref=ex.get("persona_ref", "default"),
            reports_to_role=Role(ex["reports_to_role"]),
        )
        for ex in payload.get("extra_agents", [])
    )
    return CompanySpec(
        name=payload["name"],
        mission=payload["mission"],
        industry=payload.get("industry", "general"),
        initial_budget_usd=Decimal(str(payload.get("initial_budget_usd", 1000))),
        company_disk_quota_mb=int(payload.get("company_disk_quota_mb", 64)),
        default_agent_quota_mb=int(payload.get("default_agent_quota_mb", 8)),
        auto_approve_threshold_usd=Decimal(
            str(payload.get("auto_approve_threshold_usd", 0)),
        ),
        extra_agents=extra,
        company_id=payload.get("company_id"),
    )


# --------------------------------------------------- command handlers


def cmd_init(args: argparse.Namespace) -> int:
    data = _data_dir(args)
    data.mkdir(parents=True, exist_ok=True)
    board_db = BoardDB.init(data)
    migrate_all(board_db=board_db)
    print(f"initialised data dir: {data}")
    print(f"board db: {data / 'board.db'}")
    return 0


def cmd_db_migrate(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    print("migrations applied")
    runtime.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    host = args.host or os.environ.get("ACF_API_HOST", "0.0.0.0")
    port = int(args.port or os.environ.get("ACF_API_PORT", "8000"))
    _log.info("acf serve: %s:%d", host, port)
    uvicorn.run(runtime.app, host=host, port=port, log_level="info")
    return 0


def cmd_company_list(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    summaries = runtime.factory.list_active()
    rows = [
        {
            "id": s.company_id,
            "name": s.name,
            "status": s.status.value,
            "created_at": s.created_at.isoformat(),
        }
        for s in summaries
    ]
    _print_json(rows)
    runtime.close()
    return 0


def cmd_company_create(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    spec = _spec_from_json(payload)
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )

    async def _run() -> str:
        handle = await runtime.factory.create_company(
            spec, requested_by=args.requested_by,
        )
        return handle.company_id

    company_id = asyncio.run(_run())
    _print_json({"company_id": company_id, "status": "active"})
    runtime.close()
    return 0


def cmd_company_close(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )

    async def _run() -> None:
        # Restart only the requested company so we can close it.
        await runtime.factory.restart_company(args.company_id)
        await runtime.factory.close_company(
            args.company_id, requested_by=args.requested_by,
        )

    asyncio.run(_run())
    _print_json({"company_id": args.company_id, "status": "closed"})
    runtime.close()
    return 0


def cmd_company_describe(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    summary = runtime.factory.get_summary(args.company_id)
    out = {
        "id": summary.company_id,
        "name": summary.name,
        "status": summary.status.value,
        "created_at": summary.created_at.isoformat(),
        "closed_at": summary.closed_at.isoformat() if summary.closed_at else None,
    }
    _print_json(out)
    runtime.close()
    return 0


def cmd_approvals_pending(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    out: list[dict[str, Any]] = []
    for s in runtime.factory.list_active():
        if s.status is not CompanyStatus.ACTIVE:
            continue
        try:
            handle = runtime.factory.get_handle(s.company_id)
        except Exception:
            continue
        # Pending approvals across all decider routes — query DB directly
        # to avoid coupling to the route enum.
        rows = handle.db.connect().execute(
            "SELECT id, kind, subject, requested_by, created_at "
            "FROM approvals WHERE status = 'pending' "
            "ORDER BY created_at",
        ).fetchall()
        for r in rows:
            out.append({
                "company_id": s.company_id,
                "id": r["id"],
                "kind": r["kind"],
                "subject": r["subject"],
                "requested_by": r["requested_by"],
                "created_at": r["created_at"],
            })
    _print_json(out)
    runtime.close()
    return 0


def cmd_events_tail(args: argparse.Namespace) -> int:
    runtime = bootstrap_runtime(
        data_dir=_data_dir(args), llm_factory=_silent_llm_factory,
    )
    handle = runtime.factory.get_handle(args.company_id)
    events = handle.events.read(limit=args.limit)
    rows = [
        {
            "id": e.id,
            "kind": e.kind.value,
            "actor": e.actor_agent_id,
            "ts_company": e.ts_company.isoformat(),
            "ts_real": e.ts_real.isoformat(),
            "payload": e.payload,
        }
        for e in events[-args.limit:]
    ]
    _print_json(rows)
    runtime.close()
    return 0


# --------------------------------------------------- argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acf")
    p.add_argument("--data-dir", help="override ACF_DATA_DIR")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="prepare data dir + run migrations")\
        .set_defaults(func=cmd_init)

    db = sub.add_parser("db", help="database utilities")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("migrate", help="apply all module migrations")\
        .set_defaults(func=cmd_db_migrate)

    serve = sub.add_parser("serve", help="run the Board API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", default=None)
    serve.set_defaults(func=cmd_serve)

    company = sub.add_parser("company", help="company lifecycle")
    csub = company.add_subparsers(dest="company_cmd", required=True)
    csub.add_parser("list").set_defaults(func=cmd_company_list)
    create = csub.add_parser("create")
    create.add_argument("--spec", required=True, help="JSON spec file")
    create.add_argument("--requested-by", default="cli")
    create.set_defaults(func=cmd_company_create)
    close = csub.add_parser("close")
    close.add_argument("company_id")
    close.add_argument("--requested-by", default="cli")
    close.set_defaults(func=cmd_company_close)
    desc = csub.add_parser("describe")
    desc.add_argument("company_id")
    desc.set_defaults(func=cmd_company_describe)

    approvals = sub.add_parser("approvals", help="approvals queue")
    asub = approvals.add_subparsers(dest="approvals_cmd", required=True)
    asub.add_parser("pending").set_defaults(func=cmd_approvals_pending)

    events = sub.add_parser("events", help="event log")
    esub = events.add_subparsers(dest="events_cmd", required=True)
    tail = esub.add_parser("tail")
    tail.add_argument("company_id")
    tail.add_argument("--limit", type=int, default=20)
    tail.set_defaults(func=cmd_events_tail)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("ACF_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
