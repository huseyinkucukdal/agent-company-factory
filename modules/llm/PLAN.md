# Module 19 — LLM Providers

## Purpose

Houses the **real** implementations of the `LLMClient` Protocol consumed by Agent Runtime.
Unifies multiple providers (GitHub Models, Anthropic / Claude Agent SDK, mock)
behind a single factory.

## Providers

| ID | Provider | Package | Notes |
|---|---|---|---|
| `mock` | Scriptable test client | — | The official version of `_SilentLLM`. Default. |
| `github_models` | GitHub Models REST API | `openai>=1.40` | OpenAI-compatible endpoint. Requires `GITHUB_TOKEN`. |
| `anthropic` | Anthropic Messages API (Claude Agent SDK) | `anthropic>=0.40` | Requires `ANTHROPIC_API_KEY`. Tool schema is one-to-one. |

## Public API

```python
from modules.llm import (
    LLMSettings,         # pydantic-settings
    LLMProvider,         # StrEnum
    build_llm_factory,   # (settings) -> LLMFactory
    MockLLMClient,       # tests + dev fallback
    GitHubModelsClient,
    AnthropicClient,
)
```

## Contract (same as Agent Runtime)

```python
class LLMClient(Protocol):
    def run_turn(self, *, system, history, tool_schemas, cache_keys=None
        ) -> AsyncIterator[TurnEvent]: ...
    async def submit_tool_result(self, *, tool_use_id, ok, output) -> None: ...
    async def completion(self, prompt, *, context) -> str: ...
```

`run_turn` returns an async-generator; after tool-use events,
`submit_tool_result` is called and the same stream continues absorbing
pending tool results. Providers maintain this state within the instance.

## Tool schema conversion

The Tools module already produces Claude format:
`{"name", "description", "input_schema"}`. For OpenAI calls it is
converted to `{"type": "function", "function": {"name", "description", "parameters"}}`
(only in the `github_models` provider).

## Dependencies

`openai` and `anthropic` packages are **optional** extras in `pyproject.toml`:
- `pip install -e ".[llm-openai]"` → GitHub Models
- `pip install -e ".[llm-anthropic]"` → Claude

The `mock` provider requires no extra packages and is the default for test/dev.

## Definition of Done

- 3 providers working (mock with green tests, GitHub Models / Anthropic with
  unit-mock + opt-in integration script)
- Selectable via `LLM_PROVIDER` env variable through compose
- `_SilentLLM` copies migrated to `MockLLMClient`
- Default `LLMFactory` is `build_llm_factory(settings)` in Factory bootstrap
- README note on "how to add your own key"
