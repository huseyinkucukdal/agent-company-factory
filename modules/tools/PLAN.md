# Module 08 — Tool Layer

## Purpose

Registry, permission control, cost calculation, and invocation layer for tools used by agents. Each tool comes with a cost estimator and a permission check. Role-based whitelist (e.g. the CEO does not access financial tools, the CFO does).

## Responsibility boundaries

**Inside scope:**
- Tool registry: `name`, `schema (pydantic)`, `category`, `cost_estimator`, `permission_check`, `executor`, `roles_allowed`
- Built-in tools (list below)
- Invoke flow: permission → estimate → reserve → execute → commit/release → event
- Converting tool schemas to Claude Agent SDK format
- Integration with approval system for tools that require approval

**Outside scope:**
- The LLM call itself (Agent Runtime)
- External HTTP (delegated to Connector)

## Dependencies

| Module | How |
|---|---|
| Identity | role control, workspace read permission |
| Cost | reserve/commit/release |
| Approvals | when tool's require_approval=True |
| Storage (Workspace) | backend for workspace tools |
| Memory | backend for memory tools |
| Event Store | TOOL_CALLED, TOOL_RESULT, TOOL_DENIED |
| Connector | backend for the external_call tool |

## Public API

```python
class Tools:
    def __init__(self, identity, cost, approvals, workspace,
                 memory, events, connector): ...

    def register(self, tool: ToolDef) -> None: ...

    def for_role(self, role: Role) -> list[ToolDef]: ...

    def claude_sdk_schema(self, role: Role) -> list[dict]:
        """Tool definitions for Claude Agent SDK."""

    async def invoke(self, *, agent_id: str,
                     tool_name: str, args: dict,
                     correlation_id: str | None = None) -> ToolResult: ...

@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: type[BaseModel]                    # pydantic args
    output_schema: type[BaseModel]
    category: ToolCategory
    roles_allowed: set[Role] | Literal["any"]
    requires_approval: ApprovalRequirement     # NONE | EXPENSE | EXTERNAL
    cost_estimator: Callable[[dict], Money] | None
    executor: Callable[..., Awaitable[BaseModel]]
    permission_check: Callable[[Identity, str, dict], None] | None  # raises if denied

class ToolCategory(str, Enum):
    COMMUNICATION = "communication"
    WORKSPACE     = "workspace"
    MEMORY        = "memory"
    GOVERNANCE    = "governance"  # request_approval, escalate, report_to_board
    EXTERNAL      = "external"
    INTERNAL_DATA = "internal_data"

class ApprovalRequirement(str, Enum):
    NONE     = "none"
    EXPENSE  = "expense"
    EXTERNAL = "external_action"

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: BaseModel | None
    error: ToolError | None
    cost_usd: Decimal
```

## Built-in tools (registry)

| name | category | roles | approval |
|---|---|---|---|
| `send_message` | comm | any | NONE |
| `request_approval` | gov | any | NONE |
| `read_my_workspace` | ws | any | NONE |
| `write_my_workspace` | ws | any | NONE |
| `list_my_workspace` | ws | any | NONE |
| `read_subordinate_workspace` | ws | any (manager) | NONE |
| `recall_memory` | mem | any | NONE |
| `remember_fact` | mem | any | NONE |
| `summarize_and_remember` | mem | any | NONE |
| `report_to_board` | gov | HR, Security | NONE |
| `escalate_to_manager` | gov | any | NONE |
| `propose_hire` | gov | HR | NONE |
| `propose_fire` | gov | any | NONE (Identity handles it) |
| `external_call` | ext | (varies) | EXTERNAL |
| `pay_invoice` | ext | CFO | EXPENSE |
| `register_subscription` | ext | CFO | EXPENSE |
| `query_org_chart` | data | any | NONE |
| `list_my_approvals` | gov | any | NONE |
| `list_my_tasks` | data | any | NONE |

## Invoke flow (critical)

```
async def invoke(agent_id, tool_name, args, correlation_id):
    1. tool = registry.get(tool_name) or DENIED("unknown_tool")
    2. agent = identity.get(agent_id)
    3. role check: if agent.role not in tool.roles_allowed: DENIED
    4. tool-specific permission_check (workspace owner check, etc.)
    5. parse args via tool.schema → ValidationError → DENIED
    6. cost estimate (None defaults to Money(0))
    7. reservation = budget.reserve(estimate, ref=correlation_id) or DENIED("over_budget")
    8. if tool.requires_approval != NONE:
         req_id = approvals.request(...)
         if pending: budget.release(reservation); return DENIED("pending_approval", req_id)
         if approved: continue
         if denied: budget.release; return DENIED("approval_denied")
    9. events.append(TOOL_CALLED, {tool, args, agent_id, correlation_id})
   10. try:
         output = await executor(args, ctx)
         actual = compute_actual_cost(output)
         budget.commit(reservation, actual)
         events.append(TOOL_RESULT, {tool, output_summary, cost})
         return ToolResult(ok=True, output, cost=actual)
       except ToolError as e:
         budget.release(reservation)
         events.append(TOOL_DENIED or TOOL_RESULT(error), ...)
         return ToolResult(ok=False, error=e)
```

