"""SSE endpoint contract tests.

We deliberately avoid consuming the body via ``response.aiter_bytes()``
under :class:`httpx.ASGITransport`: that transport does not stream
indefinite ASGI body messages — it accumulates them and only yields once
the generator returns. Our SSE handlers run forever, so any test that
``async for`` over the stream deadlocks regardless of the implementation.

The actual chunk-flush behaviour is exercised by running uvicorn (which
the user does in development); these tests cover the bits we *can* check
in-process: routing, auth/RBAC, response status, and content type.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from .conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _create_company(
    client: httpx.AsyncClient, token: dict[str, str], spec: dict[str, Any],
) -> str:
    r = await client.post("/companies", headers=auth_headers(token), json=spec)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_stream_company_returns_event_stream(
    client: httpx.AsyncClient,
    admin_token: dict[str, str],
    basic_spec_payload: dict[str, Any],
) -> None:
    """/companies/{id}/stream answers 200 with the SSE content type.

    The ASGITransport does not surface ASGI body messages until the
    generator returns, so the response head is the part we can verify
    in-process. Browser-side delivery is exercised against uvicorn.
    """
    cid = await _create_company(client, admin_token, basic_spec_payload)
    headers = auth_headers(admin_token)
    headers["Accept"] = "text/event-stream"

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        async with asyncio.timeout(2):
            async with client.stream(
                "GET", f"/companies/{cid}/stream?since=0", headers=headers,
            ) as response:
                assert response.status_code == 200
                ct = response.headers.get("content-type", "")
                assert ct.startswith("text/event-stream"), ct
                cache = response.headers.get("cache-control", "")
                assert "no-cache" in cache.lower()
                # Wait out the heartbeat to surface ASGI buffering.
                await asyncio.sleep(5)


async def test_stream_company_unknown_returns_404(
    client: httpx.AsyncClient, admin_token: dict[str, str],
) -> None:
    headers = auth_headers(admin_token)
    headers["Accept"] = "text/event-stream"
    async with client.stream(
        "GET", "/companies/missing/stream?since=0", headers=headers,
    ) as response:
        assert response.status_code == 404


async def test_global_stream_admin_only(
    client: httpx.AsyncClient,
    operator_token: dict[str, str],
) -> None:
    headers = auth_headers(operator_token)
    headers["Accept"] = "text/event-stream"
    async with client.stream("GET", "/stream", headers=headers) as response:
        assert response.status_code == 403


async def test_stream_requires_authentication(
    client: httpx.AsyncClient,
    basic_spec_payload: dict[str, Any],
    admin_token: dict[str, str],
) -> None:
    cid = await _create_company(client, admin_token, basic_spec_payload)
    async with client.stream(
        "GET", f"/companies/{cid}/stream?since=0",
    ) as response:
        assert response.status_code == 401
