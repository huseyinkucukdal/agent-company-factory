"""Top-level FastAPI dependencies that aren't strictly auth-related."""
from __future__ import annotations

from fastapi import Request

from .runtime import BoardRuntime


def get_runtime(request: Request) -> BoardRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, BoardRuntime):
        raise RuntimeError(
            "BoardRuntime not configured on app.state.runtime — "
            "call build_app(..) instead of constructing FastAPI manually"
        )
    return runtime


__all__ = ["get_runtime"]
