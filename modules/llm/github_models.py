"""GitHub Models provider \u2014 OpenAI-compatible Chat Completions client.

Uses the ``openai`` SDK (lazy-imported so it stays optional). Endpoint and
auth are GitHub-specific:

* ``base_url = https://models.github.ai/inference``
* ``api_key = $GITHUB_TOKEN`` (PAT with the ``models:read`` scope)

Tool-use loop bridge
====================

Agent Runtime expects an async-generator ``run_turn`` that interleaves
``tool_use`` events with ``submit_tool_result`` callbacks. OpenAI's Chat
Completions API is request/response, so we synthesise the streaming
contract:

1. Call the API.
2. If the assistant returned ``tool_calls``, register a future per call
   under :attr:`_pending` and ``yield`` a :class:`TurnEvent` for each.
3. Resume only after all those futures are resolved (by
   :meth:`submit_tool_result`), append the tool messages, and loop.
4. Otherwise yield the text and a final ``stop``.

This keeps the rest of the runtime untouched.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from modules.agent_runtime import TurnEvent
from modules.memory import WorkingItem

from .exceptions import (
    LLMConfigError,
    LLMProviderError,
    LLMProviderUnavailable,
)
from .settings import LLMSettings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import AsyncOpenAI  # type: ignore[import-not-found,unused-ignore]

_log = logging.getLogger(__name__)


def _import_openai() -> Any:
    try:
        import openai  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise LLMProviderUnavailable(
            "github_models provider requires the 'openai' package; "
            "install with: pip install -e '.[llm-openai]'"
        ) from exc
    return openai


def _to_openai_tools(
    schemas: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Claude-shaped tool schemas to OpenAI ``tools=[...]``."""
    out: list[dict[str, Any]] = []
    for s in schemas:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "parameters": s.get("input_schema", {"type": "object"}),
                },
            }
        )
    return out


