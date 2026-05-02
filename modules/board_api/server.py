"""Development server entry point for the Board API.

This module wires a :class:`CompanyFactory` with a no-op LLM and starts
uvicorn so the Board UI has something to talk to during development. It
is **not** a production entry point — the real LLM provider, secret
store and operational config are wired by Module 17 (Integration).

Usage (inside the Docker container)::

    python -m modules.board_api.server
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import uvicorn

from modules.factory import CompanyFactory, FactoryConfig
from modules.llm import LLMSettings, build_llm_factory
from modules.storage import BoardDB

from . import build_app
from . import migrate as board_api_migrate

_log = logging.getLogger(__name__)


def build_dev_app() -> Any:
    """Construct an app instance using on-disk paths from env vars."""
    data_dir = Path(os.environ.get("ACF_DATA_DIR", "/app/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    board_db = BoardDB.init(data_dir)
    board_db.migrate()
    board_api_migrate(board_db)

    factory = CompanyFactory(
        board_db, data_dir,
        FactoryConfig(llm_factory=build_llm_factory(LLMSettings.from_env())),
    )
    factory.migrate()

    return build_app(db=board_db, factory=factory)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("ACF_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    host = os.environ.get("ACF_API_HOST", "0.0.0.0")
    port = int(os.environ.get("ACF_API_PORT", "8000"))
    _log.info("Starting Board API dev server on %s:%d", host, port)
    uvicorn.run(build_dev_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
