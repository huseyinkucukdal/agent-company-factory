"""Anthropic Messages API provider \u2014 the production target.

Native Claude tool-use protocol; the tool schemas the rest of the system
already produces are in the exact shape Anthropic expects (``name`` /
``description`` / ``input_schema``), so no translation is needed.

Like :class:`modules.llm.github_models.GitHubModelsClient`, this client
synthesises the ``run_turn`` / ``submit_tool_result`` async-generator
contract on top of the request/response Messages API.

Notes
=====

* The vendor SDK package is ``anthropic`` (``pip install anthropic``).
  The "Claude Agent SDK" branding refers to the same family of tools
  (Anthropic SDK + computer-use beta + tool-use loop); we wrap it here
  with the agent-runtime contract our project already uses.
* :attr:`LLMSettings.max_tool_loops` caps the agentic loop so a
  misbehaving model cannot run unbounded.
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
    from anthropic import AsyncAnthropic  # type: ignore[import-not-found,unused-ignore]

_log = logging.getLogger(__name__)


def _import_anthropic() -> Any:
    try:
        import anthropic  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:  # pragma: no cover - depends on extras
        raise LLMProviderUnavailable(
            "anthropic provider requires the 'anthropic' package; "
            "install with: pip install -e '.[llm-anthropic]'"
        ) from exc
    return anthropic


def _to_messages(history: Sequence[WorkingItem]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in history:
        # Anthropic accepts only "user" and "assistant" roles in messages.
        role = "assistant" if item.role == "assistant" else "user"
        out.append({"role": role, "content": item.content})
    return out


class AnthropicClient:
    """Native Claude Messages API client with tool-use loop support."""

    def __init__(self, settings: LLMSettings) -> None:
        if not settings.anthropic_api_key:
            raise LLMConfigError(
                "ANTHROPIC_API_KEY is required for the anthropic provider"
            )
        self._settings = settings
        sdk = _import_anthropic()
        kwargs: dict[str, Any] = {
            "api_key": settings.anthropic_api_key,
            "timeout": settings.timeout_seconds,
        }
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        self._client: AsyncAnthropic = sdk.AsyncAnthropic(**kwargs)
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
        msgs = [*_to_messages(context), {"role": "user", "content": prompt}]
        try:
            resp = await self._client.messages.create(
                model=self._settings.model,
                messages=msgs,
                max_tokens=self._settings.max_tokens,
                temperature=self._settings.temperature,
            )
        except Exception:
            self._fire_callback(ok=False)
            raise
        self._fire_callback(ok=True)
        try:
            blocks = resp.content
            text_chunks = [
                getattr(b, "text", "") for b in blocks
                if getattr(b, "type", None) == "text"
            ]
            return "".join(text_chunks)
        except (AttributeError, IndexError) as exc:
            raise LLMProviderError("malformed completion response") from exc

    # ------------------------------------------------------- internal loop

    async def _run(
        self,
        system: str,
        history: Sequence[WorkingItem],
        tool_schemas: Sequence[Mapping[str, Any]],
    ) -> AsyncIterator[TurnEvent]:
        messages: list[dict[str, Any]] = _to_messages(history)
        tools = list(tool_schemas) or None

        max_loops = self._settings.max_tool_loops
        for _ in range(max_loops):
            try:
                resp = await self._client.messages.create(
                    model=self._settings.model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=self._settings.max_tokens,
                    temperature=self._settings.temperature,
                )
            except Exception as exc:
                self._fire_callback(ok=False)
                raise LLMProviderError(
                    f"anthropic call failed: {exc}"
                ) from exc
            self._fire_callback(ok=True)

            content_blocks = list(resp.content or [])
            tool_uses: list[Any] = [
                b for b in content_blocks if getattr(b, "type", None) == "tool_use"
            ]
            text_blocks: list[Any] = [
                b for b in content_blocks if getattr(b, "type", None) == "text"
            ]

            if not tool_uses:
                for tb in text_blocks:
                    text = getattr(tb, "text", "") or ""
                    if text:
                        yield TurnEvent(type="text", text=text)
                yield TurnEvent(
                    type="stop",
                    stop_reason=getattr(resp, "stop_reason", "") or "",
                )
                return

            # Record the assistant turn verbatim so Anthropic can resolve
            # the tool_use_ids on the next round.
            assistant_blocks: list[dict[str, Any]] = []
            for b in content_blocks:
                btype = getattr(b, "type", None)
                if btype == "text":
                    assistant_blocks.append(
                        {"type": "text", "text": getattr(b, "text", "")}
                    )
                elif btype == "tool_use":
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": b.id,
                            "name": b.name,
                            "input": b.input,
                        }
                    )
            messages.append({"role": "assistant", "content": assistant_blocks})

            futures: list[tuple[str, asyncio.Future[dict[str, Any]]]] = []
            for tu in tool_uses:
                fut: asyncio.Future[dict[str, Any]] = (
                    asyncio.get_running_loop().create_future()
                )
                self._pending[tu.id] = fut
                futures.append((tu.id, fut))
                yield TurnEvent(
                    type="tool_use",
                    tool_use_id=tu.id,
                    tool_name=tu.name,
                    tool_input=dict(tu.input or {}),
                )

            tool_result_blocks: list[dict[str, Any]] = []
            for tcid, fut in futures:
                result = await fut
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tcid,
                        "content": result["content"],
                        "is_error": not result["ok"],
                    }
                )
            messages.append({"role": "user", "content": tool_result_blocks})

        _log.warning("anthropic: tool loop cap (%d) reached", max_loops)
        yield TurnEvent(type="stop", stop_reason="tool_loop_cap")

    def _fire_callback(self, *, ok: bool, rate_limited: bool = False) -> None:
        cb = getattr(self, "_on_request", None)
        if cb is None:
            return
        try:
            cb(
                ok=ok,
                rate_limited=rate_limited,
                provider="anthropic",
                model=self._settings.model,
            )
        except Exception:  # noqa: BLE001 - counter must never break the call
            _log.exception("llm request counter callback failed")


__all__ = ["AnthropicClient"]
