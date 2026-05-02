"""Module 17 — Integration & E2E.

Stitches every module together into one runnable application:

* :func:`migrate_all` — run every module's migrations against the board
  DB in dependency order.
* :func:`bootstrap_runtime` — assemble :class:`BoardDB`,
  :class:`CompanyFactory`, :class:`InterCompanyService` and the FastAPI
  app, with the inter-company connector service auto-registered for
  every company on creation/restart.
* :func:`build_post_create_hook` — small helper used by
  ``bootstrap_runtime``; exposed so tests and CLIs can mix-and-match
  the wiring.

The Typer-based ``acf`` CLI lives in :mod:`modules.integration.cli`.
"""
from __future__ import annotations

from .bootstrap import (
    IntegratedRuntime,
    bootstrap_runtime,
    build_post_create_hook,
    register_inter_company_for_handle,
)
from .migrations import migrate_all

__all__ = [
    "IntegratedRuntime",
    "bootstrap_runtime",
    "build_post_create_hook",
    "migrate_all",
    "register_inter_company_for_handle",
]