def _to_openai_history(history: Sequence[WorkingItem]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in history:
        role = item.role if item.role in ("user", "assistant", "system") else "user"
        out.append({"role": role, "content": item.content})
    return out


def _retry_after_seconds(exc: Exception) -> float | None:
    """Best-effort Retry-After parser for openai SDK errors."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


class GitHubModelsClient:
    """OpenAI-compatible client wired to the GitHub Models endpoint."""

    def __init__(self, settings: LLMSettings) -> None:
        if not settings.github_token:
            raise LLMConfigError(
                "GITHUB_TOKEN is required for the github_models provider"
            )
        self._settings = settings
        openai_pkg = _import_openai()
        self._client: AsyncOpenAI = openai_pkg.AsyncOpenAI(
            api_key=settings.github_token,
            base_url=settings.github_models_base_url,
            timeout=settings.timeout_seconds,
        )
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._on_request: Callable[..., None] | None = None

    # ----------------------------------------------------------- public API

    def run_turn(
        self,
        *,
        system: str,
        history: list[WorkingItem],
        tool_schemas: list[dict[str, Any]],
        cache_keys: Sequence[str] | None = None,
    ) -> AsyncIterator[TurnEvent]:
        return self._run(system, history, tool_schemas)

    async def submit_tool_result(
        self,
        *,
        tool_use_id: str,
        ok: bool,
        output: Mapping[str, Any] | str,
    ) -> None:
        fut = self._pending.pop(tool_use_id, None)
        if fut is None or fut.done():
            return
        if isinstance(output, str):
            content = output
        else:
            try:
                content = json.dumps(output, default=str)
            except (TypeError, ValueError):
                content = str(output)
        fut.set_result({"ok": ok, "content": content})

    async def completion(
        self, prompt: str, *, context: list[WorkingItem]
    ) -> str:
        messages = [
            *_to_openai_history(context),
            {"role": "user", "content": prompt},
        ]
        try:
            resp = await self._client.chat.completions.create(
                model=self._settings.model,
                messages=messages,
                max_tokens=self._settings.max_tokens,
                temperature=self._settings.temperature,
            )
        except Exception:
            self._fire_callback(ok=False, rate_limited=False)
            raise
        self._fire_callback(ok=True, rate_limited=False)
        try:
            return resp.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise LLMProviderError("malformed completion response") from exc

    # ------------------------------------------------------- internal loop

    async def _chat_with_backoff(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Any:
        """Call chat.completions with backoff on 429 / transient errors.

        The ``openai`` SDK already retries internally a couple of times,
        but on a tight free-tier rate limit those retries fire within a
        couple of seconds and still 429. We add an outer loop with longer
        sleeps (and honour ``Retry-After`` when present) so the agent
        doesn't fail the whole turn on a transient throttle.
        """
        openai_pkg = _import_openai()
        rate_limit_cls = getattr(openai_pkg, "RateLimitError", None)
        api_status_cls = getattr(openai_pkg, "APIStatusError", None)

        delays = self._backoff_delays()
        last_exc: Exception | None = None
        rate_limited_seen = False
        for attempt, delay in enumerate([0.0, *delays]):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=messages,
                    tools=tools,
                    max_tokens=self._settings.max_tokens,
                    temperature=self._settings.temperature,
                )
            except Exception as exc:  # noqa: BLE001 - we re-raise below
                last_exc = exc
                is_429 = (
                    rate_limit_cls is not None and isinstance(exc, rate_limit_cls)
                ) or (
                    api_status_cls is not None
                    and isinstance(exc, api_status_cls)
                    and getattr(exc, "status_code", None) == 429
                )
                if not is_429:
                    _log.warning(
                        "%s call failed (attempt %d): %s",
                        self._provider_name(), attempt + 1, exc,
                    )
                    self._fire_callback(ok=False, rate_limited=False)
                    raise LLMProviderError(
                        f"{self._provider_name()} call failed: {exc}"
                    ) from exc
                rate_limited_seen = True
                # Honour Retry-After header if the SDK exposed one.
                retry_after = _retry_after_seconds(exc)
                if retry_after is not None and attempt < len(delays):
                    delays[attempt] = max(delays[attempt], retry_after)
                _log.warning(
                    "%s 429 rate limit (attempt %d/%d), backing off",
                    self._provider_name(), attempt + 1, len(delays) + 1,
                )
                continue
            else:
                self._fire_callback(ok=True, rate_limited=rate_limited_seen)
                return resp
        _log.error("%s exhausted retries: %s", self._provider_name(), last_exc)
        self._fire_callback(ok=False, rate_limited=True)
        raise LLMProviderError(
            f"{self._provider_name()} rate limited after retries: {last_exc}"
        ) from last_exc

    def _backoff_delays(self) -> list[float]:
        """Override hook for subclasses; defaults to GitHub's tighter limits."""
        return [2.0, 8.0, 20.0]

    def _provider_name(self) -> str:
        return "github_models"

    def _fire_callback(self, *, ok: bool, rate_limited: bool) -> None:
        cb = getattr(self, "_on_request", None)
        if cb is None:
            return
        try:
            cb(
                ok=ok,
                rate_limited=rate_limited,
                provider=self._provider_name(),
                model=self._settings.model,
            )
        except Exception:  # noqa: BLE001 - counter must never break the call
            _log.exception("llm request counter callback failed")

    async def _run(
        self,
        system: str,
        history: Sequence[WorkingItem],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[TurnEvent]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(_to_openai_history(history))
        tools = _to_openai_tools(tool_schemas) or None

        max_loops = self._settings.max_tool_loops
        for _ in range(max_loops):
            resp = await self._chat_with_backoff(messages, tools)

            choice = resp.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                text = getattr(msg, "content", None) or ""
                if text:
                    yield TurnEvent(type="text", text=text)
                yield TurnEvent(type="stop", stop_reason=choice.finish_reason or "")
                return

            # Record the assistant turn so tool messages have an anchor.
            messages.append(
                {
                    "role": "assistant",
                    "content": getattr(msg, "content", None) or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            futures: list[tuple[str, asyncio.Future[dict[str, Any]]]] = []
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                fut: asyncio.Future[dict[str, Any]] = (
                    asyncio.get_running_loop().create_future()
                )
                self._pending[tc.id] = fut
                futures.append((tc.id, fut))
                yield TurnEvent(
                    type="tool_use",
                    tool_use_id=tc.id,
                    tool_name=tc.function.name,
                    tool_input=args,
                )

            for tcid, fut in futures:
                result = await fut
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tcid,
                        "content": result["content"],
                    }
                )

        _log.warning("github_models: tool loop cap (%d) reached", max_loops)
        yield TurnEvent(type="stop", stop_reason="tool_loop_cap")


__all__ = ["GitHubModelsClient"]