## Approval-required tool behaviour

When a tool with `requires_approval = EXTERNAL` is called for the first time:
1. An approval is requested and an ID is returned
2. The tool result returns `ok=False, error=PendingApproval(request_id=...)`
3. Agent runtime sees this and puts the agent into "blocked" state
4. When approval arrives, the Orchestrator sends an informational message to the agent (`approval_decided`)
5. The agent calls the tool again (with the same `correlation_id` — idempotent)
6. This time it sees APPROVED in approvals.get, and proceeds

Alternative: block on the first call and auto-invoke on resume. The first version takes the simple path: agent calls again.

## Tool schemas (example)

```python
class SendMessageArgs(BaseModel):
    to_agent_id: str
    content: str
    thread_id: str | None = None

class SendMessageOutput(BaseModel):
    delivered: bool
    message_id: str

send_message = ToolDef(
    name="send_message",
    description="Send a message to another agent in the same company.",
    schema=SendMessageArgs,
    output_schema=SendMessageOutput,
    category=ToolCategory.COMMUNICATION,
    roles_allowed="any",
    requires_approval=ApprovalRequirement.NONE,
    cost_estimator=None,
    permission_check=check_target_in_same_company,
    executor=send_message_executor,
)
```

## Permission check examples

- `read_subordinate_workspace`: target reader, owner; identity.can_read_workspace(reader, owner)
- `report_to_board`: agent role must be HR or Security
- `pay_invoice`: agent role must be CFO
- `external_call`: service must be on the allowlist (checked by Connector)

## Edge cases

| Scenario | Behaviour |
|---|---|
| Unknown tool | TOOL_DENIED + "unknown_tool" |
| Args validation fail | TOOL_DENIED + ValidationError detail |
| Budget exhausted | TOOL_DENIED + "over_budget"; agent is informed |
| Same correlation_id a second time | Idempotent — returns cached result if available; otherwise normal flow |
| Tool executor crash | TOOL_RESULT (error=...) + reservation release |
| Tool result too large (>256KB) | Output is truncated + warning |
| Tool not for agent's role but agent keeps trying | Permanent DENY, log grows — Orchestrator health alarm is triggered |

## Test scenarios

1. `test_register_and_invoke_simple_tool`
2. `test_unknown_tool_denied`
3. `test_role_not_allowed_denied`
4. `test_args_validation_error_denied`
5. `test_over_budget_denied_release_reservation`
6. `test_approval_required_first_call_pending`
7. `test_approval_approved_second_call_succeeds`
8. `test_approval_denied_release_reservation`
9. `test_permission_check_failure_denied`
10. `test_executor_exception_release_reservation`
11. `test_event_store_records_call_and_result`
12. `test_idempotent_correlation_id`
13. `test_actual_cost_overrides_estimate`
14. `test_send_message_to_other_company_denied`
15. `test_read_subordinate_workspace_manager_only`
16. `test_claude_sdk_schema_format`

## Error classes

```python
class ToolError(Exception): ...
class UnknownTool(ToolError): ...
class RoleNotAllowed(ToolError): ...
class ArgsValidationError(ToolError): ...
class OverBudget(ToolError): ...
class PendingApproval(ToolError):
    request_id: str
class ApprovalDenied(ToolError): ...
class ToolPermissionDenied(ToolError): ...
class ExecutorFailure(ToolError): ...
```

## Definition of Done

- [ ] Registry + invoke flow working
- [ ] Built-in tools (list above) registered + each individually tested
- [ ] Approval integration tested (mock approvals)
- [ ] Reservation lifecycle (release on fail) tested
- [ ] Claude SDK schema export working
- [ ] Coverage ≥ 85%

## File skeleton

```
modules/tools/
├── PLAN.md
├── __init__.py
├── registry.py
├── invoke.py
├── builtin/
│   ├── __init__.py
│   ├── communication.py
│   ├── workspace.py
│   ├── memory.py
│   ├── governance.py
│   └── external.py
├── exceptions.py
└── tests/
    ├── test_registry.py
    ├── test_invoke.py
    ├── test_approvals_flow.py
    └── test_builtin_<each>.py
```
